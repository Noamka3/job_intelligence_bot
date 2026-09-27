"""Company sync orchestration: company rows (from the Google Sheet or a
local file) -> Company + CareerSource rows.

Rules this must honor:
- a failed read aborts before touching the DB (never delete/disable
  companies because of a transient read failure)
- upsert by normalized name, don't duplicate
- a company that disappears from a successfully-read source is disabled,
  never deleted (historical jobs/matches must stay intact)

Two entrypoints share the same apply logic (sync_companies): the live
Google Sheets sync (sync_companies_from_sheet) and a local .xlsx import
(sync_companies_from_excel) - useful before a Google service account is
set up, or for a one-off import. Both produce identical Company/
CareerSource rows from the same CompanySheetRow shape.
"""

from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.ingestion.resolver import ResolvedSource, normalize_source_url, resolve_career_source
from app.models.career_source import CareerSource
from app.models.company import Company
from app.models.enums import CareerSourceType, JobStatus
from app.models.job_posting import JobPosting
from app.schemas.sync import SheetSyncResult
from app.services.sheets.excel_reader import read_company_rows_from_excel
from app.services.sheets.reader import CompanySheetRow, read_company_rows

logger = logging.getLogger(__name__)

_PROBE_CONCURRENCY = 10

# API-backed sources are cheap to poll often; anything
# requiring real page fetches (or, later, a browser) backs off. Measured
# on real crawling: the 44 API sources take 1-6 seconds each. A plain
# career site costs one listing download per crawl now that a job page
# is fetched only when its link is new or its stored copy is a day old
# (JOB_DETAILS_REFRESH_HOURS) - a few seconds each, so at 10 minutes the
# 164 of them keep two worker processes well below capacity on this
# laptop; a bigger machine can take them to 5. An interval below the
# dispatcher's 5-minute tick means "due at the next tick", which is how
# the API sources get a real ~5 minutes (at 5 they landed on every other
# tick, i.e. 10). Applied when a CareerSource is created or changes
# type; `python -m app.cli apply-poll-intervals` re-applies them.
DEFAULT_POLL_MINUTES: dict[CareerSourceType, int] = {
    CareerSourceType.GREENHOUSE: 3,
    CareerSourceType.LEVER: 3,
    CareerSourceType.ASHBY: 3,
    CareerSourceType.SMARTRECRUITERS: 3,
    CareerSourceType.WORKABLE: 3,
    CareerSourceType.COMEET: 3,
    CareerSourceType.WORKDAY: 10,
    CareerSourceType.TALEO: 10,
    CareerSourceType.SITE_FEED: 3,
    CareerSourceType.WORDPRESS: 3,
    CareerSourceType.JSONLD: 15,
    CareerSourceType.GENERIC_HTML: 10,
    CareerSourceType.PLAYWRIGHT: 60,
}


def normalize_company_name(name: str) -> str:
    return " ".join(name.strip().lower().split())


def sync_companies_from_sheet(db: Session) -> SheetSyncResult:
    rows = read_company_rows()  # raises on failure - nothing below runs if the read fails
    return sync_companies(db, rows)


def sync_companies_from_excel(db: Session, file_path: str | Path) -> SheetSyncResult:
    rows = read_company_rows_from_excel(file_path)  # raises on failure, same guarantee
    return sync_companies(db, rows)


def resync_companies_from_stored_urls(db: Session) -> SheetSyncResult:
    """Re-runs resolution for every enabled company from the sheet URL it
    was imported with, without reading the sheet again - the way to pick
    up resolver improvements (a newly registered adapter, embedded-board
    detection) on rows that are already in the database. Same apply
    logic as a real sync, so a source that now resolves differently is
    updated in place, never duplicated.
    """
    companies = db.execute(select(Company).where(Company.enabled.is_(True))).scalars()
    rows = [
        CompanySheetRow(name=company.name, url=company.original_sheet_url) for company in companies
    ]
    return sync_companies(db, rows)


def sync_companies(db: Session, rows: list[CompanySheetRow]) -> SheetSyncResult:
    existing_by_name: dict[str, Company] = {
        company.normalized_name: company for company in db.execute(select(Company)).scalars()
    }

    resolved_sources = _resolve_all(rows)

    seen_normalized_names: set[str] = set()
    created = 0
    updated = 0
    sources_created = 0

    for row, resolved in zip(rows, resolved_sources, strict=True):
        normalized_name = normalize_company_name(row.name)
        if not normalized_name:
            continue
        seen_normalized_names.add(normalized_name)

        company = existing_by_name.get(normalized_name)
        if company is None:
            company = Company(
                name=row.name,
                normalized_name=normalized_name,
                original_sheet_url=row.url,
                enabled=True,
            )
            db.add(company)
            db.flush()
            existing_by_name[normalized_name] = company
            created += 1
        else:
            if _apply_company_updates(company, row):
                updated += 1

        sources_created += _ensure_career_source(
            db, company, normalize_source_url(row.url), resolved
        )

    disabled = _disable_missing_companies(db, existing_by_name, seen_normalized_names)

    db.commit()

    result = SheetSyncResult(
        companies_seen=len(seen_normalized_names),
        companies_created=created,
        companies_updated=updated,
        companies_disabled=disabled,
        sources_created=sources_created,
    )
    logger.info("company sheet sync complete", extra=result.model_dump())
    return result


def _resolve_all(rows: list[CompanySheetRow]) -> list[ResolvedSource]:
    """Resolve every row's CareerSourceType. Most resolve instantly from
    the URL's hostname; only unrecognized hosts trigger a real HTTP probe
    (see resolve_career_source), so a small thread pool is enough to keep
    a first-time sync of ~200+ companies from taking many minutes.
    """
    with ThreadPoolExecutor(max_workers=_PROBE_CONCURRENCY) as executor:
        return list(executor.map(lambda row: resolve_career_source(row.url), rows))


def _apply_company_updates(company: Company, row: CompanySheetRow) -> bool:
    changed = False
    if company.name != row.name:
        company.name = row.name
        changed = True
    if company.original_sheet_url != row.url:
        company.original_sheet_url = row.url
        changed = True
    if not company.enabled:
        company.enabled = True
        changed = True
    return changed


def _ensure_career_source(
    db: Session, company: Company, url: str | None, resolved: ResolvedSource
) -> int:
    """Makes the company's CareerSource match what its sheet row resolves
    to now, and returns 1 if that meant creating a new row.

    Lookup order: (source_type, external_identifier) for a known ATS
    board - the same board shows up under several URL variants across
    sheet edits (query params, tracking suffixes; see docs/job_sources.md)
    and matching on the URL would duplicate it and, later, its jobs -
    then the exact URL. An existing row is updated in place rather than
    duplicated when it resolves differently now (a probe that timed out on
    the first sync left it generic_html; a JSON-LD page later moved to a
    real ATS), and any other still-enabled source of the company is
    retired, so one sheet row never keeps two sources crawling.
    """
    if not url:
        return 0
    # A company page that embeds a known board resolves to that board's
    # canonical URL - that's what gets crawled and stored.
    crawl_url = resolved.board_url or url

    existing: CareerSource | None = None
    if resolved.external_identifier is not None:
        existing = db.execute(
            select(CareerSource).where(
                CareerSource.company_id == company.id,
                CareerSource.source_type == resolved.source_type,
                CareerSource.external_identifier == resolved.external_identifier,
            )
        ).scalar_one_or_none()
    if existing is None and resolved.source_type == CareerSourceType.GENERIC_HTML:
        # A failed probe (timeout, refusal) resolves to generic_html. A board
        # found behind this page on an earlier sync is stored under the
        # board's own URL, so it would not be found below and would be
        # retired for a generic copy of the page - keep it instead.
        existing = _enabled_board_of(db, company)
    if existing is None:
        # Either the URL as stored previously (before a resolver
        # improvement recognized the embedded board) or the board itself.
        existing = db.execute(
            select(CareerSource)
            .where(
                CareerSource.company_id == company.id,
                CareerSource.source_url.in_({crawl_url, url}),
            )
            .order_by(CareerSource.id)
            .limit(1)
        ).scalar_one_or_none()

    created = 0
    if existing is None:
        existing = CareerSource(
            company_id=company.id,
            source_type=resolved.source_type,
            source_url=crawl_url,
            external_identifier=resolved.external_identifier,
            unsupported_reason=resolved.unsupported_reason,
            enabled=resolved.source_type != CareerSourceType.UNSUPPORTED,
            poll_interval_minutes=default_poll_minutes(resolved.source_type),
        )
        db.add(existing)
        created = 1
    else:
        _reconcile_existing_source(existing, crawl_url, resolved)

    # Flushed so the next row of this same sync can see it: the session
    # is autoflush=False, and two rows for one board would otherwise both
    # miss the lookup above and collide on uq_career_sources_company_url
    # at commit, rolling back the whole sync.
    db.flush()

    others = db.execute(
        select(CareerSource).where(
            CareerSource.company_id == company.id,
            CareerSource.id != existing.id,
            CareerSource.enabled.is_(True),
        )
    ).scalars()
    for other in others:
        _retire_source(db, other)
    return created


def _enabled_board_of(db: Session, company: Company) -> CareerSource | None:
    """The company's crawled source, if it is a real board (not the
    generic fallback and not unsupported)."""
    return db.execute(
        select(CareerSource)
        .where(
            CareerSource.company_id == company.id,
            CareerSource.enabled.is_(True),
            CareerSource.source_type.notin_(
                [CareerSourceType.GENERIC_HTML, CareerSourceType.UNSUPPORTED]
            ),
        )
        .order_by(CareerSource.id)
        .limit(1)
    ).scalar_one_or_none()


def _reconcile_existing_source(existing: CareerSource, url: str, resolved: ResolvedSource) -> None:
    changed_type = existing.source_type != resolved.source_type
    # A failed probe resolves to generic_html; never let that *downgrade*
    # a source we already classified better on an earlier, successful
    # sync - only a hostname-resolved, embedded-board or JSON-LD result
    # can change it.
    downgrade_to_fallback = (
        resolved.source_type == CareerSourceType.GENERIC_HTML
        and existing.source_type != CareerSourceType.UNSUPPORTED
    )
    if downgrade_to_fallback:
        return
    changed_identifier = (
        resolved.external_identifier is not None
        and existing.external_identifier != resolved.external_identifier
    )
    if changed_type or changed_identifier:
        logger.info(
            "career source re-resolved",
            extra={
                "source_id": existing.id,
                "from": f"{existing.source_type.value}:{existing.external_identifier}",
                "to": f"{resolved.source_type.value}:{resolved.external_identifier}",
            },
        )
        existing.source_type = resolved.source_type
        existing.external_identifier = resolved.external_identifier
        existing.unsupported_reason = resolved.unsupported_reason
        existing.poll_interval_minutes = default_poll_minutes(resolved.source_type)
        existing.consecutive_failures = 0
        existing.next_check_at = None
    existing.source_url = url
    supported = existing.source_type != CareerSourceType.UNSUPPORTED
    if supported and not existing.enabled:
        # Retired earlier (company dropped from the sheet, or its row
        # pointed elsewhere for a while) and back now.
        existing.enabled = True
        existing.next_check_at = None


def default_poll_minutes(source_type: CareerSourceType) -> int:
    return DEFAULT_POLL_MINUTES.get(source_type, get_settings().default_poll_minutes)


def _retire_source(db: Session, source: CareerSource) -> None:
    """A source that is no longer polled can't vouch for its jobs being
    open, so they're closed (never deleted - history stays intact, and
    they reopen normally if the source comes back and lists them again).
    """
    source.enabled = False
    db.execute(
        update(JobPosting)
        .where(JobPosting.career_source_id == source.id, JobPosting.status == JobStatus.ACTIVE)
        .values(status=JobStatus.CLOSED)
    )


def _disable_missing_companies(
    db: Session, existing_by_name: dict[str, Company], seen_normalized_names: set[str]
) -> int:
    disabled = 0
    for normalized_name, company in existing_by_name.items():
        if normalized_name not in seen_normalized_names and company.enabled:
            company.enabled = False
            for source in company.career_sources:
                if source.enabled:
                    _retire_source(db, source)
            disabled += 1
    return disabled

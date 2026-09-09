"""Company sync orchestration: company rows (from the Google Sheet or a
local file) -> Company + CareerSource rows.

Spec §38 rules this must honor:
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

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.ingestion.resolver import ResolvedSource, resolve_career_source
from app.models.career_source import CareerSource
from app.models.company import Company
from app.models.enums import CareerSourceType
from app.schemas.sync import SheetSyncResult
from app.services.sheets.excel_reader import read_company_rows_from_excel
from app.services.sheets.reader import CompanySheetRow, read_company_rows

logger = logging.getLogger(__name__)

_PROBE_CONCURRENCY = 10

# Reasonable defaults per spec §19: API-backed sources are cheap to poll
# often; anything requiring real page fetches (or, later, a browser) backs
# off. Applied once when a CareerSource is first created.
_DEFAULT_POLL_MINUTES: dict[CareerSourceType, int] = {
    CareerSourceType.GREENHOUSE: 5,
    CareerSourceType.LEVER: 5,
    CareerSourceType.ASHBY: 5,
    CareerSourceType.SMARTRECRUITERS: 5,
    CareerSourceType.WORKABLE: 5,
    CareerSourceType.COMEET: 5,
    CareerSourceType.WORKDAY: 10,
    CareerSourceType.TALEO: 10,
    CareerSourceType.JSONLD: 15,
    CareerSourceType.GENERIC_HTML: 15,
    CareerSourceType.PLAYWRIGHT: 30,
}


def normalize_company_name(name: str) -> str:
    return " ".join(name.strip().lower().split())


def sync_companies_from_sheet(db: Session) -> SheetSyncResult:
    rows = read_company_rows()  # raises on failure - nothing below runs if the read fails
    return sync_companies(db, rows)


def sync_companies_from_excel(db: Session, file_path: str | Path) -> SheetSyncResult:
    rows = read_company_rows_from_excel(file_path)  # raises on failure, same guarantee
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

        sources_created += _ensure_career_source(db, company, row.url, resolved)

    disabled = _disable_missing_companies(existing_by_name, seen_normalized_names)

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
    if not url:
        return 0

    query = select(CareerSource).where(CareerSource.company_id == company.id)
    if resolved.external_identifier is not None:
        # The real unique key for a known ATS board is (source_type,
        # external_identifier), not the exact URL string: the same board
        # can appear under multiple URL variants (different query params,
        # a tracking suffix, ...) across sheet edits. Matching on the
        # identifier - discovered the hard way, see docs/job_sources.md -
        # avoids creating a second CareerSource (and therefore duplicate
        # jobs, once fully crawled) for what both resolve to one board.
        query = query.where(
            CareerSource.source_type == resolved.source_type,
            CareerSource.external_identifier == resolved.external_identifier,
        )
    else:
        query = query.where(CareerSource.source_url == url)

    existing = db.execute(query).scalar_one_or_none()
    if existing is not None:
        return 0

    poll_interval = _DEFAULT_POLL_MINUTES.get(
        resolved.source_type, get_settings().default_poll_minutes
    )
    source = CareerSource(
        company_id=company.id,
        source_type=resolved.source_type,
        source_url=url,
        external_identifier=resolved.external_identifier,
        unsupported_reason=resolved.unsupported_reason,
        enabled=resolved.source_type != CareerSourceType.UNSUPPORTED,
        poll_interval_minutes=poll_interval,
    )
    db.add(source)
    return 1


def _disable_missing_companies(
    existing_by_name: dict[str, Company], seen_normalized_names: set[str]
) -> int:
    disabled = 0
    for normalized_name, company in existing_by_name.items():
        if normalized_name not in seen_normalized_names and company.enabled:
            company.enabled = False
            disabled += 1
    return disabled

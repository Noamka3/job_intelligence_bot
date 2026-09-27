"""Crawls one CareerSource: discovers new / changed / missing jobs and
persists them, scoring each new/changed job against the active candidate
profile right away (the matching engine is cheap, no LLM calls, so
there's no reason to defer it to a separate manual step). This is the
core loop - incremental ingestion, closure detection, observability via
CrawlRun - as plain business logic, callable from the CLI/API and from
the Celery tasks in app/tasks/ without change.
"""

from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import timedelta
from typing import Any

import httpx
from sqlalchemy import case, select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.timezone import utc_now
from app.ingestion.adapters.base import (
    BoardBehindPage,
    JobDetails,
    JobSourceAdapter,
    JobStub,
    JobUnavailableError,
)
from app.ingestion.registry import get_adapter, supported_source_types
from app.models.career_source import CareerSource
from app.models.company import Company
from app.models.crawl_run import CrawlRun
from app.models.enums import CareerSourceType, CrawlRunStatus, JobStatus
from app.models.job_posting import JobPosting
from app.services.embeddings.base import EmbeddingProvider
from app.services.jev import needs_reading, read_posting
from app.services.jobs.location import classify_country, classify_region
from app.services.jobs.normalization import (
    build_embedding_text,
    build_normalized_description,
    content_hash_for,
    normalize_job_title,
    normalize_location,
)
from app.services.matching.runner import score_job
from app.services.matching.seniority import SeniorityAssessment, assess_seniority
from app.services.sheets.company_sync import default_poll_minutes

logger = logging.getLogger(__name__)

# Sources that fetch a page per job, or drive a browser - the expensive
# class the dispatcher serves after the API-backed ones.
SLOW_SOURCE_TYPES = frozenset({CareerSourceType.GENERIC_HTML, CareerSourceType.PLAYWRIGHT})
# A plain career page that shows no job links this many successful
# crawls in a row draws its list with JavaScript (or is truly empty,
# which the browser confirms just as well): the browser fallback takes
# it over, on the browser's own interval. A 403 hands it over at once.
_EMPTY_CRAWLS_BEFORE_BROWSER = 3


def get_due_sources(db: Session, limit: int | None = None) -> list[CareerSource]:
    """Enabled sources that have never been crawled, or are past their own
    next_check_at - the dispatch query Celery Beat uses every 5 minutes,
    reused by the CLI/API manual trigger so it behaves
    identically. Longest-overdue first (never crawled before anything
    else), so a `limit` hands out the backlog fairly across ticks.

    Source types without a registered adapter are left out on purpose:
    crawling them would only
    write a FAILED "NoAdapter" CrawlRun per tick and push their
    next_check_at out with exponential backoff - so once the adapter does
    land, they'd sit out up to a day before the first real crawl. Skipping
    them here keeps their next_check_at untouched, so they're picked up on
    the first tick after the adapter is registered.
    """
    now = utc_now()
    # Cheap API sources go first: all 44 of them together take about a
    # minute, so a new job at an ATS-backed company never waits behind a
    # 259-page career site. Within a class, longest overdue first.
    cost_class = case((CareerSource.source_type.in_(SLOW_SOURCE_TYPES), 1), else_=0)
    query = (
        select(CareerSource)
        .join(Company, CareerSource.company_id == Company.id)
        .where(
            Company.enabled.is_(True),
            CareerSource.enabled.is_(True),
            CareerSource.source_type.in_(supported_source_types()),
            (CareerSource.next_check_at.is_(None)) | (CareerSource.next_check_at <= now),
        )
        .order_by(cost_class, CareerSource.next_check_at.asc().nulls_first(), CareerSource.id)
    )
    if limit is not None:
        query = query.limit(limit)
    return list(db.execute(query).scalars())


def crawl_source(
    db: Session, source: CareerSource, embedding_provider: EmbeddingProvider
) -> CrawlRun:
    # Every network call below happens with *no* open transaction: a
    # crawl of a big source runs for minutes, and a transaction held across
    # those fetches sat "idle in transaction" holding row locks on
    # job_postings/job_matches (and FK locks on the candidate profile) the
    # whole time - blocking the other worker and anything else touching
    # the same rows. So the run/source state is committed up front and
    # each job's writes are committed as soon as they're made.
    run = CrawlRun(career_source_id=source.id, status=CrawlRunStatus.RUNNING)
    db.add(run)
    source.last_attempt_at = utc_now()
    db.commit()

    adapter = get_adapter(source.source_type)
    if adapter is None:
        return _fail_run(db, run, source, "NoAdapter", f"No adapter for {source.source_type}")

    try:
        stubs = _first_of_each_id(adapter.list_jobs(source))
    except BoardBehindPage as found:
        # The page is a front for a real ATS board. Point the source at
        # it and let this crawl end: the next one runs the board's own
        # adapter, which reads it properly instead of scraping a render.
        _repoint_to_board(source, found.board)
        run.status = CrawlRunStatus.SUCCESS
        run.finished_at = utc_now()
        source.last_attempt_at = utc_now()
        db.commit()
        return run
    except Exception as exc:  # noqa: BLE001 - one broken source must never crash the crawler
        logger.warning(
            "crawl failed while listing jobs",
            extra={"source_id": source.id, "error_type": type(exc).__name__},
        )
        if source.source_type == CareerSourceType.GENERIC_HTML and _refused_plain_reader(exc):
            _hand_to_browser(source, "403")
        return _fail_run(db, run, source, type(exc).__name__, str(exc)[:2000])

    existing_jobs: dict[str, JobPosting] = {
        job.external_job_id: job
        for job in db.execute(
            select(JobPosting).where(JobPosting.career_source_id == source.id)
        ).scalars()
    }
    db.commit()

    seen_external_ids: set[str] = set()
    created = updated = attempted = failed = unavailable_closed = 0

    to_fetch: list[tuple[JobStub, JobPosting | None]] = []
    for stub in stubs:
        seen_external_ids.add(stub.external_job_id)
        existing = existing_jobs.get(stub.external_job_id)
        if existing is not None and _is_definitely_unchanged(existing, stub):
            _mark_seen(existing)
            db.commit()
            continue
        to_fetch.append((stub, existing))

    # The page fetches run a few at a time in a thread pool (network-
    # bound; a plain career site with 259 postings took 23 minutes one
    # page after another). Everything after the fetch - embedding, ORM
    # writes, scoring - stays on this thread, in listing order, so the
    # per-job commit semantics are exactly as before.
    pool = ThreadPoolExecutor(max_workers=max(1, get_settings().crawl_fetch_concurrency))
    try:
        outcomes = pool.map(lambda pair: _fetch_details(adapter, source, pair[0]), to_fetch)
        for (stub, existing), outcome in zip(to_fetch, outcomes, strict=True):
            attempted += 1
            try:
                if isinstance(outcome, Exception):
                    raise outcome
                fetched = _embed(outcome, existing, embedding_provider)
            except JobUnavailableError as exc:
                # The source says this isn't an open job page (gone, or a
                # listing/category page a generic listing linked to) - not
                # a failure to retry. Close it if we had stored it as a job.
                logger.info(
                    "job unavailable at source",
                    extra={
                        "source_id": source.id,
                        "external_job_id": stub.external_job_id,
                        "reason": exc.reason,
                    },
                )
                if existing is not None:
                    existing.last_seen_at = utc_now()
                    existing.status = JobStatus.CLOSED
                    unavailable_closed += 1
                    db.commit()
                continue
            except Exception as exc:  # noqa: BLE001 - one broken job must not sink the rest of the listing
                logger.warning(
                    "skipping job: fetch/embed failed",
                    extra={
                        "source_id": source.id,
                        "external_job_id": stub.external_job_id,
                        "error_type": type(exc).__name__,
                    },
                )
                failed += 1
                if existing is not None:
                    # It was listed, so it's not missing - just not readable now.
                    _mark_seen(existing)
                    db.commit()
                continue

            if existing is None:
                new_job = _create_job(db, source, fetched)
                existing_jobs[stub.external_job_id] = new_job
                created += 1
                _read_with_jev(new_job)
                score_job(db, new_job)
            else:
                _mark_seen(existing)
                if _apply_update(existing, fetched):
                    updated += 1
                    # Only rescore when the content actually changed enough
                    # to re-embed - never re-run matching on unchanged
                    # jobs.
                    _read_with_jev(existing)
                    score_job(db, existing)
            db.commit()
    except Exception as exc:
        # A DB error mid-loop (an unexpected constraint, a lost connection)
        # must not leave the run RUNNING forever - record it, then let the
        # task layer's retry semantics see the exception.
        db.rollback()
        _fail_run(db, run, source, type(exc).__name__, str(exc)[:2000])
        raise
    finally:
        pool.shutdown(wait=True, cancel_futures=True)

    if attempted and failed == attempted:
        # Every detail fetch failing means the site/adapter is broken, not
        # one stale listing entry - treat it like a failed listing call so
        # the source backs off, and don't close anything based on it.
        run.jobs_seen = len(stubs)
        run.jobs_failed = failed
        return _fail_run(
            db, run, source, "AllJobsFailed", f"all {failed} job detail fetches failed"
        )

    closed = _close_missing_jobs(existing_jobs, seen_external_ids) + unavailable_closed

    if (
        source.source_type == CareerSourceType.GENERIC_HTML
        and not stubs
        and _plain_reader_kept_finding_nothing(db, source)
    ):
        _hand_to_browser(source, "empty")

    source.last_successful_check_at = utc_now()
    source.consecutive_failures = 0
    source.last_http_status = 200
    source.next_check_at = utc_now() + timedelta(minutes=source.poll_interval_minutes)

    run.status = CrawlRunStatus.SUCCESS
    run.finished_at = utc_now()
    run.jobs_seen = len(stubs)
    run.jobs_created = created
    run.jobs_updated = updated
    run.jobs_closed = closed
    run.jobs_failed = failed

    db.commit()
    return run


def _first_of_each_id(stubs: list[JobStub]) -> list[JobStub]:
    """A listing that names the same job twice (Elbit's feed does, for one
    position) is read once: inserting the second row would violate the
    (source, external_job_id) uniqueness and fail the whole crawl."""
    unique: dict[str, JobStub] = {}
    for stub in stubs:
        unique.setdefault(stub.external_job_id, stub)
    return list(unique.values())


def _refused_plain_reader(exc: BaseException) -> bool:
    return isinstance(exc, httpx.HTTPStatusError) and exc.response.status_code == 403


def _plain_reader_kept_finding_nothing(db: Session, source: CareerSource) -> bool:
    """The successful crawls before this one, as many as the rule needs,
    all saw no jobs."""
    earlier = _EMPTY_CRAWLS_BEFORE_BROWSER - 1
    seen = (
        db.execute(
            select(CrawlRun.jobs_seen)
            .where(
                CrawlRun.career_source_id == source.id,
                CrawlRun.status == CrawlRunStatus.SUCCESS,
            )
            .order_by(CrawlRun.started_at.desc())
            .limit(earlier)
        )
        .scalars()
        .all()
    )
    return len(seen) == earlier and not any(seen)


def _repoint_to_board(source: CareerSource, board: Any) -> None:
    """Turn a scraped page into the board it was fronting (a
    resolver.ResolvedSource, already verified against that ATS's API)."""
    source.source_type = board.source_type
    source.external_identifier = board.external_identifier
    if board.board_url:
        source.source_url = board.board_url
    source.poll_interval_minutes = default_poll_minutes(board.source_type)
    source.consecutive_failures = 0
    source.next_check_at = None
    logger.info(
        "page is a front for a board; source re-pointed",
        extra={
            "source_id": source.id,
            "source_type": board.source_type.value,
            "identifier": board.external_identifier,
        },
    )


def _hand_to_browser(source: CareerSource, reason: str) -> None:
    source.source_type = CareerSourceType.PLAYWRIGHT
    source.poll_interval_minutes = default_poll_minutes(CareerSourceType.PLAYWRIGHT)
    source.consecutive_failures = 0
    logger.info(
        "source handed to the browser fallback", extra={"source_id": source.id, "reason": reason}
    )


def _mark_seen(job: JobPosting) -> None:
    job.last_seen_at = utc_now()
    job.missing_streak = 0
    job.status = JobStatus.ACTIVE


@dataclass(frozen=True)
class _FetchedJob:
    details: JobDetails
    content_hash: str
    # None when the content hash matches the existing row - nothing to
    # re-embed, only metadata to refresh.
    embedding: list[float] | None


def _fetch_details(
    adapter: JobSourceAdapter, source: CareerSource, stub: JobStub
) -> JobDetails | Exception:
    """One job's network work, run on a pool thread. Returns the failure
    instead of raising so the calling loop handles it in listing order,
    with the same logging/counting as before."""
    try:
        return adapter.fetch_job(source, stub)
    except Exception as exc:  # noqa: BLE001 - re-raised on the main thread
        return exc


def _embed(
    details: JobDetails, existing: JobPosting | None, embedding_provider: EmbeddingProvider
) -> _FetchedJob:
    """One job's model work, done before any ORM mutation - so an
    embedding failure leaves the existing row exactly as it was instead of
    half-updated with a stale embedding."""
    embedding_text = build_embedding_text(details)
    content_hash = content_hash_for(embedding_text)
    if (
        existing is not None
        and content_hash == existing.content_hash
        and existing.archived_at is None
    ):
        return _FetchedJob(details, content_hash, None)
    return _FetchedJob(details, content_hash, embedding_provider.embed_one(embedding_text))


def assess_job_seniority(
    title: str, qualifications: str | None, normalized_description: str | None
) -> SeniorityAssessment:
    """The one place the seniority read of a posting is defined - stored
    on the row at ingest (JobPosting.seniority / experience_min_years, what
    the dashboard's "fits a junior" tag and filter use) and recomputed by
    the scorer from the same fields."""
    return assess_seniority(title, qualifications or normalized_description or "")


def _read_with_jev(job: JobPosting) -> None:
    """Best-effort, like the LLM step of a CV upload: a failed or
    disabled read leaves the column as it was and the crawl goes on."""
    if needs_reading(job):
        job.jev_reading = read_posting(job)


def _create_job(db: Session, source: CareerSource, fetched: _FetchedJob) -> JobPosting:
    details = fetched.details
    assert fetched.embedding is not None  # a new job never has an existing hash to match
    normalized_description = build_normalized_description(details)
    seniority = assess_job_seniority(details.title, details.qualifications, normalized_description)
    job = JobPosting(
        company_id=source.company_id,
        career_source_id=source.id,
        external_job_id=details.external_job_id,
        title=details.title,
        normalized_title=normalize_job_title(details.title),
        department=details.department,
        team=details.team,
        location_text=details.location_text,
        normalized_location=normalize_location(details.location_text),
        country=classify_country(details.location_text),
        region=classify_region(details.location_text),
        remote_type=details.remote_type,
        employment_type=details.employment_type,
        description=details.description,
        normalized_description=normalized_description,
        responsibilities=details.responsibilities,
        qualifications=details.qualifications,
        seniority=seniority.level,
        experience_min_years=seniority.min_years_required,
        required_skills=details.required_skills,
        preferred_skills=details.preferred_skills,
        source_url=details.source_url,
        apply_url=details.apply_url,
        source_published_at=details.source_published_at,
        source_updated_at=details.source_updated_at,
        details_fetched_at=utc_now(),
        status=JobStatus.ACTIVE,
        content_hash=fetched.content_hash,
        embedding=fetched.embedding,
    )
    db.add(job)
    db.flush()
    return job


def _is_definitely_unchanged(existing: JobPosting, stub: JobStub) -> bool:
    """True when the job's page need not be downloaded this crawl.

    A listing that carries the ATS's own "updated" timestamp proves it.
    Plain career sites (and some ATS list calls) carry none - there a
    stored job counts as unchanged until its page is older than
    JOB_DETAILS_REFRESH_HOURS, so a crawl costs one listing download plus
    the pages of links that are new. Before this, every crawl of a plain
    site re-downloaded every page it had already stored. A closed job
    listed again is re-read before it reopens: it may have been closed
    because its page turned out not to be a posting at all.
    """
    if stub.source_updated_at is not None and existing.source_updated_at is not None:
        return stub.source_updated_at <= existing.source_updated_at
    if existing.status == JobStatus.CLOSED or existing.details_fetched_at is None:
        return False
    if existing.archived_at is not None:
        # Its text is gone on purpose (retention). A listing timestamp the
        # check above could not compare is a reason to read it again; a
        # listing without one is not.
        return stub.source_updated_at is None
    refresh_after = timedelta(hours=get_settings().job_details_refresh_hours)
    return utc_now() - existing.details_fetched_at < refresh_after


def _apply_update(existing: JobPosting, fetched: _FetchedJob) -> bool:
    """Returns True when the job's content changed enough to re-embed."""
    details = fetched.details
    existing.title = details.title
    existing.normalized_title = normalize_job_title(details.title)
    existing.department = details.department
    existing.team = details.team
    existing.location_text = details.location_text
    existing.normalized_location = normalize_location(details.location_text)
    existing.country = classify_country(details.location_text)
    existing.region = classify_region(details.location_text)
    existing.remote_type = details.remote_type
    existing.employment_type = details.employment_type
    existing.description = details.description
    existing.normalized_description = build_normalized_description(details)
    existing.responsibilities = details.responsibilities
    existing.qualifications = details.qualifications
    seniority = assess_job_seniority(
        details.title, details.qualifications, existing.normalized_description
    )
    existing.seniority = seniority.level
    existing.experience_min_years = seniority.min_years_required
    existing.required_skills = details.required_skills
    existing.preferred_skills = details.preferred_skills
    existing.source_url = details.source_url
    existing.apply_url = details.apply_url
    existing.source_published_at = details.source_published_at
    existing.source_updated_at = details.source_updated_at
    existing.details_fetched_at = utc_now()
    existing.archived_at = None

    if fetched.embedding is None:
        return False

    existing.embedding = fetched.embedding
    existing.content_hash = fetched.content_hash
    return True


def _close_missing_jobs(existing_jobs: dict[str, JobPosting], seen_external_ids: set[str]) -> int:
    """A job absent from this listing is not immediately closed - it must
    stay absent across JOB_MISSING_THRESHOLD consecutive successful crawls
    first, since a source can omit a job in one crawl by
    accident (pagination hiccup, transient filtering) without it actually
    having closed.
    """
    threshold = get_settings().job_missing_threshold
    closed = 0
    for external_id, job in existing_jobs.items():
        if external_id in seen_external_ids or job.status == JobStatus.CLOSED:
            continue
        job.missing_streak += 1
        if job.missing_streak >= threshold:
            job.status = JobStatus.CLOSED
            closed += 1
    return closed


def _fail_run(
    db: Session, run: CrawlRun, source: CareerSource, error_type: str, error_message: str
) -> CrawlRun:
    run.status = CrawlRunStatus.FAILED
    run.error_type = error_type
    run.error_message = error_message
    run.finished_at = utc_now()

    source.consecutive_failures += 1
    backoff_minutes = min(source.poll_interval_minutes * (2**source.consecutive_failures), 24 * 60)
    source.next_check_at = utc_now() + timedelta(minutes=backoff_minutes)

    db.commit()
    return run

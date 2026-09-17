"""Crawls one CareerSource: discovers new / changed / missing jobs and
persists them, scoring each new/changed job against the active candidate
profile right away (Phase 5's matching engine - cheap, no LLM calls, so
there's no reason to defer it to a separate manual step). This is the
core loop spec §20 (incremental ingestion), §23 (closure detection), and
§33 (observability via CrawlRun) describe - plain business logic,
callable from the CLI/API and from the Celery tasks in app/tasks/
(Phase 6) without change.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.timezone import utc_now
from app.ingestion.adapters.base import (
    JobDetails,
    JobSourceAdapter,
    JobStub,
    JobUnavailableError,
)
from app.ingestion.registry import get_adapter, supported_source_types
from app.models.career_source import CareerSource
from app.models.company import Company
from app.models.crawl_run import CrawlRun
from app.models.enums import CrawlRunStatus, JobStatus
from app.models.job_posting import JobPosting
from app.services.embeddings.base import EmbeddingProvider
from app.services.jobs.location import classify_country, classify_region
from app.services.jobs.normalization import (
    build_embedding_text,
    build_normalized_description,
    content_hash_for,
    normalize_job_title,
    normalize_location,
)
from app.services.matching.runner import score_job

logger = logging.getLogger(__name__)


def get_due_sources(db: Session, limit: int | None = None) -> list[CareerSource]:
    """Enabled sources that have never been crawled, or are past their own
    next_check_at - the dispatch query Celery Beat uses every 5 minutes
    (spec §19), reused by the CLI/API manual trigger so it behaves
    identically. Longest-overdue first (never crawled before anything
    else), so a `limit` hands out the backlog fairly across ticks.

    Source types without a registered adapter yet (workday, generic_html,
    ... until Phase 8) are left out on purpose: crawling them would only
    write a FAILED "NoAdapter" CrawlRun per tick and push their
    next_check_at out with exponential backoff - so once the adapter does
    land, they'd sit out up to a day before the first real crawl. Skipping
    them here keeps their next_check_at untouched, so they're picked up on
    the first tick after the adapter is registered.
    """
    now = utc_now()
    query = (
        select(CareerSource)
        .join(Company, CareerSource.company_id == Company.id)
        .where(
            Company.enabled.is_(True),
            CareerSource.enabled.is_(True),
            CareerSource.source_type.in_(supported_source_types()),
            (CareerSource.next_check_at.is_(None)) | (CareerSource.next_check_at <= now),
        )
        .order_by(CareerSource.next_check_at.asc().nulls_first(), CareerSource.id)
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
        stubs = adapter.list_jobs(source)
    except Exception as exc:  # noqa: BLE001 - one broken source must never crash the crawler (spec §34)
        logger.warning(
            "crawl failed while listing jobs",
            extra={"source_id": source.id, "error_type": type(exc).__name__},
        )
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

    try:
        for stub in stubs:
            seen_external_ids.add(stub.external_job_id)
            existing = existing_jobs.get(stub.external_job_id)

            if existing is not None and _is_definitely_unchanged(existing, stub):
                _mark_seen(existing)
                db.commit()
                continue

            attempted += 1
            try:
                fetched = _fetch_and_embed(adapter, source, stub, existing, embedding_provider)
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
            except Exception as exc:  # noqa: BLE001 - one broken job must not sink the rest of the listing (spec §34)
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
                score_job(db, new_job)
            else:
                _mark_seen(existing)
                if _apply_update(existing, fetched):
                    updated += 1
                    # Only rescore when the content actually changed enough
                    # to re-embed (spec §45: never re-run matching on
                    # unchanged jobs).
                    score_job(db, existing)
            db.commit()
    except Exception as exc:
        # A DB error mid-loop (an unexpected constraint, a lost connection)
        # must not leave the run RUNNING forever - record it, then let the
        # task layer's retry semantics see the exception.
        db.rollback()
        _fail_run(db, run, source, type(exc).__name__, str(exc)[:2000])
        raise

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


def _fetch_and_embed(
    adapter: JobSourceAdapter,
    source: CareerSource,
    stub: JobStub,
    existing: JobPosting | None,
    embedding_provider: EmbeddingProvider,
) -> _FetchedJob:
    """All of one job's network + model work, done before any ORM
    mutation - so a fetch that 404s (job pulled between the list call and
    now) or an embedding failure leaves the existing row exactly as it was
    instead of half-updated with a stale embedding.
    """
    details = adapter.fetch_job(source, stub)
    embedding_text = build_embedding_text(details)
    content_hash = content_hash_for(embedding_text)
    if existing is not None and content_hash == existing.content_hash:
        return _FetchedJob(details, content_hash, None)
    return _FetchedJob(details, content_hash, embedding_provider.embed_one(embedding_text))


def _create_job(db: Session, source: CareerSource, fetched: _FetchedJob) -> JobPosting:
    details = fetched.details
    assert fetched.embedding is not None  # a new job never has an existing hash to match
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
        normalized_description=build_normalized_description(details),
        responsibilities=details.responsibilities,
        qualifications=details.qualifications,
        required_skills=details.required_skills,
        preferred_skills=details.preferred_skills,
        source_url=details.source_url,
        apply_url=details.apply_url,
        source_published_at=details.source_published_at,
        source_updated_at=details.source_updated_at,
        status=JobStatus.ACTIVE,
        content_hash=fetched.content_hash,
        embedding=fetched.embedding,
    )
    db.add(job)
    db.flush()
    return job


def _is_definitely_unchanged(existing: JobPosting, stub: JobStub) -> bool:
    """True only when the cheap list call already proves nothing changed -
    saves a fetch_job call + re-embed for the common case of a job sitting
    unchanged across many consecutive 5-minute polls.
    """
    if stub.source_updated_at is None or existing.source_updated_at is None:
        return False
    return stub.source_updated_at <= existing.source_updated_at


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
    existing.required_skills = details.required_skills
    existing.preferred_skills = details.preferred_skills
    existing.source_url = details.source_url
    existing.apply_url = details.apply_url
    existing.source_published_at = details.source_published_at
    existing.source_updated_at = details.source_updated_at

    if fetched.embedding is None:
        return False

    existing.embedding = fetched.embedding
    existing.content_hash = fetched.content_hash
    return True


def _close_missing_jobs(existing_jobs: dict[str, JobPosting], seen_external_ids: set[str]) -> int:
    """A job absent from this listing is not immediately closed - it must
    stay absent across JOB_MISSING_THRESHOLD consecutive successful crawls
    first (spec §23), since a source can omit a job in one crawl by
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

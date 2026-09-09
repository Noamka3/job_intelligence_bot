"""Crawls one CareerSource: discovers new / changed / missing jobs and
persists them. This is the core loop spec §20 (incremental ingestion),
§23 (closure detection), and §33 (observability via CrawlRun) describe -
plain business logic, callable from the CLI now and from a Celery task
body later (Phase 6) without change.
"""

from __future__ import annotations

import logging
from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.timezone import utc_now
from app.ingestion.adapters.base import JobStub
from app.ingestion.registry import get_adapter
from app.models.career_source import CareerSource
from app.models.crawl_run import CrawlRun
from app.models.enums import CrawlRunStatus, JobStatus
from app.models.job_posting import JobPosting
from app.services.embeddings.base import EmbeddingProvider
from app.services.jobs.normalization import (
    build_embedding_text,
    build_normalized_description,
    content_hash_for,
    normalize_job_title,
    normalize_location,
)

logger = logging.getLogger(__name__)


def get_due_sources(db: Session) -> list[CareerSource]:
    """Enabled sources that have never been crawled, or are past their own
    next_check_at - the dispatch query Celery Beat will use every 5
    minutes from Phase 6 onward (spec §19), reused here so the CLI/API
    manual trigger behaves identically.
    """
    now = utc_now()
    query = select(CareerSource).where(
        CareerSource.enabled.is_(True),
        (CareerSource.next_check_at.is_(None)) | (CareerSource.next_check_at <= now),
    )
    return list(db.execute(query).scalars())


def crawl_source(
    db: Session, source: CareerSource, embedding_provider: EmbeddingProvider
) -> CrawlRun:
    run = CrawlRun(career_source_id=source.id, status=CrawlRunStatus.RUNNING)
    db.add(run)
    db.flush()
    source.last_attempt_at = utc_now()

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

    seen_external_ids: set[str] = set()
    created = updated = closed = 0

    for stub in stubs:
        seen_external_ids.add(stub.external_job_id)
        existing = existing_jobs.get(stub.external_job_id)

        if existing is None:
            existing_jobs[stub.external_job_id] = _ingest_new_job(
                db, source, stub, embedding_provider
            )
            created += 1
            continue

        existing.last_seen_at = utc_now()
        existing.missing_streak = 0
        existing.status = JobStatus.ACTIVE

        if _is_definitely_unchanged(existing, stub):
            continue
        if _refresh_existing_job(db, source, stub, existing, embedding_provider):
            updated += 1

    closed = _close_missing_jobs(existing_jobs, seen_external_ids)

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

    db.commit()
    return run


def _ingest_new_job(
    db: Session, source: CareerSource, stub: JobStub, embedding_provider: EmbeddingProvider
) -> JobPosting:
    adapter = get_adapter(source.source_type)
    assert adapter is not None  # already checked by the caller
    details = adapter.fetch_job(source, stub)

    embedding_text = build_embedding_text(details)
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
        content_hash=content_hash_for(embedding_text),
        embedding=embedding_provider.embed_one(embedding_text),
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


def _refresh_existing_job(
    db: Session,
    source: CareerSource,
    stub: JobStub,
    existing: JobPosting,
    embedding_provider: EmbeddingProvider,
) -> bool:
    adapter = get_adapter(source.source_type)
    assert adapter is not None
    details = adapter.fetch_job(source, stub)
    embedding_text = build_embedding_text(details)
    new_hash = content_hash_for(embedding_text)

    existing.title = details.title
    existing.normalized_title = normalize_job_title(details.title)
    existing.department = details.department
    existing.team = details.team
    existing.location_text = details.location_text
    existing.normalized_location = normalize_location(details.location_text)
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

    if new_hash == existing.content_hash:
        return False

    existing.embedding = embedding_provider.embed_one(embedding_text)
    existing.content_hash = new_hash
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

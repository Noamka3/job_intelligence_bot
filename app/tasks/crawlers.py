"""Celery tasks wrapping the Phase 4/5 ingestion + scoring logic
(app.services.jobs.ingestion, app.services.matching.runner). No new
business logic here - just Celery's dispatch/retry semantics around
functions that are already independently testable without Celery.
"""

from __future__ import annotations

import logging
from datetime import timedelta

from app.core.config import get_settings
from app.core.timezone import utc_now
from app.db.session import get_session_factory
from app.models.career_source import CareerSource
from app.services.embeddings import get_embedding_provider
from app.services.jobs.ingestion import crawl_source, get_due_sources
from app.services.scheduler_state import crawl_queue_depth, record_dispatch_tick
from app.tasks.celery_app import celery_app

logger = logging.getLogger(__name__)


@celery_app.task(name="app.tasks.crawlers.dispatch_due_sources")
def dispatch_due_sources() -> int:
    """Runs every DEFAULT_POLL_MINUTES via Celery Beat (spec §19).
    Enqueues one crawl_one_source task per due source rather than
    crawling them inline, so a slow source can't delay the others and
    each gets Celery's own retry semantics on top of the per-source
    backoff crawl_source already tracks via CareerSource.next_check_at.
    """
    record_dispatch_tick()
    # Top the queue up to the target, never past it: what stays due is
    # simply picked up next tick, longest-overdue first. An unreachable
    # Redis reads as an empty queue - the .delay() below would fail
    # loudly anyway.
    backlog = crawl_queue_depth() or 0
    budget = max(0, get_settings().crawl_queue_target - backlog)
    if budget == 0:
        logger.info("crawl queue full, dispatching nothing", extra={"backlog": backlog})
        return 0
    session_factory = get_session_factory()
    with session_factory() as db:
        sources = get_due_sources(db, limit=budget)
        # Lease every source for one poll interval *before* enqueueing.
        # With a long queue (bootstrap, a fresh sheet sync) the next Beat
        # tick fires before the worker reaches the tail, and would
        # otherwise re-enqueue everything still waiting - two workers then
        # crawl the same source at once and collide on
        # uq_job_postings_source_external_id. crawl_source overwrites the
        # lease with the real next_check_at when it finishes; if a task is
        # lost (worker killed), the source just becomes due again one
        # interval later. Committed first so a worker that finishes before
        # this loop does can't have its result clobbered by the lease.
        now = utc_now()
        for source in sources:
            source.next_check_at = now + timedelta(minutes=source.poll_interval_minutes)
        db.commit()

        for source in sources:
            crawl_one_source.delay(source.id)
        logger.info(
            "dispatched due sources", extra={"source_count": len(sources), "backlog": backlog}
        )
        return len(sources)


@celery_app.task(
    name="app.tasks.crawlers.crawl_one_source",
    autoretry_for=(Exception,),
    retry_backoff=True,
    retry_backoff_max=600,
    retry_jitter=True,
    max_retries=3,
)
def crawl_one_source(source_id: int) -> None:
    """Retries here are a safety net for genuinely unexpected failures
    (e.g. a DB hiccup) - the routine case (a source's HTTP call failing)
    is already handled without raising inside crawl_source, which records
    a FAILED CrawlRun and backs off that source's own next_check_at
    instead (spec §34).
    """
    session_factory = get_session_factory()
    embedding_provider = get_embedding_provider()
    with session_factory() as db:
        source = db.get(CareerSource, source_id)
        if source is None:
            logger.warning(
                "crawl_one_source: source no longer exists", extra={"source_id": source_id}
            )
            return

        run = crawl_source(db, source, embedding_provider)
        logger.info(
            "crawled source",
            extra={
                "source_id": source_id,
                "status": run.status.value,
                "jobs_seen": run.jobs_seen,
                "jobs_created": run.jobs_created,
                "jobs_updated": run.jobs_updated,
                "jobs_closed": run.jobs_closed,
                "jobs_failed": run.jobs_failed,
            },
        )

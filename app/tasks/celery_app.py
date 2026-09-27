"""Celery application + beat schedule.

The worker and beat scheduler run inside Docker (Linux) containers, not
directly on the Windows host - Celery's default prefork pool doesn't work
on native Windows. See docker-compose.yml's worker/beat services and
README's Celery troubleshooting entry.
"""

from __future__ import annotations

from celery import Celery
from celery.schedules import crontab
from celery.signals import worker_process_init

from app.core.config import get_settings
from app.db.session import dispose_engine_after_fork
from app.services.scheduler_state import CRAWL_QUEUE, SCHEDULER_QUEUE, reset_client_after_fork

settings = get_settings()

celery_app = Celery(
    "job_intel_bot",
    broker=settings.redis_url,
    include=["app.tasks.crawlers", "app.tasks.retention"],
)

celery_app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    timezone="UTC",
    enable_utc=True,
    # Nothing reads task return values (Beat fires and forgets; crawl
    # outcomes live in CrawlRun rows), so don't keep a result per task in
    # Redis - at 5-minute ticks over ~200 sources that's tens of thousands
    # of keys a day for nothing.
    task_ignore_result=True,
    # The dispatcher must never wait behind the crawls it feeds: after a
    # pause (worker restart, laptop asleep) hundreds of crawls queue up
    # and a dispatcher stuck behind them would report the "next refresh"
    # as late for an hour. It gets its own queue, consumed by the beat
    # container (an embedded worker, see docker-compose.yml); the crawl
    # worker consumes only the default queue.
    task_default_queue=CRAWL_QUEUE,
    task_routes={
        "app.tasks.crawlers.dispatch_due_sources": {"queue": SCHEDULER_QUEUE},
        "app.tasks.retention.prune_postings": {"queue": SCHEDULER_QUEUE},
    },
    beat_schedule={
        "dispatch-due-sources": {
            "task": "app.tasks.crawlers.dispatch_due_sources",
            "schedule": settings.default_poll_minutes * 60,
        },
        # Retention, once a night (01:00 in Israel), when nothing is watching.
        "prune-postings": {
            "task": "app.tasks.retention.prune_postings",
            "schedule": crontab(hour=22, minute=0),
        },
    },
)


@worker_process_init.connect
def _reset_pools_in_child(**_: object) -> None:
    dispose_engine_after_fork()
    reset_client_after_fork()

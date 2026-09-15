"""Celery application + beat schedule (spec §19/§46 Phase 6).

The worker and beat scheduler run inside Docker (Linux) containers, not
directly on the Windows host - Celery's default prefork pool doesn't work
on native Windows. See docker-compose.yml's worker/beat services and
README's Celery troubleshooting entry.
"""

from __future__ import annotations

from celery import Celery

from app.core.config import get_settings

settings = get_settings()

celery_app = Celery(
    "job_intel_bot",
    broker=settings.redis_url,
    backend=settings.redis_url,
    include=["app.tasks.crawlers"],
)

celery_app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="UTC",
    enable_utc=True,
    beat_schedule={
        "dispatch-due-sources": {
            "task": "app.tasks.crawlers.dispatch_due_sources",
            "schedule": settings.default_poll_minutes * 60,
        },
    },
)

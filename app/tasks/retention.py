"""The nightly prune, as a Celery task on the scheduler queue - see
app/services/jobs/retention.py for what it removes and why."""

from __future__ import annotations

import logging

from app.db.session import get_session_factory
from app.services.jobs.retention import prune_postings
from app.tasks.celery_app import celery_app

logger = logging.getLogger(__name__)


@celery_app.task(name="app.tasks.retention.prune_postings")
def prune_postings_task() -> None:
    with get_session_factory()() as db:
        result = prune_postings(db)
    logger.info("pruned postings", extra={"archived": result.archived, "deleted": result.deleted})

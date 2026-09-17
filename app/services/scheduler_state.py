"""The one piece of scheduler state the dashboard needs that no table
records: when Beat last fired the dispatcher. Kept in Redis (which the
scheduler already depends on) as a single timestamp, so the status page
can show "next refresh in mm:ss" and prove the timer is really running.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta

import redis

from app.core.config import get_settings
from app.core.timezone import utc_now

logger = logging.getLogger(__name__)

_KEY = "job_bot:last_dispatch_at"

# Celery's default queue, where crawl_one_source tasks wait (the
# dispatcher itself is routed to its own "scheduler" queue - see
# celery_app.py). With the Redis broker a queue is a plain list, so its
# length is the crawl backlog.
CRAWL_QUEUE = "celery"
SCHEDULER_QUEUE = "scheduler"


def crawl_queue_depth() -> int | None:
    """How many crawls are waiting for a worker; None if Redis is down."""
    try:
        return int(redis.Redis.from_url(get_settings().redis_url).llen(CRAWL_QUEUE))
    except redis.RedisError:
        return None


def record_dispatch_tick() -> None:
    try:
        redis.Redis.from_url(get_settings().redis_url).set(_KEY, utc_now().isoformat())
    except redis.RedisError as exc:  # never let bookkeeping break a dispatch
        logger.warning("could not record dispatch tick", extra={"error": str(exc)})


def last_dispatch_at() -> datetime | None:
    try:
        raw = redis.Redis.from_url(get_settings().redis_url).get(_KEY)
    except redis.RedisError:
        return None
    if raw is None:
        return None
    value = raw.decode() if isinstance(raw, bytes) else str(raw)
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def next_dispatch_at() -> datetime | None:
    last = last_dispatch_at()
    if last is None:
        return None
    return last + timedelta(minutes=get_settings().default_poll_minutes)

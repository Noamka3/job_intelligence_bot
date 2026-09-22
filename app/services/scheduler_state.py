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
# One pooled client, not a fresh connection per call. The status page
# asks for three of these values at once, and on Windows each new
# connection through Docker's port forwarding cost over a second - four
# of the six seconds the page took to load. The timeouts keep an
# unreachable Redis from stalling the page instead.
_CONNECT_TIMEOUT_SECONDS = 2
_client: redis.Redis | None = None
_client_url: str | None = None


def _redis() -> redis.Redis:
    """The pooled client for the configured URL, built once. Keyed by the
    URL so that pointing the app at a different Redis - which is what a
    test does - builds a new client rather than reusing the old one."""
    global _client, _client_url
    url = get_settings().redis_url
    if _client is None or _client_url != url:
        _client = redis.Redis.from_url(
            url,
            socket_connect_timeout=_CONNECT_TIMEOUT_SECONDS,
            socket_timeout=_CONNECT_TIMEOUT_SECONDS,
        )
        _client_url = url
    return _client


def reset_client_after_fork() -> None:
    """A pooled connection must not be shared with a forked child (two
    processes on one socket); the child builds its own on next use. Same
    reasoning as db.session.dispose_engine_after_fork."""
    global _client, _client_url
    if _client is not None:
        _client.close()
        _client = None
        _client_url = None


# Celery's default queue, where crawl_one_source tasks wait (the
# dispatcher itself is routed to its own "scheduler" queue - see
# celery_app.py). With the Redis broker a queue is a plain list, so its
# length is the crawl backlog.
CRAWL_QUEUE = "celery"
SCHEDULER_QUEUE = "scheduler"


def crawl_queue_depth() -> int | None:
    """How many crawls are waiting for a worker; None if Redis is down."""
    try:
        return int(_redis().llen(CRAWL_QUEUE))
    except redis.RedisError:
        return None


def record_dispatch_tick() -> None:
    try:
        _redis().set(_KEY, utc_now().isoformat())
    except redis.RedisError as exc:  # never let bookkeeping break a dispatch
        logger.warning("could not record dispatch tick", extra={"error": str(exc)})


def last_dispatch_at() -> datetime | None:
    try:
        raw = _redis().get(_KEY)
    except redis.RedisError:
        return None
    if raw is None:
        return None
    value = raw.decode() if isinstance(raw, bytes) else str(raw)
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def next_dispatch_at(last: datetime | None = None) -> datetime | None:
    """The caller passes `last` when it already read it, so the status
    page doesn't ask Redis for the same timestamp twice."""
    last = last if last is not None else last_dispatch_at()
    if last is None:
        return None
    return last + timedelta(minutes=get_settings().default_poll_minutes)

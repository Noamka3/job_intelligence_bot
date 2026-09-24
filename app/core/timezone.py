"""Timezone helpers.

Every timestamp is stored in the database as UTC. Conversion to the
configurable display timezone (default Asia/Jerusalem) happens only at the
presentation layer (API responses / dashboard), never in storage or in
business logic comparisons.
"""

from __future__ import annotations

from datetime import UTC, datetime
from zoneinfo import ZoneInfo

from app.core.config import get_settings


def utc_now() -> datetime:
    return datetime.now(UTC)


def interpret_naive_as_default_timezone(value: datetime) -> datetime:
    """For user-supplied timestamps (API query params): a value without an
    offset is read in the configured display timezone - what a person
    typing "2026-09-15T09:00" means - never in whatever the DB session's
    TimeZone happens to be, which is where a naive value would otherwise
    get interpreted by Postgres."""
    if value.tzinfo is not None:
        return value
    return value.replace(tzinfo=ZoneInfo(get_settings().default_timezone))

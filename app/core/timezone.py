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


def to_display_timezone(value: datetime, timezone_name: str | None = None) -> datetime:
    tz_name = timezone_name or get_settings().default_timezone
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.astimezone(ZoneInfo(tz_name))

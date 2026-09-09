"""Small helpers shared by more than one adapter."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from app.models.enums import EmploymentType


def parse_timestamp(value: Any) -> datetime | None:
    """Parses either an ISO-8601 string (Greenhouse, Lever, Ashby) or a
    Unix epoch in seconds or milliseconds (Comeet) into a UTC datetime.
    Returns None rather than raising - a source with an odd/missing
    timestamp must not break ingestion.
    """
    if value is None or value == "":
        return None

    if isinstance(value, int | float):
        seconds = value / 1000 if value > 10_000_000_000 else value
        try:
            return datetime.fromtimestamp(seconds, tz=UTC)
        except (OverflowError, OSError, ValueError):
            return None

    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None


def first_name(items: list[dict[str, Any]] | None) -> str | None:
    if not items:
        return None
    name = items[0].get("name")
    return str(name) if name else None


def map_employment_type(value: str | None) -> EmploymentType:
    """Maps an ATS's free-text commitment/employment field (Lever, Ashby,
    Comeet all use slightly different wording for the same handful of
    concepts) onto our fixed enum.
    """
    if not value:
        return EmploymentType.UNKNOWN
    lowered = value.lower()
    if "intern" in lowered:
        return EmploymentType.INTERNSHIP
    if "part" in lowered:
        return EmploymentType.PART_TIME
    if "contract" in lowered or "temp" in lowered or "freelance" in lowered:
        return EmploymentType.CONTRACT
    if "full" in lowered:
        return EmploymentType.FULL_TIME
    return EmploymentType.UNKNOWN

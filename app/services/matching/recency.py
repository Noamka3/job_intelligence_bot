"""Recency scoring: a fresh job gets a small ranking advantage,
but recency must never dominate relevance - hence a floor well above 0
rather than a hard cutoff, and a small weight in the overall composite
(see scoring.py).
"""

from __future__ import annotations

from datetime import datetime

from app.core.timezone import utc_now


def score_recency(published_or_first_seen_at: datetime | None) -> tuple[float, str]:
    if published_or_first_seen_at is None:
        return 0.5, "no publish/discovery date available"

    age = utc_now() - published_or_first_seen_at
    hours = age.total_seconds() / 3600

    if hours < 24:
        return 1.0, "posted within the last 24 hours"
    if hours < 24 * 3:
        return 0.85, "posted within the last 3 days"
    if hours < 24 * 7:
        return 0.65, "posted within the last week"
    if hours < 24 * 14:
        return 0.4, "posted 1-2 weeks ago"
    if hours < 24 * 30:
        return 0.2, "posted 2-4 weeks ago"
    return 0.1, "posted over a month ago"

from __future__ import annotations

from datetime import timedelta

from app.core.timezone import utc_now
from app.services.matching.recency import score_recency


def test_score_recency_none_is_neutral() -> None:
    score, _ = score_recency(None)
    assert score == 0.5


def test_score_recency_decays_with_age() -> None:
    now = utc_now()
    fresh, _ = score_recency(now - timedelta(hours=1))
    three_days, _ = score_recency(now - timedelta(days=2))
    one_week, _ = score_recency(now - timedelta(days=6))
    two_weeks, _ = score_recency(now - timedelta(days=10))
    old, _ = score_recency(now - timedelta(days=60))

    assert fresh > three_days > one_week > two_weeks > old
    assert fresh == 1.0
    # Recency must never dominate relevance (spec §27) - even a very old
    # job keeps a non-zero floor rather than being zeroed out.
    assert old > 0.0

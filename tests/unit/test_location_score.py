from __future__ import annotations

from app.models.enums import RemoteType
from app.services.matching.location_score import score_location


def test_remote_scores_high_regardless_of_country() -> None:
    score, _ = score_location(None, "United States", RemoteType.REMOTE)
    assert score == 1.0


def test_israel_scores_high() -> None:
    score, _ = score_location("Israel", "Tel Aviv", RemoteType.ONSITE)
    assert score == 1.0


def test_confidently_foreign_scores_low() -> None:
    score, _ = score_location(None, "Boston, MA", RemoteType.ONSITE)
    assert score <= 0.2


def test_unrecognized_location_is_neutral_not_excluded() -> None:
    score, _ = score_location(None, "Some Unrecognized Town", RemoteType.ONSITE)
    assert score == 0.5

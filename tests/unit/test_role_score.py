from __future__ import annotations

from app.models.target_role import TargetRole
from app.services.matching.role_score import score_role


def _role(**overrides: object) -> TargetRole:
    defaults: dict[str, object] = {
        "canonical_name": "Junior Software Engineer",
        "aliases": ["Software Engineer I", "Associate Software Engineer"],
        "positive_keywords": ["python", "backend"],
        "negative_keywords": ["sales", "marketing"],
        "preferred_skills": [],
    }
    defaults.update(overrides)
    return TargetRole(**defaults)


def test_exact_canonical_name_match() -> None:
    score, _ = score_role("Junior Software Engineer", None, _role())
    assert score == 1.0


def test_alias_match() -> None:
    score, _ = score_role("Software Engineer I", None, _role())
    assert score == 1.0


def test_negative_keyword_excludes() -> None:
    score, explanation = score_role("Sales Engineer", None, _role())
    assert score <= 0.2
    assert "sales" in explanation


def test_positive_keyword_partial_credit() -> None:
    score, _ = score_role("Backend Developer", None, _role())
    assert 0.5 <= score < 1.0


def test_no_signal_is_low_neutral() -> None:
    score, _ = score_role("Warehouse Associate Driver", None, _role())
    assert score == 0.3

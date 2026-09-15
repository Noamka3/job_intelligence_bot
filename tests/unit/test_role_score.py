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


def test_negative_keyword_beats_an_alias_contained_in_the_title() -> None:
    """"Senior Software Engineer" contains the alias "Software Engineer";
    the alias used to short-circuit to 1.0 before negatives were checked."""
    role = _role(aliases=["Software Engineer"], negative_keywords=["senior", "staff"])
    score, explanation = score_role("Senior Software Engineer", None, role)
    assert score <= 0.2
    assert "senior" in explanation


def test_keyword_and_alias_matching_is_whole_word() -> None:
    role = _role(aliases=["Software Engineer"], positive_keywords=["java"])
    javascript_score, _ = score_role("JavaScript Developer", None, role)
    assert javascript_score == 0.3  # "java" must not credit "JavaScript"
    manager_score, _ = score_role("Software Engineering Manager", None, role)
    assert manager_score < 1.0  # "Software Engineer" must not match "Software Engineering"


def test_no_signal_is_low_neutral() -> None:
    score, _ = score_role("Warehouse Associate Driver", None, _role())
    assert score == 0.3

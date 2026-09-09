from __future__ import annotations

import pytest

from app.core.config import Settings
from app.models.candidate_profile import CandidateProfile
from app.models.enums import RemoteType
from app.models.job_posting import JobPosting
from app.models.target_role import TargetRole
from app.services.matching import scoring


def _candidate(**overrides: object) -> CandidateProfile:
    defaults: dict[str, object] = {
        "version": 1,
        "filename": "cv.pdf",
        "file_hash": "a" * 64,
        "raw_text": "Junior Software Engineer with Python and Docker experience.",
        "normalized_text": "junior software engineer with python and docker experience.",
        "structured_profile": {},
        "embedding": [0.1, 0.2, 0.3],
        "is_active": True,
    }
    defaults.update(overrides)
    return CandidateProfile(**defaults)


def _target_role(**overrides: object) -> TargetRole:
    defaults: dict[str, object] = {
        "canonical_name": "Junior Software Engineer",
        "aliases": ["Software Engineer I"],
        "positive_keywords": [],
        "negative_keywords": [],
        "preferred_skills": [],
        "max_expected_years": 2,
        "embedding": [0.1, 0.2, 0.3],
    }
    defaults.update(overrides)
    return TargetRole(**defaults)


def _job(**overrides: object) -> JobPosting:
    defaults: dict[str, object] = {
        "company_id": 1,
        "career_source_id": 1,
        "external_job_id": "1",
        "title": "Software Engineer I",
        "normalized_title": "software engineer i",
        "location_text": "Tel Aviv",
        "normalized_location": "tel aviv",
        "country": "Israel",
        "remote_type": RemoteType.ONSITE,
        "description": "Python, Docker, REST APIs. 0-2 years of experience.",
        "normalized_description": "python, docker, rest apis. 0-2 years of experience.",
        "required_skills": [],
        "preferred_skills": [],
        "source_url": "https://example.com/jobs/1",
        "content_hash": "b" * 64,
        "embedding": [0.1, 0.2, 0.3],
    }
    defaults.update(overrides)
    return JobPosting(**defaults)


def test_compute_match_perfect_scores_yield_100(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(scoring, "cosine_similarity", lambda a, b: 1.0)
    monkeypatch.setattr(scoring, "score_skills", lambda c, j: (1.0, [], []))
    monkeypatch.setattr(scoring, "score_role", lambda *a: (1.0, "role ok"))
    monkeypatch.setattr(scoring, "score_seniority", lambda *a: (1.0, "junior signal"))
    monkeypatch.setattr(scoring, "score_location", lambda *a: (1.0, "in Israel"))
    monkeypatch.setattr(scoring, "score_recency", lambda *a: (1.0, "fresh"))

    result = scoring.compute_match(_candidate(), _target_role(), _job())

    assert result.final_score == 100.0


def test_compute_match_zero_scores_yield_0(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(scoring, "cosine_similarity", lambda a, b: 0.0)
    monkeypatch.setattr(scoring, "score_skills", lambda c, j: (0.0, [], []))
    monkeypatch.setattr(scoring, "score_role", lambda *a: (0.0, "no match"))
    monkeypatch.setattr(scoring, "score_seniority", lambda *a: (0.0, "too senior"))
    monkeypatch.setattr(scoring, "score_location", lambda *a: (0.0, "not israel"))
    monkeypatch.setattr(scoring, "score_recency", lambda *a: (0.0, "stale"))

    result = scoring.compute_match(_candidate(), _target_role(), _job())

    assert result.final_score == 0.0


def test_compute_match_missing_embeddings_fall_back_to_neutral() -> None:
    candidate = _candidate(embedding=None)
    target_role = _target_role(embedding=None)
    job = _job(embedding=None)

    result = scoring.compute_match(candidate, target_role, job)

    assert result.candidate_semantic_score == 0.5
    assert result.intent_semantic_score == 0.5


def test_compute_match_reports_matched_skills_as_reasons() -> None:
    candidate = _candidate(normalized_text="python docker experience")
    job = _job(normalized_description="requires python and docker")

    result = scoring.compute_match(candidate, _target_role(), job)

    assert "python" in result.reasons
    assert "docker" in result.reasons


def test_invalid_weights_raise_a_clear_error() -> None:
    settings = Settings(
        _env_file=None,
        weight_candidate_semantic=0.5,
        weight_intent_semantic=0.5,
        weight_skills=0.5,
        weight_role=0.1,
        weight_seniority=0.1,
        weight_location=0.1,
        weight_recency=0.1,
    )
    with pytest.raises(scoring.InvalidWeightsError):
        scoring._validate_weights(settings)

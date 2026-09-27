"""Jev's two readers against a canned API: no network, no key. The
transport is TypeSafe's own httpx2 mock, so the SDK's request building
and response parsing run for real."""

from __future__ import annotations

import json
from typing import Any

import httpx2
import pytest
from typesafe_sdk import TypeSafeClient

from app.core.config import get_settings
from app.models.candidate_profile import CandidateProfile
from app.models.job_match import JobMatch
from app.models.job_posting import JobPosting
from app.services.jev import client as jev_client
from app.services.jev import judge_fit, needs_judgement, needs_reading, read_posting

READING_ANSWERS = {
    "role_family": {
        "type": "choice",
        "choice": "software_development",
        "confidence": 0.93,
        "probabilities": {"software_development": 0.95, "qa_automation": 0.03, "other": 0.02},
    },
    "seniority": {
        "type": "choice",
        "choice": "student",
        "confidence": 0.88,
        "probabilities": {"student": 0.9, "junior": 0.08, "mid": 0.02},
    },
    "students_only": {"type": "noul", "noul": 0.94},
    "experience_required": {"type": "noul", "noul": 0.06},
}
FIT_ANSWERS = {
    "would_be_considered": {"type": "noul", "noul": 0.81},
    "skills_coverage": {
        "type": "score",
        "score": 3.2,
        "confidence": 0.7,
        "legend": {"0": "None", "1": "A few", "2": "Half", "3": "Most", "4": "All"},
        "probabilities": {"0": 0.0, "1": 0.05, "2": 0.15, "3": 0.4, "4": 0.4},
    },
    "experience_level": {
        "type": "choice",
        "choice": "matches",
        "confidence": 0.77,
        "probabilities": {"below": 0.1, "matches": 0.85, "above": 0.05},
    },
}


def _fake_api(
    monkeypatch: pytest.MonkeyPatch, answers: dict[str, Any], status: int = 200
) -> list[dict[str, Any]]:
    """Points the module's client at a transport that records each request
    and answers with `answers`."""
    monkeypatch.setattr(get_settings(), "typesafe_api_key", "test-key")
    seen: list[dict[str, Any]] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        seen.append(json.loads(request.content))
        body = {"model": "jev-1.13.0", "usage": {"input_tokens": 900, "output_tokens": 0}}
        return httpx2.Response(status, json={**body, "answers": answers})

    client = TypeSafeClient(api_key="test-key", transport=httpx2.MockTransport(handler))
    monkeypatch.setattr(jev_client, "_client", lambda: client)
    return seen


def _job(**overrides: Any) -> JobPosting:
    fields: dict[str, Any] = {
        "title": "Software Development Student",
        "department": "R&D",
        "location_text": "Tel Aviv",
        "normalized_description": "Student position, 2 days a week. Python, SQL.",
        "content_hash": "a" * 64,
    }
    return JobPosting(**{**fields, **overrides})


def test_read_posting_asks_about_the_text_and_stores_the_typed_answers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen = _fake_api(monkeypatch, READING_ANSWERS)
    job = _job()

    reading = read_posting(job)

    assert reading == {
        "model": "jev-1.13.0",
        "content_hash": "a" * 64,
        "role_family": "software_development",
        "role_family_confidence": 0.93,
        "seniority": "student",
        "seniority_confidence": 0.88,
        "students_only": 0.94,
        "experience_required": 0.06,
    }
    (request,) = seen
    assert request["state"]["title"] == "Software Development Student"
    assert set(request["questions"]) == {
        "role_family",
        "seniority",
        "students_only",
        "experience_required",
    }


def test_a_posting_is_read_once_per_text(monkeypatch: pytest.MonkeyPatch) -> None:
    _fake_api(monkeypatch, READING_ANSWERS)
    job = _job()
    assert needs_reading(job)
    job.jev_reading = read_posting(job)
    assert not needs_reading(job)
    job.content_hash = "b" * 64  # the posting's text changed
    assert needs_reading(job)


def test_judge_fit_sends_resume_and_posting_side_by_side(monkeypatch: pytest.MonkeyPatch) -> None:
    seen = _fake_api(monkeypatch, FIT_ANSWERS)
    candidate = CandidateProfile(normalized_text="B.Sc. Computer Science. Python, React, SQL.")
    job = _job(title="Junior Backend Developer")

    judgement = judge_fit(candidate, job)

    assert judgement == {
        "model": "jev-1.13.0",
        "content_hash": "a" * 64,
        "would_be_considered": 0.81,
        "skills_coverage": 3.2,
        "experience_level": "matches",
        "experience_level_confidence": 0.77,
    }
    (request,) = seen
    assert request["state"]["resume"].startswith("B.Sc.")
    assert request["state"]["job_posting"]["title"] == "Junior Backend Developer"
    match = JobMatch(jev_fit=judgement)
    assert not needs_judgement(match, job)


def test_off_without_a_key_and_none_when_the_api_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(get_settings(), "typesafe_api_key", "")
    assert read_posting(_job()) is None
    assert not needs_reading(_job())

    _fake_api(monkeypatch, {}, status=500)
    assert read_posting(_job()) is None

"""End-to-end validation of spec §43's exact expected-behavior examples,
using real embeddings (the local provider - no network, no Ollama) rather
than mocks. This is the actual acceptance test for the hybrid matching
engine: a job semantically similar to the candidate's background must
still score low if it's the wrong seniority or the wrong role.
"""

from __future__ import annotations

import pytest

from app.models.candidate_profile import CandidateProfile
from app.models.enums import RemoteType
from app.models.job_posting import JobPosting
from app.models.target_role import TargetRole
from app.services.embeddings.local_provider import LocalEmbeddingProvider
from app.services.matching.scoring import compute_match

_CANDIDATE_TEXT = (
    "Noam - aspiring Junior Software Engineer. B.Sc. Computer Science. "
    "Experience with Python, JavaScript, REST APIs, Docker, and SQL from "
    "university projects and a short internship. Comfortable with Git and "
    "basic AWS usage. Looking for an entry-level backend or full-stack role."
)

_TARGET_ROLE_TEXT = (
    "Junior Software Engineer. Software Engineer I. Associate Software Engineer. "
    "Entry Level Software Engineer. Graduate Software Engineer. Junior Backend Engineer. "
    "Keywords: python, javascript, rest api, backend, full stack, entry level."
)


@pytest.fixture(scope="module")
def embedder() -> LocalEmbeddingProvider:
    return LocalEmbeddingProvider()


@pytest.fixture(scope="module")
def candidate(embedder: LocalEmbeddingProvider) -> CandidateProfile:
    return CandidateProfile(
        version=1,
        filename="cv.pdf",
        file_hash="a" * 64,
        raw_text=_CANDIDATE_TEXT,
        normalized_text=_CANDIDATE_TEXT,
        structured_profile={},
        embedding=embedder.embed_one(_CANDIDATE_TEXT),
        is_active=True,
    )


@pytest.fixture(scope="module")
def target_role(embedder: LocalEmbeddingProvider) -> TargetRole:
    return TargetRole(
        canonical_name="Junior Software Engineer",
        aliases=[
            "Software Engineer I",
            "Associate Software Engineer",
            "Entry Level Software Engineer",
            "Graduate Software Engineer",
            "Junior Backend Engineer",
        ],
        positive_keywords=["python", "backend", "full stack", "entry level"],
        negative_keywords=[],
        preferred_skills=["python", "docker", "rest api"],
        max_expected_years=2,
        embedding=embedder.embed_one(_TARGET_ROLE_TEXT),
    )


def _make_job(
    embedder: LocalEmbeddingProvider, *, title: str, body: str, **overrides: object
) -> JobPosting:
    text = f"{title}. {body}"
    defaults: dict[str, object] = {
        "company_id": 1,
        "career_source_id": 1,
        "external_job_id": title,
        "title": title,
        "normalized_title": title.lower(),
        "location_text": "Tel Aviv",
        "normalized_location": "tel aviv",
        "country": "Israel",
        "remote_type": RemoteType.ONSITE,
        "description": body,
        "normalized_description": body,
        "required_skills": [],
        "preferred_skills": [],
        "source_url": f"https://example.com/jobs/{title}",
        "content_hash": "b" * 64,
        "embedding": embedder.embed_one(text),
    }
    defaults.update(overrides)
    return JobPosting(**defaults)


def test_software_engineer_i_scores_very_high(
    embedder: LocalEmbeddingProvider, candidate: CandidateProfile, target_role: TargetRole
) -> None:
    job = _make_job(
        embedder,
        title="Software Engineer I",
        body="Node.js, AWS, REST APIs. 0-2 years of experience required.",
    )
    result = compute_match(candidate, target_role, job)
    assert result.final_score >= 70, result


def test_junior_backend_developer_scores_high(
    embedder: LocalEmbeddingProvider, candidate: CandidateProfile, target_role: TargetRole
) -> None:
    job = _make_job(
        embedder,
        title="Junior Backend Developer",
        body="Python, PostgreSQL, Docker. Great first role for new graduates.",
    )
    result = compute_match(candidate, target_role, job)
    assert result.final_score >= 65, result


def test_senior_backend_engineer_scores_low_despite_semantic_similarity(
    embedder: LocalEmbeddingProvider, candidate: CandidateProfile, target_role: TargetRole
) -> None:
    job = _make_job(
        embedder,
        title="Senior Backend Engineer",
        body="Node.js, Docker, AWS. 7+ years of experience required.",
    )
    result = compute_match(candidate, target_role, job)
    assert result.final_score <= 45, result
    assert result.seniority_score <= 0.2


def test_senior_data_scientist_scores_low_even_with_related_background(
    embedder: LocalEmbeddingProvider, candidate: CandidateProfile, target_role: TargetRole
) -> None:
    job = _make_job(
        embedder,
        title="Senior Data Scientist",
        body="Computer vision, PyTorch, deep learning research. 6+ years required.",
    )
    result = compute_match(candidate, target_role, job)
    assert result.final_score <= 40, result


def test_graduate_platform_engineer_scores_well_without_saying_junior(
    embedder: LocalEmbeddingProvider, candidate: CandidateProfile, target_role: TargetRole
) -> None:
    job = _make_job(
        embedder,
        title="Graduate Platform Engineer",
        body="Linux, AWS, Docker. A great first step for early-career engineers.",
    )
    result = compute_match(candidate, target_role, job)
    assert result.final_score >= 55, result


def test_relative_ordering_matches_seniority_expectations(
    embedder: LocalEmbeddingProvider, candidate: CandidateProfile, target_role: TargetRole
) -> None:
    """The core claim of a *hybrid* matcher: seniority must dominate over
    raw semantic similarity, not the other way around.
    """
    junior = _make_job(
        embedder,
        title="Software Engineer I",
        body="Node.js, AWS, REST APIs. 0-2 years of experience required.",
    )
    senior = _make_job(
        embedder,
        title="Senior Backend Engineer",
        body="Node.js, Docker, AWS. 7+ years of experience required.",
    )
    junior_result = compute_match(candidate, target_role, junior)
    senior_result = compute_match(candidate, target_role, senior)

    assert junior_result.final_score > senior_result.final_score
    # Semantic similarity alone would likely rank these close together
    # (same tech stack, "Engineer" in both titles) - seniority detection
    # is what's supposed to pull them apart.
    assert junior_result.final_score - senior_result.final_score >= 25

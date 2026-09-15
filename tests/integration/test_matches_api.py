from __future__ import annotations

import hashlib

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.models.candidate_profile import CandidateProfile
from app.models.career_source import CareerSource
from app.models.company import Company
from app.models.enums import CareerSourceType, JobStatus
from app.models.job_match import JobMatch
from app.models.job_posting import JobPosting
from app.models.target_role import TargetRole


def _seed(db_session: Session) -> tuple[JobPosting, JobPosting]:
    candidate = CandidateProfile(
        version=1,
        filename="cv.pdf",
        file_hash="a" * 64,
        raw_text="x",
        normalized_text="x",
        structured_profile={},
        is_active=True,
    )
    role = TargetRole(canonical_name="Junior Software Engineer")
    company = Company(name="Acme", normalized_name="acme")
    db_session.add_all([candidate, role, company])
    db_session.flush()
    source = CareerSource(
        company_id=company.id,
        source_type=CareerSourceType.GREENHOUSE,
        source_url="https://job-boards.greenhouse.io/acme",
    )
    db_session.add(source)
    db_session.flush()

    jobs = []
    for external_id, status in (("open", JobStatus.ACTIVE), ("gone", JobStatus.CLOSED)):
        job = JobPosting(
            company_id=company.id,
            career_source_id=source.id,
            external_job_id=external_id,
            title="Junior Software Engineer",
            normalized_title="junior software engineer",
            source_url=f"https://job-boards.greenhouse.io/acme/jobs/{external_id}",
            content_hash=hashlib.sha256(external_id.encode()).hexdigest(),
            status=status,
        )
        db_session.add(job)
        db_session.flush()
        db_session.add(
            JobMatch(
                candidate_profile_id=candidate.id,
                target_role_id=role.id,
                job_id=job.id,
                candidate_semantic_score=0.9,
                intent_semantic_score=0.9,
                skill_score=0.9,
                role_score=1.0,
                seniority_score=1.0,
                location_score=1.0,
                recency_score=1.0,
                final_score=95.0,
                reasons=[],
                concerns=[],
            )
        )
        jobs.append(job)
    db_session.commit()
    return jobs[0], jobs[1]


def test_top_matches_never_lists_closed_jobs(api_client: TestClient, db_session: Session) -> None:
    """JobMatch rows are kept when a job closes (history) - a 95-point
    match for a job that vanished from the ATS used to stay at #1 forever."""
    open_job, closed_job = _seed(db_session)

    response = api_client.get("/matches/top")

    assert response.status_code == 200
    job_ids = {match["job_id"] for match in response.json()}
    assert open_job.id in job_ids
    assert closed_job.id not in job_ids


def test_top_matches_rejects_out_of_range_params(api_client: TestClient) -> None:
    assert api_client.get("/matches/top", params={"limit": 0}).status_code == 422
    assert api_client.get("/matches/top", params={"min_score": 101}).status_code == 422

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
from app.services.jobs.location import classify_country


def _seed(db_session: Session) -> tuple[JobPosting, JobPosting, JobPosting]:
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
    for external_id, status, location in (
        ("open", JobStatus.ACTIVE, "Tel Aviv"),
        ("gone", JobStatus.CLOSED, "Tel Aviv"),
        ("abroad", JobStatus.ACTIVE, "Warsaw, Poland"),
    ):
        job = JobPosting(
            company_id=company.id,
            career_source_id=source.id,
            external_job_id=external_id,
            title="Junior Software Engineer",
            normalized_title="junior software engineer",
            location_text=location,
            country=classify_country(location),
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
    return jobs[0], jobs[1], jobs[2]


def test_top_matches_never_lists_closed_jobs(api_client: TestClient, db_session: Session) -> None:
    """JobMatch rows are kept when a job closes (history) - a 95-point
    match for a job that vanished from the ATS used to stay at #1 forever."""
    open_job, closed_job, _ = _seed(db_session)

    response = api_client.get("/matches/top")

    assert response.status_code == 200
    job_ids = {match["job_id"] for match in response.json()}
    assert open_job.id in job_ids
    assert closed_job.id not in job_ids


def test_top_matches_are_israel_only_by_default(
    api_client: TestClient, db_session: Session
) -> None:
    """location_score is a 3% tiebreaker, so a strong match abroad used to
    outrank every Israeli one in the list the user actually reads."""
    open_job, _, abroad_job = _seed(db_session)

    default_ids = {match["job_id"] for match in api_client.get("/matches/top").json()}
    assert open_job.id in default_ids
    assert abroad_job.id not in default_ids

    everything = api_client.get("/matches/top", params={"israel_only": "false"}).json()
    assert abroad_job.id in {match["job_id"] for match in everything}


def test_top_matches_search_dismissal_and_per_job_breakdown(
    api_client: TestClient, db_session: Session
) -> None:
    open_job, _, _ = _seed(db_session)

    by_query = api_client.get("/matches/top", params={"q": "acme"}).json()
    assert open_job.id in {m["job_id"] for m in by_query}
    assert api_client.get("/matches/top", params={"q": "zzz-no-such"}).json() == []
    assert by_query[0]["source_type"] == "greenhouse"
    assert by_query[0]["company_name"] == "Acme"
    assert by_query[0]["last_feedback"] is None

    # Dismissing feedback hides the job by default, but not with hide_dismissed=false.
    api_client.post(f"/jobs/{open_job.id}/feedback", json={"action": "too_senior"})
    assert open_job.id not in {m["job_id"] for m in api_client.get("/matches/top").json()}
    shown = api_client.get("/matches/top", params={"hide_dismissed": "false"}).json()
    assert {m["job_id"]: m["last_feedback"] for m in shown}[open_job.id] == "too_senior"

    breakdown = api_client.get(f"/matches/job/{open_job.id}")
    assert breakdown.status_code == 200
    assert breakdown.json()["seniority_score"] == 1.0
    assert api_client.get("/matches/job/999999").status_code == 404


def test_dashboard_stats_and_root_redirect(api_client: TestClient, db_session: Session) -> None:
    _seed(db_session)

    stats = api_client.get("/dashboard/stats")
    assert stats.status_code == 200
    body = stats.json()
    assert body["active_jobs"] >= 1
    assert body["matches"] >= 2
    assert any(row["source_type"] == "greenhouse" for row in body["by_source_type"])

    root = api_client.get("/", follow_redirects=False)
    assert root.status_code in (302, 307)
    assert root.headers["location"] == "/app/"


def test_top_matches_rejects_out_of_range_params(api_client: TestClient) -> None:
    assert api_client.get("/matches/top", params={"limit": 0}).status_code == 422
    assert api_client.get("/matches/top", params={"min_score": 101}).status_code == 422

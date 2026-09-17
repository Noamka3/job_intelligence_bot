from __future__ import annotations

import hashlib

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.models.career_source import CareerSource
from app.models.company import Company
from app.models.enums import CareerSourceType, JobStatus
from app.models.job_posting import JobPosting


def _job(db_session: Session, external_id: str = "1") -> JobPosting:
    company = Company(name="Acme", normalized_name="acme")
    db_session.add(company)
    db_session.flush()
    source = CareerSource(
        company_id=company.id,
        source_type=CareerSourceType.GREENHOUSE,
        source_url="https://job-boards.greenhouse.io/acme",
    )
    db_session.add(source)
    db_session.flush()
    job = JobPosting(
        company_id=company.id,
        career_source_id=source.id,
        external_job_id=external_id,
        title="Junior Software Engineer",
        normalized_title="junior software engineer",
        source_url=f"https://job-boards.greenhouse.io/acme/jobs/{external_id}",
        content_hash=hashlib.sha256(external_id.encode()).hexdigest(),
        status=JobStatus.ACTIVE,
    )
    db_session.add(job)
    db_session.commit()
    return job


def test_applied_feedback_opens_an_application_once(
    api_client: TestClient, db_session: Session
) -> None:
    job = _job(db_session)

    assert api_client.get("/applications").json() == []
    applied = api_client.post(f"/jobs/{job.id}/feedback", json={"action": "applied"})
    assert applied.status_code == 201
    # A second "applied" (double tap) must not open a second application.
    api_client.post(f"/jobs/{job.id}/feedback", json={"action": "applied"})

    applications = api_client.get("/applications").json()
    mine = [a for a in applications if a["job_id"] == job.id]
    assert len(mine) == 1
    assert mine[0]["status"] == "applied"
    assert mine[0]["company_name"] == "Acme"
    assert mine[0]["job_title"] == "Junior Software Engineer"


def test_application_status_and_notes_can_be_updated(
    api_client: TestClient, db_session: Session
) -> None:
    job = _job(db_session, "2")
    assert api_client.post(f"/applications/{job.id}").status_code == 201

    moved = api_client.patch(
        f"/applications/{job.id}", json={"status": "interview", "notes": "ראיון טכני ביום ג"}
    )
    assert moved.status_code == 200
    assert moved.json()["status"] == "interview"
    assert moved.json()["notes"] == "ראיון טכני ביום ג"

    cleared = api_client.patch(f"/applications/{job.id}", json={"notes": None})
    assert cleared.json()["notes"] is None
    assert cleared.json()["status"] == "interview"  # untouched

    assert api_client.patch(f"/applications/{job.id}", json={"status": None}).status_code == 422
    assert api_client.patch("/applications/999999", json={"status": "offer"}).status_code == 404
    assert api_client.post("/applications/999999").status_code == 404

from __future__ import annotations

import hashlib

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.api.routes import operations
from app.ingestion.adapters.base import JobDetails, JobStub
from app.models.career_source import CareerSource
from app.models.company import Company
from app.models.enums import CareerSourceType, JobStatus
from app.models.job_posting import JobPosting
from app.services.jobs import ingestion


def _seed_job(db_session: Session) -> JobPosting:
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
        external_job_id="1",
        title="Junior Software Engineer",
        normalized_title="junior software engineer",
        source_url="https://job-boards.greenhouse.io/acme/jobs/1",
        content_hash=hashlib.sha256(b"x").hexdigest(),
        status=JobStatus.ACTIVE,
    )
    db_session.add(job)
    db_session.commit()
    return job


def test_list_and_get_job(api_client: TestClient, db_session: Session) -> None:
    job = _seed_job(db_session)

    list_response = api_client.get("/jobs")
    assert list_response.status_code == 200
    assert any(j["id"] == job.id for j in list_response.json())

    detail_response = api_client.get(f"/jobs/{job.id}")
    assert detail_response.status_code == 200
    assert detail_response.json()["title"] == "Junior Software Engineer"


def test_get_missing_job_returns_404(api_client: TestClient) -> None:
    response = api_client.get("/jobs/999999")
    assert response.status_code == 404


def test_list_jobs_filters_by_status(api_client: TestClient, db_session: Session) -> None:
    job = _seed_job(db_session)
    job.status = JobStatus.CLOSED
    db_session.commit()

    active_response = api_client.get("/jobs", params={"status": "active"})
    assert active_response.status_code == 200
    assert all(j["id"] != job.id for j in active_response.json())

    closed_response = api_client.get("/jobs", params={"status": "closed"})
    assert any(j["id"] == job.id for j in closed_response.json())


def test_crawl_now_endpoint_runs_due_sources(
    api_client: TestClient, db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    company = Company(name="Acme", normalized_name="acme")
    db_session.add(company)
    db_session.flush()
    source = CareerSource(
        company_id=company.id,
        source_type=CareerSourceType.GREENHOUSE,
        source_url="https://job-boards.greenhouse.io/acme",
        enabled=True,
    )
    db_session.add(source)
    db_session.commit()

    class _FakeAdapter:
        def list_jobs(self, source: CareerSource) -> list[JobStub]:
            return [
                JobStub(external_job_id="1", title="Junior Engineer", source_url=source.source_url)
            ]

        def fetch_job(self, source: CareerSource, stub: JobStub) -> JobDetails:
            return JobDetails(
                external_job_id=stub.external_job_id,
                title=stub.title,
                source_url=stub.source_url,
            )

    monkeypatch.setattr(ingestion, "get_adapter", lambda _: _FakeAdapter())
    # Scope to just this test's source: the dev DB can have other real
    # CareerSources that are also genuinely due by wall-clock time (e.g.
    # from a manual crawl-now run earlier), and this test's _FakeAdapter
    # must never be used against those - it would write bogus jobs onto
    # real companies. get_due_sources itself is covered separately in
    # tests/integration/test_ingestion.py.
    monkeypatch.setattr(operations, "get_due_sources", lambda _db: [source])

    response = api_client.post("/operations/crawl-now")

    assert response.status_code == 200
    body = response.json()
    assert body["sources_attempted"] == 1
    assert body["succeeded"] == 1

    jobs_response = api_client.get("/jobs", params={"career_source_id": source.id})
    assert len(jobs_response.json()) == 1

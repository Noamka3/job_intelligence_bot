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
from app.services.jobs.location import classify_country


def _seed_job(
    db_session: Session,
    *,
    external_job_id: str = "1",
    location_text: str | None = None,
    country: str | None = None,
) -> JobPosting:
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
        external_job_id=external_job_id,
        title="Junior Software Engineer",
        normalized_title="junior software engineer",
        location_text=location_text,
        country=country,
        source_url=f"https://job-boards.greenhouse.io/acme/jobs/{external_job_id}",
        content_hash=hashlib.sha256(b"x").hexdigest(),
        status=JobStatus.ACTIVE,
    )
    db_session.add(job)
    db_session.commit()
    return job


def _seed_jobs_by_location(db_session: Session, locations: dict[str, str | None]) -> dict[str, int]:
    """One company, one source, one job per (external id -> location)."""
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
    ids: dict[str, int] = {}
    for external_id, location in locations.items():
        job = JobPosting(
            company_id=company.id,
            career_source_id=source.id,
            external_job_id=external_id,
            title="Engineer",
            normalized_title="engineer",
            location_text=location,
            country=classify_country(location),
            source_url=f"https://job-boards.greenhouse.io/acme/jobs/{external_id}",
            content_hash=hashlib.sha256(b"x").hexdigest(),
            status=JobStatus.ACTIVE,
        )
        db_session.add(job)
        db_session.flush()
        ids[external_id] = job.id
    db_session.commit()
    return ids


def test_israel_only_filter_matches_the_python_classifier(
    api_client: TestClient, db_session: Session
) -> None:
    """The SQL filter used to be a bare substring exclusion: "usa" hid
    "JerUSAlem", and "Tel Aviv / New York" (classified Israel at ingest)
    was dropped for containing "new york"."""
    ids = _seed_jobs_by_location(
        db_session,
        {
            "jerusalem": "Jerusalem, Israel",
            "mixed": "Tel Aviv / New York",
            "unknown": "Some Unrecognized Town",
            "none": None,
            "lodz": "Lodz, Poland",
            "usa": "Boston, MA, USA",
        },
    )

    response = api_client.get("/jobs")
    assert response.status_code == 200
    returned = {job["id"] for job in response.json()}

    assert ids["jerusalem"] in returned
    assert ids["mixed"] in returned
    assert ids["unknown"] in returned
    assert ids["none"] in returned
    assert ids["lodz"] not in returned
    assert ids["usa"] not in returned

    unfiltered = api_client.get("/jobs", params={"israel_only": "false"}).json()
    assert ids["lodz"] in {job["id"] for job in unfiltered}


def test_list_jobs_rejects_out_of_range_limit(api_client: TestClient) -> None:
    assert api_client.get("/jobs", params={"limit": 0}).status_code == 422
    assert api_client.get("/jobs", params={"limit": -1}).status_code == 422
    assert api_client.get("/jobs", params={"limit": 501}).status_code == 422


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


def test_list_jobs_filters_by_title_keyword(api_client: TestClient, db_session: Session) -> None:
    job = _seed_job(db_session)  # title: "Junior Software Engineer"

    match_response = api_client.get("/jobs", params={"title": "junior"})
    assert any(j["id"] == job.id for j in match_response.json())

    case_insensitive_response = api_client.get("/jobs", params={"title": "JUNIOR"})
    assert any(j["id"] == job.id for j in case_insensitive_response.json())

    no_match_response = api_client.get("/jobs", params={"title": "senior"})
    assert all(j["id"] != job.id for j in no_match_response.json())


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

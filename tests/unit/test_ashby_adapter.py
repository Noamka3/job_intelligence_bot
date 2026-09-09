from __future__ import annotations

import httpx
import respx

from app.ingestion.adapters.ashby import AshbyAdapter
from app.models.career_source import CareerSource
from app.models.enums import CareerSourceType, EmploymentType

_SOURCE = CareerSource(
    source_type=CareerSourceType.ASHBY,
    source_url="https://jobs.ashbyhq.com/acme",
    external_identifier="acme",
)

_JOB = {
    "id": "job-1",
    "title": "Junior Software Engineer",
    "department": "Engineering",
    "location": "Remote",
    "employmentType": "FullTime",
    "descriptionPlain": "Build our platform.",
    "jobUrl": "https://jobs.ashbyhq.com/acme/job-1",
    "applyUrl": "https://jobs.ashbyhq.com/acme/job-1/apply",
    "publishedAt": "2024-01-01T00:00:00.000Z",
}


@respx.mock
def test_list_jobs_parses_board() -> None:
    respx.get(
        "https://api.ashbyhq.com/posting-api/job-board/acme", params={"includeCompensation": "true"}
    ).mock(return_value=httpx.Response(200, json={"jobs": [_JOB]}))

    stubs = AshbyAdapter().list_jobs(_SOURCE)

    assert len(stubs) == 1
    assert stubs[0].external_job_id == "job-1"
    assert stubs[0].raw == _JOB


@respx.mock
@respx.mock
def test_fetch_job_uses_cached_raw() -> None:
    stub = AshbyAdapter()._to_stub(_JOB, _SOURCE)  # noqa: SLF001 - test-only access

    details = AshbyAdapter().fetch_job(_SOURCE, stub)

    assert details.title == "Junior Software Engineer"
    assert details.employment_type == EmploymentType.FULL_TIME
    assert details.description == "Build our platform."

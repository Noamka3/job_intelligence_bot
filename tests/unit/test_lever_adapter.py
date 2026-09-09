from __future__ import annotations

import httpx
import respx

from app.ingestion.adapters.lever import LeverAdapter
from app.models.career_source import CareerSource
from app.models.enums import CareerSourceType, EmploymentType

_SOURCE = CareerSource(
    source_type=CareerSourceType.LEVER,
    source_url="https://jobs.lever.co/acme",
    external_identifier="acme",
)

_POSTING = {
    "id": "abc-123",
    "text": "Junior Backend Engineer",
    "categories": {"team": "Engineering", "location": "Tel Aviv", "commitment": "Full-time"},
    "hostedUrl": "https://jobs.lever.co/acme/abc-123",
    "applyUrl": "https://jobs.lever.co/acme/abc-123/apply",
    "createdAt": 1700000000000,
    "descriptionPlain": "Join our backend team.",
    "lists": [{"text": "Requirements", "content": "<ul><li>Python</li></ul>"}],
}


@respx.mock
def test_list_jobs_captures_full_posting_in_raw() -> None:
    respx.get("https://api.lever.co/v0/postings/acme", params={"mode": "json"}).mock(
        return_value=httpx.Response(200, json=[_POSTING])
    )

    stubs = LeverAdapter().list_jobs(_SOURCE)

    assert len(stubs) == 1
    assert stubs[0].external_job_id == "abc-123"
    assert stubs[0].raw == _POSTING


@respx.mock
def test_fetch_job_uses_cached_raw_without_a_second_network_call() -> None:
    # No respx route registered at all - if fetch_job tried to hit the
    # network instead of reusing stub.raw, respx would raise on the
    # unmocked call and fail this test.
    stub = LeverAdapter()._to_stub(_POSTING, _SOURCE)  # noqa: SLF001 - test-only access

    details = LeverAdapter().fetch_job(_SOURCE, stub)

    assert details.title == "Junior Backend Engineer"
    assert details.department == "Engineering"
    assert details.employment_type == EmploymentType.FULL_TIME
    assert details.description is not None
    assert "Join our backend team." in details.description
    assert "Requirements" in details.description

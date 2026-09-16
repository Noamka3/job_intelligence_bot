from __future__ import annotations

import httpx
import respx

from app.ingestion.adapters.workable import WorkableAdapter
from app.models.career_source import CareerSource
from app.models.enums import CareerSourceType, EmploymentType, RemoteType

_SOURCE = CareerSource(
    source_type=CareerSourceType.WORKABLE,
    source_url="https://apply.workable.com/acme/",
    external_identifier="acme",
)

# Shape copied from a real widget response (Humanz).
_WIDGET_JOB = {
    "title": "Account Director",
    "shortcode": "FCCB4FAF39",
    "employment_type": "Contract",
    "telecommuting": False,
    "department": "South Africa Offices",
    "url": "https://apply.workable.com/j/FCCB4FAF39",
    "application_url": "https://apply.workable.com/j/FCCB4FAF39/apply",
    "published_on": "2026-04-13",
    "created_at": "2026-04-02",
    "country": "South Africa",
    "city": "Sandton",
}


@respx.mock
def test_list_jobs_dedupes_the_one_row_per_location_widget_output() -> None:
    respx.get("https://apply.workable.com/api/v1/widget/accounts/acme").mock(
        return_value=httpx.Response(
            200,
            json={
                "name": "Acme",
                "jobs": [
                    _WIDGET_JOB,
                    {**_WIDGET_JOB, "city": "Tel Aviv", "country": "Israel"},
                    {**_WIDGET_JOB, "shortcode": "OTHER1", "title": "QA Engineer"},
                ],
            },
        )
    )

    stubs = WorkableAdapter().list_jobs(_SOURCE)

    assert [s.external_job_id for s in stubs] == ["FCCB4FAF39", "OTHER1"]
    assert stubs[0].location_text == "Sandton, South Africa"
    assert stubs[0].apply_url == "https://apply.workable.com/j/FCCB4FAF39/apply"
    assert stubs[0].source_updated_at is None


@respx.mock
def test_fetch_job_maps_v2_detail() -> None:
    respx.get("https://apply.workable.com/api/v2/accounts/acme/jobs/FCCB4FAF39").mock(
        return_value=httpx.Response(
            200,
            json={
                "title": "Account Director",
                "description": "<p>Lead the team.</p>",
                "requirements": "<ul><li>5+ years' experience</li></ul>",
                "benefits": "<p>Hybrid work.</p>",
                "location": {"country": "Israel", "countryCode": "IL", "city": "Tel Aviv"},
                "department": ["Sales"],
                "remote": False,
                "workplace": "hybrid",
                "state": "published",
            },
        )
    )
    stub = WorkableAdapter()._to_stub(_WIDGET_JOB, _SOURCE)  # noqa: SLF001 - test-only access

    details = WorkableAdapter().fetch_job(_SOURCE, stub)

    assert details.description == "Lead the team.\n\nBenefits\nHybrid work."
    assert details.qualifications == "5+ years' experience"
    assert details.location_text == "Tel Aviv, Israel"
    assert details.department == "Sales"
    assert details.remote_type == RemoteType.HYBRID
    assert details.employment_type == EmploymentType.CONTRACT
    assert details.source_published_at is not None

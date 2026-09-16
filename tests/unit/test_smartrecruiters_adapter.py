from __future__ import annotations

import httpx
import respx

from app.ingestion.adapters.smartrecruiters import SmartRecruitersAdapter
from app.models.career_source import CareerSource
from app.models.enums import CareerSourceType, EmploymentType, RemoteType

_SOURCE = CareerSource(
    source_type=CareerSourceType.SMARTRECRUITERS,
    source_url="https://jobs.smartrecruiters.com/acme",
    external_identifier="acme",
)
_LIST = "https://api.smartrecruiters.com/v1/companies/acme/postings"


def _posting(i: int) -> dict[str, object]:
    # Shape copied from a live Posting API response.
    return {
        "id": f"74400014845465{i}",
        "name": f"Data Operations Consultant {i} ",
        "uuid": "f4a3d5b9",
        "releasedDate": "2026-09-09T09:43:26.403Z",
        "location": {
            "city": "Tel Aviv",
            "region": "",
            "country": "il",
            "remote": False,
            "hybrid": True,
            "fullLocation": "Tel Aviv, Israel",
        },
        "department": {"id": "5408931", "label": "Technical Services"},
        "typeOfEmployment": {"id": "contract", "label": "Contract"},
    }


@respx.mock
def test_list_jobs_paginates_by_offset_until_total_found() -> None:
    route = respx.get(_LIST)
    route.side_effect = [
        httpx.Response(
            200,
            json={
                "offset": 0,
                "limit": 100,
                "totalFound": 3,
                "content": [_posting(1), _posting(2)],
            },
        ),
        httpx.Response(
            200, json={"offset": 2, "limit": 100, "totalFound": 3, "content": [_posting(3)]}
        ),
    ]

    stubs = SmartRecruitersAdapter().list_jobs(_SOURCE)

    assert [s.external_job_id for s in stubs] == [
        "744000148454651",
        "744000148454652",
        "744000148454653",
    ]
    assert stubs[0].title == "Data Operations Consultant 1"
    assert stubs[0].location_text == "Tel Aviv, Israel"
    assert [call.request.url.params["offset"] for call in route.calls] == ["0", "2"]


@respx.mock
def test_fetch_job_splits_job_ad_sections() -> None:
    respx.get(f"{_LIST}/744000148454651").mock(
        return_value=httpx.Response(
            200,
            json={
                **_posting(1),
                "jobAd": {
                    "sections": {
                        "companyDescription": {
                            "title": "Company Description",
                            "text": "<p>We are Acme.</p>",
                        },
                        "jobDescription": {
                            "title": "Job Description",
                            "text": "<p>Run data ops.</p>",
                        },
                        "qualifications": {
                            "title": "Qualifications",
                            "text": "<p>2 years of SQL.</p>",
                        },
                        "additionalInformation": {
                            "title": "Additional Information",
                            "text": "<p>Hybrid.</p>",
                        },
                    }
                },
                "applyUrl": "https://jobs.smartrecruiters.com/acme/744000148454651-x?oga=true",
                "postingUrl": "https://jobs.smartrecruiters.com/acme/744000148454651-x",
            },
        )
    )
    stub = SmartRecruitersAdapter()._to_stub(_posting(1), _SOURCE)  # noqa: SLF001

    details = SmartRecruitersAdapter().fetch_job(_SOURCE, stub)

    assert details.description == "We are Acme.\n\nRun data ops.\n\nHybrid."
    assert details.qualifications == "2 years of SQL."
    assert details.department == "Technical Services"
    assert details.employment_type == EmploymentType.CONTRACT
    assert details.remote_type == RemoteType.HYBRID
    assert details.source_url == "https://jobs.smartrecruiters.com/acme/744000148454651-x"
    assert details.source_published_at is not None

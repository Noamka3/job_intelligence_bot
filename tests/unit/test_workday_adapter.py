from __future__ import annotations

import json

import httpx
import respx

from app.core.config import get_settings
from app.ingestion.adapters.workday import WorkdayAdapter, _country_facets
from app.models.career_source import CareerSource
from app.models.enums import CareerSourceType, RemoteType

_SOURCE = CareerSource(
    source_type=CareerSourceType.WORKDAY,
    source_url="https://acme.wd1.myworkdayjobs.com/External",
    external_identifier="acme/External",
)
_CXS = "https://acme.wd1.myworkdayjobs.com/wday/cxs/acme/External"

# Facet shapes copied from the real Intel / Flex / Medtronic responses.
_INTEL_STYLE_FACETS = [
    {"facetParameter": "jobFamilyGroup", "values": [{"descriptor": "Engineering", "id": "x"}]},
    {
        "facetParameter": "locationMainGroup",
        "values": [
            {
                "facetParameter": "locations",
                "descriptor": "Locations",
                "values": [
                    {"descriptor": "Israel, Haifa", "id": "il-haifa", "count": 16},
                    {"descriptor": "Israel, Petah-Tikva", "id": "il-pt", "count": 7},
                    {"descriptor": "USA, Oregon", "id": "us-or", "count": 200},
                ],
            }
        ],
    },
]
_MEDTRONIC_STYLE_FACETS = [
    {
        "facetParameter": "locationMainGroup",
        "values": [
            {
                "facetParameter": "locationCountry",
                "descriptor": "Location Country/Region",
                "values": [
                    {"descriptor": "Israel", "id": "il-country", "count": 25},
                    {"descriptor": "Argentina", "id": "ar", "count": 1},
                ],
            },
            {
                "facetParameter": "locations",
                "values": [{"descriptor": "Israel, Caesarea", "id": "il-caesarea"}],
            },
        ],
    },
]


def test_country_facets_discovers_city_level_values_when_no_country_facet_exists() -> None:
    assert _country_facets(_INTEL_STYLE_FACETS, "Israel") == {"locations": ["il-haifa", "il-pt"]}


def test_country_facets_prefers_the_country_level_facet() -> None:
    assert _country_facets(_MEDTRONIC_STYLE_FACETS, "Israel") == {"locationCountry": ["il-country"]}


def test_country_facets_is_empty_without_a_target_country() -> None:
    assert _country_facets(_MEDTRONIC_STYLE_FACETS, "") == {}


def _posting(req: str, title: str) -> dict[str, object]:
    return {
        "title": title,
        "externalPath": f"/job/Haifa-Israel/{title.replace(' ', '-')}_{req}",
        "locationsText": "Haifa, Israel",
        "postedOn": "Posted Today",
        "bulletFields": [req],
    }


@respx.mock
def test_list_jobs_applies_the_discovered_facet_and_paginates(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setattr(get_settings(), "target_country", "Israel")
    requests: list[dict[str, object]] = []

    def respond(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        requests.append(body)
        if not body["appliedFacets"]:
            return httpx.Response(
                200,
                json={
                    "total": 600,
                    "jobPostings": [_posting("US1", "Elsewhere")],
                    "facets": _INTEL_STYLE_FACETS,
                },
            )
        offset = body["offset"]
        postings = [
            _posting(f"JR{offset + i}", f"Engineer {offset + i}")
            for i in range(20 if offset == 0 else 3)
        ]
        return httpx.Response(200, json={"total": 23, "jobPostings": postings, "facets": []})

    respx.post(f"{_CXS}/jobs").mock(side_effect=respond)

    stubs = WorkdayAdapter().list_jobs(_SOURCE)

    assert len(stubs) == 23
    assert stubs[0].external_job_id == "JR0"
    assert (
        stubs[0].source_url
        == "https://acme.wd1.myworkdayjobs.com/External/job/Haifa-Israel/Engineer-0_JR0"
    )
    assert stubs[0].source_updated_at is None
    # discovery call (no facets), then filtered page 1 and page 2
    assert [r["appliedFacets"] for r in requests] == [
        {},
        {"locations": ["il-haifa", "il-pt"]},
        {"locations": ["il-haifa", "il-pt"]},
    ]
    assert [r["offset"] for r in requests] == [0, 0, 20]


@respx.mock
def test_fetch_job_reads_job_posting_info() -> None:
    stub = WorkdayAdapter()._to_stub(  # noqa: SLF001 - test-only access
        _posting("R76497-1", "Senior Mechanical Design Engineer"),
        "https://acme.wd1.myworkdayjobs.com/External",
    )
    assert stub.external_job_id == "R76497-1"
    respx.get(f"{_CXS}/job/Haifa-Israel/Senior-Mechanical-Design-Engineer_R76497-1").mock(
        return_value=httpx.Response(
            200,
            json={
                "jobPostingInfo": {
                    "title": "Senior Mechanical Design Engineer",
                    "jobDescription": "<p>Careers that change lives.</p>",
                    "location": "Caesarea, Haifa, Israel",
                    "additionalLocations": ["Yokneam, Israel"],
                    "startDate": "2026-09-14",
                    "timeType": "Full time",
                    "remoteType": {"descriptor": "Hybrid"},
                    "externalUrl": "https://acme.wd1.myworkdayjobs.com/External/job/x_R76497-1",
                    "country": {"descriptor": "Israel", "id": "084562884af243748dad7c84c304d89a"},
                }
            },
        )
    )

    details = WorkdayAdapter().fetch_job(_SOURCE, stub)

    assert details.description == "Careers that change lives."
    assert details.location_text == "Caesarea, Haifa, Israel, Yokneam, Israel"
    assert details.remote_type == RemoteType.HYBRID
    assert details.employment_type.value == "full_time"
    assert details.source_published_at is not None
    assert details.source_published_at.tzinfo is not None
    assert details.apply_url == "https://acme.wd1.myworkdayjobs.com/External/job/x_R76497-1"

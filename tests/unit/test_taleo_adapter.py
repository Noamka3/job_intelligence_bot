from __future__ import annotations

import json
from typing import Any
from urllib.parse import quote

import httpx
import respx

from app.ingestion.adapters.taleo import TaleoAdapter, _clean_location, _fill_list_fields
from app.models.career_source import CareerSource
from app.models.enums import CareerSourceType

_SOURCE = CareerSource(
    source_type=CareerSourceType.TALEO,
    source_url="https://acme.taleo.net/careersection/ex/moresearch.ftl",
    external_identifier="acme",
)
_SEARCH = "https://acme.taleo.net/careersection/rest/jobboard/searchjobs"


def _requisition(contest: str, title: str, location: str) -> dict[str, object]:
    # Shape copied from the real Radware response.
    return {
        "jobId": "54295",
        "contestNo": contest,
        "column": [title, json.dumps([location]), "Sep 14, 2026"],
        "linkedColumn": 0,
        "locationsColumns": [1],
    }


@respx.mock
def test_list_jobs_sends_timezone_header_filters_by_country_and_paginates() -> None:
    bodies: list[dict[str, Any]] = []

    def respond(request: httpx.Request) -> httpx.Response:
        assert request.headers.get("tzname")  # the server 500s without it
        body = json.loads(request.content)
        bodies.append(body)
        location_filter = body["filterSelectionParam"]["searchFilterSelections"][1][
            "selectedValues"
        ]
        if not location_filter:
            return httpx.Response(
                200,
                json={
                    "requisitionList": [_requisition("1", "Sales, Nordic", "SE-Sweden-Stockholm")],
                    "pagingData": {"currentPageNo": 1, "pageSize": 25, "totalCount": 40},
                    "facetResults": [
                        {
                            "id": "LOCATION",
                            "facetValueResults": [
                                {"id": "105010219", "text": "Israel", "quantity": "10", "level": 1},
                                {"id": "2", "text": "Sweden", "quantity": "2", "level": 1},
                            ],
                        }
                    ],
                },
            )
        page = body["pageNo"]
        reqs = [
            _requisition(f"26000{page}{i}", f"Engineer {page}-{i}", "IL-IL-Tel Aviv")
            for i in range(25 if page == 1 else 2)
        ]
        return httpx.Response(
            200,
            json={
                "requisitionList": reqs,
                "pagingData": {"currentPageNo": page, "pageSize": 25, "totalCount": 27},
            },
        )

    respx.post(url__startswith=_SEARCH).mock(side_effect=respond)

    stubs = TaleoAdapter().list_jobs(_SOURCE)

    assert len(stubs) == 27
    assert stubs[0].title == "Engineer 1-0"
    assert stubs[0].location_text == "Tel Aviv, IL"
    assert (
        stubs[0].source_url
        == "https://acme.taleo.net/careersection/ex/jobdetail.ftl?job=2600010&lang=en"
    )
    assert [b["pageNo"] for b in bodies] == [1, 1, 2]
    assert bodies[1]["filterSelectionParam"]["searchFilterSelections"][1]["selectedValues"] == [
        "105010219"
    ]


def test_clean_location_turns_taleo_codes_into_readable_text() -> None:
    assert _clean_location("IL-IL-Tel Aviv") == "Tel Aviv, IL"
    assert _clean_location("US-CA-San Jose") == "San Jose, US"
    assert _clean_location("Remote") == "Remote"


@respx.mock
def test_fetch_job_decodes_the_inline_fill_list_description() -> None:
    description_html = "<p>Radware is a <b>global</b> leader.</p>"
    qualifications_html = "<ul><li>2+ years with Python</li></ul>"
    fields = [""] * 9 + [
        "Backend Developer",
        "26000096",
        "!*!" + quote(description_html),
        "!*!" + quote(qualifications_html),
        "IL-IL-Tel Aviv",
    ]
    js_array = ",".join("'" + f.replace("'", "\\'") + "'" for f in fields)
    html = (
        "<html><script>api.fillList('requisitionDescriptionInterface', "
        f"'descRequisition', [{js_array}]);</script></html>"
    )
    respx.get(
        "https://acme.taleo.net/careersection/ex/jobdetail.ftl",
        params={"job": "26000096", "lang": "en"},
    ).mock(return_value=httpx.Response(200, text=html))
    stub = TaleoAdapter()._to_stub(  # noqa: SLF001
        _requisition("26000096", "Backend Developer", "IL-IL-Tel Aviv"), "acme.taleo.net", "ex"
    )

    details = TaleoAdapter().fetch_job(_SOURCE, stub)

    assert details.title == "Backend Developer"
    assert details.description == "Radware is a global leader."
    assert details.qualifications == "2+ years with Python"
    assert details.location_text == "Tel Aviv, IL"


def test_fill_list_fields_handles_escaped_quotes() -> None:
    html = "api.fillList('requisitionDescriptionInterface', 'descRequisition', ['a','it\\'s','c']);"
    assert _fill_list_fields(html) == ["a", "it's", "c"]

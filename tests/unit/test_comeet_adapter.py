from __future__ import annotations

import httpx
import respx

from app.ingestion.adapters.comeet import ComeetAdapter
from app.models.career_source import CareerSource
from app.models.enums import CareerSourceType

_SOURCE = CareerSource(
    source_type=CareerSourceType.COMEET,
    source_url="https://www.comeet.com/jobs/acme/F1.008",
    external_identifier="F1.008",
)

# Trimmed to the parts the adapter actually parses; shape matches a real
# page fetched live during development (see comeet.py's module docstring).
_COMPANY_PAGE_HTML = """
<html><body>
<script>
var COMPANY_DATA = {"name": "Acme", "company_uid": "F1.008", "token": "abc123token"};
</script>
</body></html>
"""

_POSITION_STUB = {
    "uid": "AC.F64",
    "name": "Junior Backend Engineer",
    "department": "Engineering",
    "location": {"name": "Tel Aviv", "is_remote": False},
    "employment_type": "Full-time",
    "url_active_page": "https://www.comeet.com/jobs/acme/F1.008/junior-backend/AC.F64",
    "time_updated": "2024-01-15T10:00:00Z",
}

_POSITION_DETAILS = {
    **_POSITION_STUB,
    "details": [
        {"name": "Description", "value": "<p>About the role.</p>"},
        {"name": "Responsibilities", "value": "<ul><li>Ship features.</li></ul>"},
        {"name": "Requirements", "value": "<ul><li>Python experience.</li></ul>"},
    ],
}


@respx.mock
def test_list_jobs_resolves_credentials_from_page_then_lists_positions() -> None:
    respx.get("https://www.comeet.com/jobs/acme/F1.008").mock(
        return_value=httpx.Response(200, text=_COMPANY_PAGE_HTML)
    )
    respx.get(
        "https://www.comeet.com/careers-api/2.0/company/F1.008/positions",
        params={"token": "abc123token", "details": "false"},
    ).mock(return_value=httpx.Response(200, json=[_POSITION_STUB]))

    stubs = ComeetAdapter().list_jobs(_SOURCE)

    assert len(stubs) == 1
    assert stubs[0].external_job_id == "AC.F64"
    assert stubs[0].raw == {"company_uid": "F1.008", "token": "abc123token"}


@respx.mock
def test_credentials_are_read_from_a_company_page_embedding_the_js_api() -> None:
    """eToro/Checkmarx-style pages: no COMPANY_DATA, but COMEET.init({...})
    with the token and company-uid - a JS object literal with comments,
    so it can't be json.loads'ed."""
    page = CareerSource(
        source_type=CareerSourceType.COMEET,
        source_url="https://www.etoro.com/about/careers/",
        external_identifier="41.009",
    )
    respx.get("https://www.etoro.com/about/careers/").mock(
        return_value=httpx.Response(
            200,
            text=(
                "<script> window.comeetInit = function() { COMEET.init({ "
                '"token": "14952452466D3DB7B61495240B91", "company-uid": "41.009", '
                '"font-size": "16px", //optional\n "language": "en" }); }</script>'
            ),
        )
    )
    respx.get(
        "https://www.comeet.com/careers-api/2.0/company/41.009/positions",
        params={"token": "14952452466D3DB7B61495240B91", "details": "false"},
    ).mock(return_value=httpx.Response(200, json=[_POSITION_STUB]))

    stubs = ComeetAdapter().list_jobs(page)

    assert len(stubs) == 1
    assert stubs[0].raw == {"company_uid": "41.009", "token": "14952452466D3DB7B61495240B91"}


@respx.mock
def test_fetch_job_splits_description_into_sections() -> None:
    respx.get(
        "https://www.comeet.com/careers-api/2.0/company/F1.008/positions/AC.F64",
        params={"token": "abc123token", "details": "true"},
    ).mock(return_value=httpx.Response(200, json=_POSITION_DETAILS))
    stub = ComeetAdapter()._to_stub(  # noqa: SLF001 - test-only access
        _POSITION_STUB, "F1.008", "abc123token", _SOURCE
    )

    details = ComeetAdapter().fetch_job(_SOURCE, stub)

    assert details.description == "About the role."
    assert details.responsibilities == "Ship features."
    assert details.qualifications == "Python experience."


@respx.mock
def test_fetch_job_keeps_custom_sections_in_the_description() -> None:
    """Comeet postings use company-specific section names ("Advantages",
    "About the team", ...) that used to be dropped entirely, so only a
    fraction of the posting was embedded/matched."""
    respx.get(
        "https://www.comeet.com/careers-api/2.0/company/F1.008/positions/AC.F64",
        params={"token": "abc123token", "details": "true"},
    ).mock(
        return_value=httpx.Response(
            200,
            json={
                **_POSITION_STUB,
                "details": [
                    {"name": "About the team", "value": "<p>We are five.</p>"},
                    {"name": "Requirements", "value": "<p>Python.</p>"},
                    {"name": "Advantages", "value": "<p>Docker.</p>"},
                ],
            },
        )
    )
    stub = ComeetAdapter()._to_stub(  # noqa: SLF001 - test-only access
        _POSITION_STUB, "F1.008", "abc123token", _SOURCE
    )

    details = ComeetAdapter().fetch_job(_SOURCE, stub)

    assert details.description == "About the team\nWe are five.\n\nAdvantages\nDocker."
    assert details.qualifications == "Python."
    assert details.responsibilities is None

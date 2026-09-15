from __future__ import annotations

import httpx
import respx

from app.ingestion.adapters.jsonld import JsonLdAdapter
from app.models.career_source import CareerSource
from app.models.enums import CareerSourceType, EmploymentType

_SOURCE = CareerSource(
    source_type=CareerSourceType.JSONLD,
    source_url="https://careers.example.com/",
    external_identifier=None,
)

_PAGE_HTML = """
<html><head>
<script type="application/ld+json">
{
    "@context": "https://schema.org/",
    "@type": "JobPosting",
    "title": "Junior Software Engineer",
    "description": "<p>Build things with us.</p>",
    "datePosted": "2024-01-10",
    "employmentType": "FULL_TIME",
    "identifier": {"@type": "PropertyValue", "value": "job-42"},
    "jobLocation": {
        "@type": "Place",
        "address": {"addressLocality": "Tel Aviv", "addressCountry": "IL"}
    },
    "url": "https://careers.example.com/jobs/42"
}
</script>
</head></html>
"""


@respx.mock
def test_list_jobs_parses_single_jobposting_block() -> None:
    respx.get("https://careers.example.com/").mock(
        return_value=httpx.Response(200, text=_PAGE_HTML)
    )

    stubs = JsonLdAdapter().list_jobs(_SOURCE)

    assert len(stubs) == 1
    assert stubs[0].external_job_id == "job-42"
    assert stubs[0].title == "Junior Software Engineer"
    assert stubs[0].location_text == "Tel Aviv, IL"
    assert stubs[0].source_url == "https://careers.example.com/jobs/42"


@respx.mock
def test_fetch_job_uses_cached_raw_and_maps_employment_type() -> None:
    stub = JsonLdAdapter()._to_stub(  # noqa: SLF001 - test-only access
        {
            "title": "Junior Software Engineer",
            "description": "<p>Build things with us.</p>",
            "employmentType": "FULL_TIME",
            "identifier": {"value": "job-42"},
        },
        _SOURCE,
    )

    details = JsonLdAdapter().fetch_job(_SOURCE, stub)

    assert details.description == "Build things with us."
    assert details.employment_type == EmploymentType.FULL_TIME


def test_date_only_date_posted_is_timezone_aware() -> None:
    """"datePosted": "2024-01-10" (Google's documented form) used to come
    back naive; comparing it with the aware value stored in Postgres, or
    subtracting it from utc_now() in recency scoring, raised TypeError
    and aborted the whole crawl of a JSON-LD source every tick.
    """
    stub = JsonLdAdapter()._to_stub(  # noqa: SLF001 - test-only access
        {"title": "Role", "datePosted": "2024-01-10", "identifier": {"value": "x"}}, _SOURCE
    )
    details = JsonLdAdapter().fetch_job(_SOURCE, stub)

    assert details.source_published_at is not None
    assert details.source_published_at.tzinfo is not None
    # datePosted never moves after an edit, so it must not stand in for
    # "updated at" - that would hide every later change from the crawler.
    assert details.source_updated_at is None
    assert stub.source_updated_at is None


def test_address_country_object_is_rendered_by_name() -> None:
    stub = JsonLdAdapter()._to_stub(  # noqa: SLF001 - test-only access
        {
            "title": "Role",
            "identifier": {"value": "x"},
            "jobLocation": {
                "address": {
                    "addressLocality": "Tel Aviv",
                    "addressCountry": {"@type": "Country", "name": "Israel"},
                }
            },
        },
        _SOURCE,
    )
    assert stub.location_text == "Tel Aviv, Israel"


def test_postings_without_identifier_or_url_get_distinct_ids() -> None:
    """All such postings used to hash the shared page URL and collapse
    into a single job row."""
    adapter = JsonLdAdapter()
    first = adapter._to_stub({"title": "Role A"}, _SOURCE)  # noqa: SLF001
    second = adapter._to_stub({"title": "Role B"}, _SOURCE)  # noqa: SLF001
    same_as_first = adapter._to_stub({"title": "Role A"}, _SOURCE)  # noqa: SLF001

    assert first.external_job_id != second.external_job_id
    assert first.external_job_id == same_as_first.external_job_id
    assert first.source_url == _SOURCE.source_url


def test_ignores_non_jobposting_jsonld() -> None:
    from app.ingestion.adapters.jsonld import _extract_job_postings

    html = """
    <script type="application/ld+json">{"@type": "Organization", "name": "Acme"}</script>
    """
    assert _extract_job_postings(html) == []


def test_handles_graph_wrapped_and_list_jsonld() -> None:
    from app.ingestion.adapters.jsonld import _extract_job_postings

    html = """
    <script type="application/ld+json">
    {"@graph": [
        {"@type": "Organization", "name": "Acme"},
        {"@type": "JobPosting", "title": "Role A"},
        {"@type": "JobPosting", "title": "Role B"}
    ]}
    </script>
    """
    postings = _extract_job_postings(html)
    assert [p["title"] for p in postings] == ["Role A", "Role B"]


def test_ignores_malformed_jsonld_without_crashing() -> None:
    from app.ingestion.adapters.jsonld import _extract_job_postings

    html = '<script type="application/ld+json">{not valid json</script>'
    assert _extract_job_postings(html) == []

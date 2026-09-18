from __future__ import annotations

from typing import Any

import httpx
import pytest
import respx

from app.ingestion.adapters import wordpress
from app.ingestion.adapters.base import JobDetails, JobStub
from app.ingestion.adapters.generic_html import job_id_for_url
from app.ingestion.adapters.wordpress import WordPressAdapter
from app.models.career_source import CareerSource
from app.models.enums import CareerSourceType

_SOURCE = CareerSource(
    source_type=CareerSourceType.WORDPRESS,
    source_url="https://comblack.example/careers/",
    external_identifier="careers",
)
_POSTS = "https://comblack.example/wp-json/wp/v2/careers"


def _post(number: int, content: str) -> dict[str, Any]:
    return {
        "id": number,
        "link": f"https://comblack.example/careers/position-{number}/",
        "date_gmt": "2026-09-10T09:00:00",
        "modified_gmt": "2026-09-17T16:59:17",
        "title": {"rendered": f"Developer &amp; Tester {number}"},
        "content": {"rendered": content},
    }


@respx.mock
def test_list_jobs_pages_through_the_rest_api(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(wordpress, "_PAGE_SIZE", 1)
    common = {"per_page": "1", "_fields": wordpress._FIELDS}
    respx.get(_POSTS, params={**common, "page": "1"}).mock(
        return_value=httpx.Response(200, json=[_post(1, "<p>Python</p>")])
    )
    respx.get(_POSTS, params={**common, "page": "2"}).mock(
        return_value=httpx.Response(200, json=[])
    )

    stubs = WordPressAdapter().list_jobs(_SOURCE)

    assert len(stubs) == 1
    # The same id the generic reader gives this page, so a site that moves
    # from generic crawling to its REST API keeps its stored jobs.
    assert stubs[0].external_job_id == job_id_for_url(stubs[0].source_url)
    assert stubs[0].title == "Developer & Tester 1"
    assert stubs[0].source_url == "https://comblack.example/careers/position-1/"
    assert stubs[0].source_updated_at is not None


def test_fetch_job_uses_the_posts_text_when_the_api_has_it() -> None:
    adapter = WordPressAdapter()
    stub = adapter._to_stub(_post(1, "<p>Python</p><p>SQL</p>"))  # noqa: SLF001 - test-only access

    details = adapter.fetch_job(_SOURCE, stub)

    assert details.title == "Developer & Tester 1"
    assert details.description == "Python\nSQL"
    assert details.source_published_at is not None
    assert details.source_updated_at == stub.source_updated_at


def test_fetch_job_reads_the_page_when_the_api_has_no_text(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """One renders its jobs from page-builder fields the API doesn't expose."""
    adapter = WordPressAdapter()
    stub = adapter._to_stub(_post(1, ""))  # noqa: SLF001 - test-only access
    from_page = JobDetails(
        external_job_id="1",
        title="Tech Lead",
        description="From the page",
        source_url=stub.source_url,
    )

    class _PageReader:
        def fetch_job(self, source: CareerSource, stub: JobStub) -> JobDetails:
            return from_page

    monkeypatch.setattr(adapter, "_page_reader", _PageReader())

    assert adapter.fetch_job(_SOURCE, stub) is from_page

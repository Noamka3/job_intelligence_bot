from __future__ import annotations

import httpx
import respx

from app.ingestion.adapters.base import JobStub
from app.ingestion.adapters.greenhouse import GreenhouseAdapter
from app.models.career_source import CareerSource
from app.models.enums import CareerSourceType

_SOURCE = CareerSource(
    source_type=CareerSourceType.GREENHOUSE,
    source_url="https://job-boards.greenhouse.io/acme",
    external_identifier="acme",
)


@respx.mock
def test_list_jobs_returns_cheap_stubs() -> None:
    respx.get("https://boards-api.greenhouse.io/v1/boards/acme/jobs").mock(
        return_value=httpx.Response(
            200,
            json={
                "jobs": [
                    {
                        "id": 123,
                        "title": "Software Engineer I",
                        "updated_at": "2024-01-15T10:00:00Z",
                        "location": {"name": "Tel Aviv"},
                        "absolute_url": "https://job-boards.greenhouse.io/acme/jobs/123",
                    }
                ]
            },
        )
    )

    stubs = GreenhouseAdapter().list_jobs(_SOURCE)

    assert len(stubs) == 1
    assert stubs[0].external_job_id == "123"
    assert stubs[0].title == "Software Engineer I"
    assert stubs[0].location_text == "Tel Aviv"
    assert stubs[0].source_updated_at is not None


@respx.mock
def test_fetch_job_returns_full_description() -> None:
    respx.get("https://boards-api.greenhouse.io/v1/boards/acme/jobs/123").mock(
        return_value=httpx.Response(
            200,
            json={
                "id": 123,
                "title": "Software Engineer I",
                "departments": [{"name": "Engineering"}],
                "location": {"name": "Tel Aviv"},
                "content": "<p>Build things.</p>",
                "absolute_url": "https://job-boards.greenhouse.io/acme/jobs/123",
                "updated_at": "2024-01-15T10:00:00Z",
            },
        )
    )
    stub = JobStub(
        external_job_id="123", title="Software Engineer I", source_url=_SOURCE.source_url
    )

    details = GreenhouseAdapter().fetch_job(_SOURCE, stub)

    assert details.department == "Engineering"
    assert details.description == "Build things."


@respx.mock
def test_fetch_job_unescapes_greenhouse_entity_encoded_content() -> None:
    """Greenhouse's real API returns `content` HTML-entity-escaped
    ("&lt;p&gt;..."), verified live against a real board. Before this was
    handled, every Greenhouse description in the database was literal
    markup ("<div class="content-intro"><p>...") and got embedded as such.
    """
    respx.get("https://boards-api.greenhouse.io/v1/boards/acme/jobs/123").mock(
        return_value=httpx.Response(
            200,
            json={
                "id": 123,
                "title": "Software Engineer I",
                "content": (
                    "&lt;div class=&quot;content-intro&quot;&gt;&lt;p&gt;Build things "
                    "&amp; ship them.&lt;/p&gt;&lt;/div&gt;"
                ),
                "absolute_url": "https://job-boards.greenhouse.io/acme/jobs/123",
            },
        )
    )
    stub = JobStub(
        external_job_id="123", title="Software Engineer I", source_url=_SOURCE.source_url
    )

    details = GreenhouseAdapter().fetch_job(_SOURCE, stub)

    assert details.description == "Build things & ship them."


@respx.mock
def test_list_jobs_returns_empty_without_board_token() -> None:
    source = CareerSource(
        source_type=CareerSourceType.GREENHOUSE, source_url="https://x", external_identifier=None
    )
    assert GreenhouseAdapter().list_jobs(source) == []

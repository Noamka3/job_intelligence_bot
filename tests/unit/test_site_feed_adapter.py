from __future__ import annotations

import httpx
import pytest
import respx

from app.ingestion.adapters.site_feed import AmazonFeed, SiteFeedAdapter
from app.models.career_source import CareerSource
from app.models.enums import CareerSourceType, EmploymentType


def _source(feed: str, url: str) -> CareerSource:
    return CareerSource(
        source_type=CareerSourceType.SITE_FEED, source_url=url, external_identifier=feed
    )


@respx.mock
def test_elbit_feed_lists_positions_with_their_page_and_unescaped_text() -> None:
    respx.get("https://elbitsystemscareer.com/cron/jobs.json").mock(
        return_value=httpx.Response(
            200,
            json=[
                {
                    "jobId": 20717,
                    "jobTitle": "AIT System Engineer",
                    "area": "Center",
                    "openDate": "2026-06-23T07:16:00",
                    "updateDate": "2026-09-17T13:55:00",
                    "description": "&lt;div&gt;For our site in Rehovot&lt;/div&gt;",
                    "requirements": "&lt;ul&gt;&lt;li&gt;B.Sc. in Physics&lt;/li&gt;&lt;/ul&gt;",
                    "employmentType": None,
                }
            ],
        )
    )
    adapter = SiteFeedAdapter()
    source = _source("elbit", "https://elbitsystemscareer.com/")

    (stub,) = adapter.list_jobs(source)
    details = adapter.fetch_job(source, stub)

    assert stub.external_job_id == "20717"
    assert stub.source_url == "https://elbitsystemscareer.com/jobs/?jobId=20717"
    assert stub.location_text == "Center, Israel"
    assert stub.source_updated_at is not None
    assert details.description == "For our site in Rehovot"
    assert details.qualifications == "B.Sc. in Physics"
    assert details.source_published_at is not None


@respx.mock
def test_iai_feed_reads_its_terse_keys() -> None:
    respx.get("https://jobs.iai.co.il/wp-content/themes/tyco-wp/assets/json/jobs.json").mock(
        return_value=httpx.Response(
            200,
            json=[
                {
                    "id": 76050151,
                    "tl": "מהנדס/ת תוכנה",
                    "cd": "147227",
                    "ct": "אשדוד",
                    "tp": "משרה מלאה",
                    "dc": "קצת על התפקיד\n\nפיתוח מערכות",
                }
            ],
        )
    )
    adapter = SiteFeedAdapter()
    source = _source("iai", "https://jobs.iai.co.il/jobs/")

    (stub,) = adapter.list_jobs(source)
    details = adapter.fetch_job(source, stub)

    assert stub.source_url == "https://jobs.iai.co.il/job/76050151/"
    assert stub.location_text == "אשדוד, Israel"
    assert details.title == "מהנדס/ת תוכנה"
    assert details.employment_type == EmploymentType.FULL_TIME
    assert details.description == "קצת על התפקיד\n\nפיתוח מערכות"


def _amazon_job(number: int, title: str) -> dict[str, object]:
    return {
        "id_icims": str(number),
        "title": title,
        "city": "Tel Aviv",
        "location": "IL, Tel Aviv",
        "job_path": f"/en/jobs/{number}/x",
        "url_next_step": f"https://account.amazon.jobs/jobs/{number}/apply",
        "posted_date": "September 17, 2026",
        "job_schedule_type": "full-time",
        "is_intern": False,
        "description": "Build <br/>things",
        "basic_qualifications": "- Bachelor's degree",
        "preferred_qualifications": "- Python",
        "job_category": "Software Development",
    }


@respx.mock
def test_amazon_feed_pages_through_the_search_api(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(AmazonFeed, "page_size", 1)
    base = "https://www.amazon.jobs/en/search.json"
    common = {"country": "ISR", "result_limit": "1", "sort": "recent"}
    respx.get(base, params={**common, "offset": "0"}).mock(
        return_value=httpx.Response(200, json={"hits": 2, "jobs": [_amazon_job(1, "SDE I")]})
    )
    respx.get(base, params={**common, "offset": "1"}).mock(
        return_value=httpx.Response(200, json={"hits": 2, "jobs": [_amazon_job(2, "SDE II")]})
    )
    adapter = SiteFeedAdapter()
    source = _source("amazon", "https://www.amazon.jobs/en/search?country=ISR")

    stubs = adapter.list_jobs(source)
    details = adapter.fetch_job(source, stubs[0])

    assert [stub.external_job_id for stub in stubs] == ["1", "2"]
    assert stubs[0].source_url == "https://www.amazon.jobs/en/jobs/1/x"
    assert stubs[0].location_text == "Tel Aviv, Israel"
    assert details.employment_type == EmploymentType.FULL_TIME
    assert details.qualifications == "- Bachelor's degree"
    assert details.description == "Build\nthings\n\nPreferred qualifications\n- Python"
    assert details.source_published_at is not None
    assert details.source_published_at.year == 2026


def test_an_unknown_feed_name_fails_loudly() -> None:
    source = _source("nope", "https://example.com/")
    with pytest.raises(ValueError, match="No site feed named 'nope'"):
        SiteFeedAdapter().list_jobs(source)

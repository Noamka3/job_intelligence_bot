"""Feeds a company's own career site publishes as JSON - what its page
fetches in the browser, so a site whose HTML shows no jobs can still be
read without one. Each feed is a few lines keyed by the name the resolver
maps its hostname to (resolver._SITE_FEED_HOSTS). Verified live:

- Elbit (elbitsystemscareer.com/cron/jobs.json): every open position,
  HTML-escaped description and requirements, area, open/update times;
  a job opens at /jobs/?jobId={jobId} (the link shape Elbit publishes)
- IAI (jobs.iai.co.il/.../jobs.json): terse keys - tl title, dc plain-text
  description, ct city, tp employment type; the job page is /job/{id}/
- Amazon (amazon.jobs/en/search.json): the search API behind the careers
  site, 100 per page, full text in the list; the country comes from the
  sheet URL's own `country=` parameter (ISR)
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, Protocol
from urllib.parse import parse_qs, urlparse

from app.ingestion.adapters._http import get_json
from app.ingestion.adapters._util import map_employment_type, parse_timestamp
from app.ingestion.adapters.base import JobDetails, JobStub
from app.models.career_source import CareerSource
from app.models.enums import EmploymentType
from app.services.jobs.html_text import html_to_text


class SiteFeed(Protocol):
    def list_jobs(self, source: CareerSource) -> list[JobStub]: ...

    def to_details(self, stub: JobStub) -> JobDetails: ...


class ElbitFeed:
    url = "https://elbitsystemscareer.com/cron/jobs.json"

    def list_jobs(self, source: CareerSource) -> list[JobStub]:
        return [self._to_stub(job) for job in get_json(self.url) if job.get("jobId")]

    def to_details(self, stub: JobStub) -> JobDetails:
        job = stub.raw or {}
        return JobDetails(
            external_job_id=stub.external_job_id,
            title=stub.title,
            location_text=stub.location_text,
            employment_type=map_employment_type(job.get("employmentType")),
            # Entity-escaped HTML ("&lt;div&gt;"), which html_to_text unescapes.
            description=html_to_text(job.get("description")) or None,
            qualifications=html_to_text(job.get("requirements")) or None,
            source_url=stub.source_url,
            apply_url=stub.apply_url,
            source_published_at=parse_timestamp(job.get("openDate")),
            source_updated_at=stub.source_updated_at,
        )

    @staticmethod
    def _to_stub(job: dict[str, Any]) -> JobStub:
        url = f"https://elbitsystemscareer.com/jobs/?jobId={job['jobId']}"
        area = job.get("area")
        return JobStub(
            external_job_id=str(job["jobId"]),
            title=str(job.get("jobTitle") or ""),
            location_text=f"{area}, Israel" if area else "Israel",
            source_url=url,
            apply_url=url,
            source_updated_at=parse_timestamp(job.get("updateDate")),
            raw=job,
        )


class IaiFeed:
    url = "https://jobs.iai.co.il/wp-content/themes/tyco-wp/assets/json/jobs.json"

    def list_jobs(self, source: CareerSource) -> list[JobStub]:
        return [self._to_stub(job) for job in get_json(self.url) if job.get("id")]

    def to_details(self, stub: JobStub) -> JobDetails:
        job = stub.raw or {}
        return JobDetails(
            external_job_id=stub.external_job_id,
            title=stub.title,
            location_text=stub.location_text,
            employment_type=map_employment_type(job.get("tp")),
            description=str(job.get("dc") or "") or None,
            source_url=stub.source_url,
            apply_url=stub.apply_url,
        )

    @staticmethod
    def _to_stub(job: dict[str, Any]) -> JobStub:
        url = f"https://jobs.iai.co.il/job/{job['id']}/"
        city = job.get("ct")
        return JobStub(
            external_job_id=str(job["id"]),
            title=str(job.get("tl") or ""),
            location_text=f"{city}, Israel" if city else "Israel",
            source_url=url,
            apply_url=url,
            raw=job,
        )


class AmazonFeed:
    url = "https://www.amazon.jobs/en/search.json"
    page_size = 100

    def list_jobs(self, source: CareerSource) -> list[JobStub]:
        country = parse_qs(urlparse(source.source_url).query).get("country", ["ISR"])[0]
        stubs: list[JobStub] = []
        offset = 0
        while True:
            page = get_json(
                self.url,
                params={
                    "country": country,
                    "result_limit": self.page_size,
                    "offset": offset,
                    "sort": "recent",
                },
            )
            jobs = page.get("jobs") or []
            stubs.extend(self._to_stub(job) for job in jobs if job.get("id_icims"))
            offset += self.page_size
            if not jobs or offset >= int(page.get("hits") or 0):
                return stubs

    def to_details(self, stub: JobStub) -> JobDetails:
        job = stub.raw or {}
        description = html_to_text(job.get("description"))
        preferred = html_to_text(job.get("preferred_qualifications"))
        if preferred:
            description = f"{description}\n\nPreferred qualifications\n{preferred}"
        return JobDetails(
            external_job_id=stub.external_job_id,
            title=stub.title,
            department=job.get("job_category"),
            location_text=stub.location_text,
            employment_type=(
                EmploymentType.INTERNSHIP
                if job.get("is_intern")
                else map_employment_type(job.get("job_schedule_type"))
            ),
            description=description or None,
            qualifications=html_to_text(job.get("basic_qualifications")) or None,
            source_url=stub.source_url,
            apply_url=stub.apply_url,
            source_published_at=_parse_amazon_date(job.get("posted_date")),
        )

    @staticmethod
    def _to_stub(job: dict[str, Any]) -> JobStub:
        url = f"https://www.amazon.jobs{job.get('job_path') or ''}"
        city = job.get("city")
        return JobStub(
            external_job_id=str(job["id_icims"]),
            title=str(job.get("title") or ""),
            location_text=f"{city}, Israel" if city else job.get("location"),
            source_url=url,
            apply_url=job.get("url_next_step") or url,
            raw=job,
        )


def _parse_amazon_date(value: Any) -> datetime | None:
    """ "September 17, 2026" - the only date form the search API returns."""
    try:
        return datetime.strptime(str(value), "%B %d, %Y").replace(tzinfo=UTC)
    except ValueError:
        return None


FEEDS: dict[str, SiteFeed] = {"elbit": ElbitFeed(), "iai": IaiFeed(), "amazon": AmazonFeed()}


class SiteFeedAdapter:
    def list_jobs(self, source: CareerSource) -> list[JobStub]:
        return _feed(source).list_jobs(source)

    def fetch_job(self, source: CareerSource, stub: JobStub) -> JobDetails:
        return _feed(source).to_details(stub)


def _feed(source: CareerSource) -> SiteFeed:
    feed = FEEDS.get(source.external_identifier or "")
    if feed is None:
        raise ValueError(f"No site feed named {source.external_identifier!r} (source {source.id})")
    return feed

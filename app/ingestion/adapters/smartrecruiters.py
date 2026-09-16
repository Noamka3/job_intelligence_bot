"""SmartRecruiters Posting API - verified against the official OpenAPI
spec (api.smartrecruiters.com/posting-api/v1/api-docs) and a live call;
see docs/job_sources.md.

GET https://api.smartrecruiters.com/v1/companies/{companyIdentifier}/postings?limit=100&offset=0
    -> {"offset", "limit", "totalFound", "content": [{id, uuid, name,
        refNumber, releasedDate, location: {city, region, country, remote,
        hybrid, fullLocation}, department: {label}, typeOfEmployment:
        {label}, experienceLevel: {label}}]}
GET https://api.smartrecruiters.com/v1/companies/{companyIdentifier}/postings/{id}
    -> the same plus jobAd.sections.{companyDescription, jobDescription,
       qualifications, additionalInformation}.{title, text (HTML)},
       applyUrl, postingUrl, active

No auth (the spec declares `security: []` for these). limit caps at 100;
paginate by offset until offset >= totalFound. No updated-at exists -
only releasedDate.
"""

from __future__ import annotations

from typing import Any

from app.ingestion.adapters._http import get_json
from app.ingestion.adapters._util import map_employment_type, parse_timestamp
from app.ingestion.adapters.base import JobDetails, JobStub
from app.models.career_source import CareerSource
from app.models.enums import RemoteType
from app.services.jobs.html_text import html_to_text

_BASE_URL = "https://api.smartrecruiters.com/v1/companies"
_PAGE_SIZE = 100
_MAX_PAGES = 20


class SmartRecruitersAdapter:
    def list_jobs(self, source: CareerSource) -> list[JobStub]:
        company = source.external_identifier
        if not company:
            return []
        postings: list[dict[str, Any]] = []
        offset = 0
        for _ in range(_MAX_PAGES):
            page = get_json(
                f"{_BASE_URL}/{company}/postings", params={"limit": _PAGE_SIZE, "offset": offset}
            )
            content = page.get("content") or []
            postings.extend(content)
            offset += len(content)
            if not content or offset >= int(page.get("totalFound") or 0):
                break
        return [self._to_stub(posting, source) for posting in postings if posting.get("id")]

    def fetch_job(self, source: CareerSource, stub: JobStub) -> JobDetails:
        company = source.external_identifier
        detail = get_json(f"{_BASE_URL}/{company}/postings/{stub.external_job_id}")
        return self._to_details(detail, stub)

    @staticmethod
    def _to_stub(posting: dict[str, Any], source: CareerSource) -> JobStub:
        posting_id = str(posting["id"])
        return JobStub(
            external_job_id=posting_id,
            title=str(posting.get("name") or "").strip(),
            location_text=_location_text(posting.get("location")),
            source_url=f"https://jobs.smartrecruiters.com/{source.external_identifier}/{posting_id}",
            source_updated_at=None,
            raw=posting,
        )

    @staticmethod
    def _to_details(detail: dict[str, Any], stub: JobStub) -> JobDetails:
        sections = (detail.get("jobAd") or {}).get("sections") or {}

        def section(name: str) -> str:
            return html_to_text((sections.get(name) or {}).get("text"))

        description_parts = [
            section("companyDescription"),
            section("jobDescription"),
            section("additionalInformation"),
        ]
        location = detail.get("location") or {}
        if location.get("remote"):
            remote_type = RemoteType.REMOTE
        elif location.get("hybrid"):
            remote_type = RemoteType.HYBRID
        else:
            remote_type = RemoteType.UNKNOWN
        return JobDetails(
            external_job_id=stub.external_job_id,
            title=str(detail.get("name") or stub.title).strip(),
            department=_label(detail.get("department")),
            location_text=_location_text(location) or stub.location_text,
            remote_type=remote_type,
            employment_type=map_employment_type(_label(detail.get("typeOfEmployment"))),
            description="\n\n".join(part for part in description_parts if part) or None,
            qualifications=section("qualifications") or None,
            source_url=str(detail.get("postingUrl") or stub.source_url),
            apply_url=detail.get("applyUrl") or detail.get("postingUrl") or stub.source_url,
            source_published_at=parse_timestamp(detail.get("releasedDate")),
            source_updated_at=None,
        )


def _label(value: Any) -> str | None:
    if isinstance(value, dict):
        label = value.get("label")
        return str(label) if label else None
    return str(value) if value else None


def _location_text(location: Any) -> str | None:
    if not isinstance(location, dict):
        return None
    full = location.get("fullLocation")
    if full:
        return str(full)
    parts = [location.get("city"), location.get("region"), location.get("country")]
    return ", ".join(str(part) for part in parts if part) or None

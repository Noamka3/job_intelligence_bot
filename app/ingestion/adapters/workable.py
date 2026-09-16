"""Workable public job board - verified live against a real account from
the sheet (Humanz); see docs/job_sources.md.

GET https://apply.workable.com/api/v1/widget/accounts/{subdomain}
    -> {"name", "description", "jobs": [{shortcode, title, city, country,
        state, department, url, application_url, published_on, created_at,
        employment_type, telecommuting, locations: [...]}]}
GET https://apply.workable.com/api/v2/accounts/{subdomain}/jobs/{shortcode}
    -> {title, description (HTML), requirements (HTML), benefits (HTML),
        location: {city, region, country}, department: [...], remote,
        workplace: "on_site"|"hybrid"|"remote", published, state}

No auth. The widget endpoint is what companies' own career pages call
from the browser (seen verbatim on anzu.io).
"""

from __future__ import annotations

from typing import Any

from app.ingestion.adapters._http import get_json
from app.ingestion.adapters._util import map_employment_type, parse_timestamp
from app.ingestion.adapters.base import JobDetails, JobStub
from app.models.career_source import CareerSource
from app.models.enums import RemoteType
from app.services.jobs.html_text import html_to_text

_WIDGET_URL = "https://apply.workable.com/api/v1/widget/accounts"
_DETAIL_URL = "https://apply.workable.com/api/v2/accounts"

_WORKPLACE_TO_REMOTE = {
    "remote": RemoteType.REMOTE,
    "hybrid": RemoteType.HYBRID,
    "on_site": RemoteType.ONSITE,
    "onsite": RemoteType.ONSITE,
}


class WorkableAdapter:
    def list_jobs(self, source: CareerSource) -> list[JobStub]:
        subdomain = source.external_identifier
        if not subdomain:
            return []
        data = get_json(f"{_WIDGET_URL}/{subdomain}")
        # The widget emits one row per location for multi-location jobs
        # (verified: 71 rows for 57 jobs on a real account) - the
        # shortcode is the job.
        stubs: dict[str, JobStub] = {}
        for job in data.get("jobs") or []:
            shortcode = job.get("shortcode")
            if shortcode and str(shortcode) not in stubs:
                stubs[str(shortcode)] = self._to_stub(job, source)
        return list(stubs.values())

    def fetch_job(self, source: CareerSource, stub: JobStub) -> JobDetails:
        detail = get_json(f"{_DETAIL_URL}/{source.external_identifier}/jobs/{stub.external_job_id}")
        return self._to_details(detail, stub)

    @staticmethod
    def _to_stub(job: dict[str, Any], source: CareerSource) -> JobStub:
        return JobStub(
            external_job_id=str(job["shortcode"]),
            title=str(job.get("title") or ""),
            location_text=_location_text(job.get("city"), job.get("country")),
            source_url=str(job.get("url") or source.source_url),
            apply_url=job.get("application_url") or job.get("url"),
            # published_on/created_at never move after an edit - no
            # updated-at exists, so the crawler re-compares content.
            source_updated_at=None,
            raw=job,
        )

    @staticmethod
    def _to_details(detail: dict[str, Any], stub: JobStub) -> JobDetails:
        raw = stub.raw or {}
        location = detail.get("location") or {}
        departments = detail.get("department")
        department = (
            ", ".join(str(d) for d in departments)
            if isinstance(departments, list)
            else (str(departments) if departments else None)
        )
        description_parts = [html_to_text(detail.get("description"))]
        benefits = html_to_text(detail.get("benefits"))
        if benefits:
            description_parts.append(f"Benefits\n{benefits}")
        workplace = str(detail.get("workplace") or "").lower()
        remote_type = _WORKPLACE_TO_REMOTE.get(workplace, RemoteType.UNKNOWN)
        if remote_type is RemoteType.UNKNOWN and (detail.get("remote") or raw.get("telecommuting")):
            remote_type = RemoteType.REMOTE
        return JobDetails(
            external_job_id=stub.external_job_id,
            title=str(detail.get("title") or stub.title),
            department=department or raw.get("department"),
            location_text=_location_text(location.get("city"), location.get("country"))
            or stub.location_text,
            remote_type=remote_type,
            employment_type=map_employment_type(raw.get("employment_type")),
            description="\n\n".join(part for part in description_parts if part) or None,
            qualifications=html_to_text(detail.get("requirements")) or None,
            source_url=stub.source_url,
            apply_url=stub.apply_url,
            source_published_at=parse_timestamp(raw.get("published_on")),
            source_updated_at=None,
        )


def _location_text(city: Any, country: Any) -> str | None:
    parts = [str(part) for part in (city, country) if part]
    return ", ".join(parts) or None

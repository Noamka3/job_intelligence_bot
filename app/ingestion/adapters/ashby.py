"""Ashby public Job Postings API - verified in docs/job_sources.md.

GET https://api.ashbyhq.com/posting-api/job-board/{client}?includeCompensation=true

Public, no authentication, returns full posting content in one call (like
Lever) - no separate per-job detail endpoint needed.

CAVEAT: the endpoint itself and its general shape (a `jobs` array of
postings with title/department/location/description fields) are
documented and verified (see docs/job_sources.md), but this project has
no Ashby company in the current sheet to test a live response against.
Field access below is defensive (.get() with fallbacks) precisely because
of that - if Ashby's exact field names differ slightly from what's coded
here, this adapter degrades to missing fields rather than crashing, but
should be checked against a real response before being relied on.
"""

from __future__ import annotations

from typing import Any

from app.ingestion.adapters._http import get_json
from app.ingestion.adapters._util import map_employment_type, parse_timestamp
from app.ingestion.adapters.base import JobDetails, JobStub
from app.models.career_source import CareerSource
from app.services.jobs.html_text import html_to_text

_BASE_URL = "https://api.ashbyhq.com/posting-api/job-board"


class AshbyAdapter:
    def list_jobs(self, source: CareerSource) -> list[JobStub]:
        client = source.external_identifier
        if not client:
            return []

        data = get_json(f"{_BASE_URL}/{client}", params={"includeCompensation": "true"})
        return [self._to_stub(job, source) for job in data.get("jobs", [])]

    def fetch_job(self, source: CareerSource, stub: JobStub) -> JobDetails:
        job = stub.raw
        if job is None:
            client = source.external_identifier
            data = get_json(f"{_BASE_URL}/{client}", params={"includeCompensation": "true"})
            job = next(
                (j for j in data.get("jobs", []) if str(j.get("id")) == stub.external_job_id),
                {},
            )
        return self._to_details(job, stub)

    @staticmethod
    def _to_stub(job: dict[str, Any], source: CareerSource) -> JobStub:
        return JobStub(
            external_job_id=str(job["id"]),
            title=job.get("title", ""),
            location_text=job.get("location"),
            source_url=job.get("jobUrl") or source.source_url,
            apply_url=job.get("applyUrl"),
            source_updated_at=parse_timestamp(job.get("updatedAt") or job.get("publishedAt")),
            raw=job,
        )

    @staticmethod
    def _to_details(job: dict[str, Any], stub: JobStub) -> JobDetails:
        description = job.get("descriptionPlain") or html_to_text(job.get("descriptionHtml"))
        return JobDetails(
            external_job_id=str(job.get("id", stub.external_job_id)),
            title=job.get("title", stub.title),
            department=job.get("department"),
            team=job.get("team"),
            location_text=job.get("location") or stub.location_text,
            employment_type=map_employment_type(job.get("employmentType")),
            description=description,
            source_url=job.get("jobUrl") or stub.source_url,
            apply_url=job.get("applyUrl") or stub.apply_url,
            source_published_at=parse_timestamp(job.get("publishedAt")),
            source_updated_at=parse_timestamp(job.get("updatedAt") or job.get("publishedAt")),
        )

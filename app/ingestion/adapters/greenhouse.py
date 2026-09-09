"""Greenhouse Job Board API - verified in docs/job_sources.md.

GET /v1/boards/{board_token}/jobs           - cheap list, no description
GET /v1/boards/{board_token}/jobs/{id}?content=true - full description

No authentication required for reads.
"""

from __future__ import annotations

from typing import Any

from app.ingestion.adapters._http import get_json
from app.ingestion.adapters._util import first_name, parse_timestamp
from app.ingestion.adapters.base import JobDetails, JobStub
from app.models.career_source import CareerSource
from app.services.jobs.html_text import html_to_text

_BASE_URL = "https://boards-api.greenhouse.io/v1/boards"


class GreenhouseAdapter:
    def list_jobs(self, source: CareerSource) -> list[JobStub]:
        board_token = source.external_identifier
        if not board_token:
            return []

        data = get_json(f"{_BASE_URL}/{board_token}/jobs")
        return [self._to_stub(job, source) for job in data.get("jobs", [])]

    def fetch_job(self, source: CareerSource, stub: JobStub) -> JobDetails:
        board_token = source.external_identifier
        data = get_json(
            f"{_BASE_URL}/{board_token}/jobs/{stub.external_job_id}", params={"content": "true"}
        )
        return self._to_details(data, stub)

    @staticmethod
    def _to_stub(job: dict[str, Any], source: CareerSource) -> JobStub:
        return JobStub(
            external_job_id=str(job["id"]),
            title=job.get("title", ""),
            location_text=(job.get("location") or {}).get("name"),
            source_url=job.get("absolute_url") or source.source_url,
            apply_url=job.get("absolute_url"),
            source_updated_at=parse_timestamp(job.get("updated_at")),
        )

    @staticmethod
    def _to_details(data: dict[str, Any], stub: JobStub) -> JobDetails:
        return JobDetails(
            external_job_id=str(data.get("id", stub.external_job_id)),
            title=data.get("title", stub.title),
            department=first_name(data.get("departments")),
            location_text=(data.get("location") or {}).get("name") or stub.location_text,
            description=html_to_text(data.get("content")),
            source_url=data.get("absolute_url") or stub.source_url,
            apply_url=data.get("absolute_url") or stub.apply_url,
            source_updated_at=parse_timestamp(data.get("updated_at")),
        )

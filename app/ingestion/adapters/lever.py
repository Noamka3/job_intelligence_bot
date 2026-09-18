"""Lever Postings API - verified in docs/job_sources.md.

GET https://api.lever.co/v0/postings/{client}?mode=json

Public, no authentication. Unlike Greenhouse, the list call already
returns full posting content, so fetch_job normally does no extra network
call - it reuses JobStub.raw.
"""

from __future__ import annotations

from typing import Any
from urllib.parse import urlparse

from app.ingestion.adapters._http import get_json
from app.ingestion.adapters._util import map_employment_type, parse_timestamp
from app.ingestion.adapters.base import JobDetails, JobStub
from app.models.career_source import CareerSource
from app.services.jobs.html_text import html_to_text

_BASE_URL = "https://api.lever.co/v0/postings"
_EU_BASE_URL = "https://api.eu.lever.co/v0/postings"


def postings_api_base(board_host: str) -> str:
    """Lever's EU region has its own hosts: Mobileye's board is
    jobs.eu.lever.co and only api.eu.lever.co knows the client."""
    return _EU_BASE_URL if board_host.endswith("eu.lever.co") else _BASE_URL


class LeverAdapter:
    def list_jobs(self, source: CareerSource) -> list[JobStub]:
        client = source.external_identifier
        if not client:
            return []

        postings = get_json(f"{_api_base(source)}/{client}", params={"mode": "json"})
        return [self._to_stub(posting, source) for posting in postings]

    def fetch_job(self, source: CareerSource, stub: JobStub) -> JobDetails:
        posting = stub.raw
        if posting is None:
            client = source.external_identifier
            posting = get_json(
                f"{_api_base(source)}/{client}/{stub.external_job_id}", params={"mode": "json"}
            )
        return self._to_details(posting, stub)

    @staticmethod
    def _to_stub(posting: dict[str, Any], source: CareerSource) -> JobStub:
        categories = posting.get("categories") or {}
        return JobStub(
            external_job_id=str(posting["id"]),
            title=posting.get("text", ""),
            location_text=categories.get("location"),
            source_url=posting.get("hostedUrl") or source.source_url,
            apply_url=posting.get("applyUrl"),
            # Lever exposes createdAt only, no updated-at: leaving this
            # unset makes the crawler re-compare content every time (free
            # here - the list call already carried the full posting in
            # `raw`) instead of treating an edited posting as unchanged
            # forever because its creation time never moves.
            source_updated_at=None,
            raw=posting,
        )

    @staticmethod
    def _to_details(posting: dict[str, Any], stub: JobStub) -> JobDetails:
        categories = posting.get("categories") or {}

        description_parts = [
            posting.get("descriptionPlain") or html_to_text(posting.get("description"))
        ]
        for section in posting.get("lists") or []:
            heading = section.get("text")
            content = html_to_text(section.get("content"))
            description_parts.append(f"{heading}\n{content}" if heading else content)
        additional = posting.get("additionalPlain") or html_to_text(posting.get("additional"))
        if additional:
            description_parts.append(additional)

        return JobDetails(
            external_job_id=str(posting.get("id", stub.external_job_id)),
            title=posting.get("text", stub.title),
            department=categories.get("team") or categories.get("department"),
            location_text=categories.get("location") or stub.location_text,
            employment_type=map_employment_type(categories.get("commitment")),
            description="\n\n".join(part for part in description_parts if part),
            source_url=posting.get("hostedUrl") or stub.source_url,
            apply_url=posting.get("applyUrl") or stub.apply_url,
            source_published_at=parse_timestamp(posting.get("createdAt")),
            source_updated_at=None,
        )


def _api_base(source: CareerSource) -> str:
    return postings_api_base(urlparse(source.source_url).hostname or "")

"""Generic schema.org JobPosting JSON-LD adapter.

Handles the case where a company's career/listing page embeds one or more
<script type="application/ld+json"> blocks with JobPosting objects
directly on that one page - common for SEO (Google for Jobs). Does NOT
follow links to separate per-job detail pages; a listing page that only
links out to individual job pages (each with their own JSON-LD) needs
real link-following/crawling logic that belongs with the generic HTML
adapter (Phase 8), not here. Companies whose only JobPosting markup lives
on per-job pages will show zero jobs from this adapter until then - a
documented limitation, not a silent failure.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

from bs4 import BeautifulSoup

from app.ingestion.adapters._http import get_text
from app.ingestion.adapters._util import map_employment_type, parse_timestamp
from app.ingestion.adapters.base import JobDetails, JobStub
from app.models.career_source import CareerSource
from app.models.enums import EmploymentType
from app.services.jobs.html_text import html_to_text

_SCHEMA_EMPLOYMENT_TYPES: dict[str, EmploymentType] = {
    "FULL_TIME": EmploymentType.FULL_TIME,
    "PART_TIME": EmploymentType.PART_TIME,
    "CONTRACTOR": EmploymentType.CONTRACT,
    "TEMPORARY": EmploymentType.TEMPORARY,
    "INTERN": EmploymentType.INTERNSHIP,
}


class JsonLdAdapter:
    def list_jobs(self, source: CareerSource) -> list[JobStub]:
        html = get_text(source.source_url)
        return [self._to_stub(posting, source) for posting in _extract_job_postings(html)]

    def fetch_job(self, source: CareerSource, stub: JobStub) -> JobDetails:
        return self._to_details(stub.raw or {}, stub)

    @staticmethod
    def _to_stub(posting: dict[str, Any], source: CareerSource) -> JobStub:
        title = str(posting.get("title") or "")
        job_url = _extract_url(posting)
        location_text = _extract_location_text(posting)
        return JobStub(
            external_job_id=_extract_identifier(posting, title, job_url, location_text),
            title=title,
            location_text=location_text,
            source_url=job_url or source.source_url,
            apply_url=job_url or source.source_url,
            # Only a real dateModified counts as "updated at": datePosted
            # never moves after an edit, so using it here would make every
            # later change to the posting invisible to the crawler.
            source_updated_at=parse_timestamp(posting.get("dateModified")),
            raw=posting,
        )

    @staticmethod
    def _to_details(posting: dict[str, Any], stub: JobStub) -> JobDetails:
        return JobDetails(
            external_job_id=stub.external_job_id,
            title=str(posting.get("title") or stub.title),
            location_text=_extract_location_text(posting) or stub.location_text,
            employment_type=_map_employment_type(posting.get("employmentType")),
            description=html_to_text(posting.get("description")),
            source_url=stub.source_url,
            apply_url=stub.apply_url,
            source_published_at=parse_timestamp(posting.get("datePosted")),
            source_updated_at=parse_timestamp(posting.get("dateModified")),
        )


def _extract_job_postings(html: str) -> list[dict[str, Any]]:
    soup = BeautifulSoup(html, "lxml")
    postings: list[dict[str, Any]] = []
    for script in soup.find_all("script", attrs={"type": "application/ld+json"}):
        text = script.string or script.get_text()
        if not text or not text.strip():
            continue
        try:
            payload = json.loads(text)
        except (json.JSONDecodeError, TypeError):
            continue
        postings.extend(_find_job_postings(payload))
    return postings


def _find_job_postings(node: Any) -> list[dict[str, Any]]:
    found: list[dict[str, Any]] = []
    if isinstance(node, list):
        for item in node:
            found.extend(_find_job_postings(item))
        return found
    if not isinstance(node, dict):
        return found

    node_type = node.get("@type")
    type_values = node_type if isinstance(node_type, list) else [node_type]
    if "JobPosting" in type_values:
        found.append(node)
    if "@graph" in node:
        found.extend(_find_job_postings(node["@graph"]))
    return found


def _extract_url(posting: dict[str, Any]) -> str | None:
    url = posting.get("url")
    if isinstance(url, str) and url:
        return url
    main_entity = posting.get("mainEntityOfPage")
    if isinstance(main_entity, str):
        return main_entity
    if isinstance(main_entity, dict):
        candidate = main_entity.get("@id") or main_entity.get("url")
        if isinstance(candidate, str):
            return candidate
    return None


def _extract_identifier(
    posting: dict[str, Any], title: str, url: str | None, location_text: str | None
) -> str:
    identifier = posting.get("identifier")
    if isinstance(identifier, dict):
        value = identifier.get("value")
        if value:
            return str(value)
    if isinstance(identifier, str) and identifier:
        return identifier
    # No identifier and no per-job URL: hash what still distinguishes
    # postings on one page (title + location). Hashing the shared page URL
    # instead would give every such posting the same id, silently
    # collapsing them into one row.
    seed = url or f"{title}|{location_text or ''}"
    return hashlib.sha256(seed.encode("utf-8")).hexdigest()[:32]


def _extract_location_text(posting: dict[str, Any]) -> str | None:
    job_location = posting.get("jobLocation")
    if isinstance(job_location, list):
        job_location = job_location[0] if job_location else None
    if isinstance(job_location, dict):
        address = job_location.get("address")
        if isinstance(address, dict):
            parts = [
                address.get("addressLocality"),
                address.get("addressRegion"),
                _schema_name(address.get("addressCountry")),
            ]
            text = ", ".join(str(p) for p in parts if p)
            if text:
                return text
        elif isinstance(address, str) and address.strip():
            return address.strip()
    if posting.get("jobLocationType") == "TELECOMMUTE":
        return "Remote"
    return None


def _schema_name(value: Any) -> str | None:
    """schema.org lets a Country be a plain string or an object
    ({"@type": "Country", "name": "Israel"}) - both are common."""
    if isinstance(value, dict):
        name = value.get("name")
        return str(name) if name else None
    return str(value) if value else None


def _map_employment_type(value: Any) -> EmploymentType:
    if isinstance(value, list):
        value = value[0] if value else None
    if not isinstance(value, str):
        return EmploymentType.UNKNOWN
    return _SCHEMA_EMPLOYMENT_TYPES.get(value.upper()) or map_employment_type(value)

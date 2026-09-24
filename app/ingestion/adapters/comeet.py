"""Comeet public Careers API - verified live against real companies from
the sheet (Cymotive, Tango); see
docs/job_sources.md for the exact findings, which differ from generic
documentation summaries in one important way: the positions endpoint
requires a `token` that is NOT present in the public jobs page URL - it is
embedded in a `COMPANY_DATA` JS variable inside that page's HTML. This
adapter fetches that page once per source to recover company_uid + token
rather than trusting URL path segments for it (a generic web search
summary described a token-based API but not where the token actually
comes from - always verify the real response format).
"""

from __future__ import annotations

import json
import re
from typing import Any

from app.ingestion.adapters._http import get_json, get_text
from app.ingestion.adapters._util import map_employment_type, parse_timestamp
from app.ingestion.adapters.base import JobDetails, JobStub
from app.models.career_source import CareerSource
from app.services.jobs.html_text import html_to_text

_BASE_URL = "https://www.comeet.com/careers-api/2.0/company"
_COMPANY_DATA_RE = re.compile(r"COMPANY_DATA\s*=\s*(\{.*?\});", re.DOTALL)
# A company's own page embedding the Comeet JS API (verified on real
# pages: eToro, Checkmarx): COMEET.init({"token": "...", "company-uid":
# "41.009", ...}) - a JS object literal with comments, not strict JSON,
# hence regexes scoped to that call rather than json.loads. The key
# quotes are optional: Alice's page writes `token: '...'` bare.
_COMEET_INIT_RE = re.compile(r"COMEET\.init\s*\(\s*\{(.{0,6000}?)\}\s*\)", re.DOTALL)
_INIT_TOKEN_RE = re.compile(r"(?<![\w-])[\"']?token[\"']?\s*:\s*[\"']([A-Za-z0-9]+)[\"']")
_INIT_UID_RE = re.compile(r"[\"']?company-uid[\"']?\s*:\s*[\"']([A-Z0-9]{2}\.[A-Z0-9]{3})[\"']")


class ComeetCredentialsNotFoundError(RuntimeError):
    def __init__(self, url: str) -> None:
        super().__init__(
            f"Could not find Comeet credentials (COMPANY_DATA or COMEET.init) on {url}"
        )


def _resolve_company_credentials(source_url: str) -> tuple[str, str]:
    html = get_text(source_url)
    match = _COMPANY_DATA_RE.search(html)
    if match:
        data = json.loads(match.group(1))
        return data["company_uid"], data["token"]

    init = _COMEET_INIT_RE.search(html)
    if init:
        uid = _INIT_UID_RE.search(init.group(1))
        token = _INIT_TOKEN_RE.search(init.group(1))
        if uid and token:
            return uid.group(1), token.group(1)
    raise ComeetCredentialsNotFoundError(source_url)


class ComeetAdapter:
    def list_jobs(self, source: CareerSource) -> list[JobStub]:
        company_uid, token = _resolve_company_credentials(source.source_url)
        positions = get_json(
            f"{_BASE_URL}/{company_uid}/positions", params={"token": token, "details": "false"}
        )
        return [self._to_stub(position, company_uid, token, source) for position in positions]

    def fetch_job(self, source: CareerSource, stub: JobStub) -> JobDetails:
        credentials = stub.raw or {}
        company_uid = credentials.get("company_uid")
        token = credentials.get("token")
        if not company_uid or not token:
            company_uid, token = _resolve_company_credentials(source.source_url)

        data = get_json(
            f"{_BASE_URL}/{company_uid}/positions/{stub.external_job_id}",
            params={"token": token, "details": "true"},
        )
        return self._to_details(data, stub)

    @staticmethod
    def _to_stub(
        position: dict[str, Any], company_uid: str, token: str, source: CareerSource
    ) -> JobStub:
        location = position.get("location") or {}
        return JobStub(
            external_job_id=str(position["uid"]),
            title=position.get("name", ""),
            location_text=location.get("name"),
            source_url=position.get("url_active_page") or source.source_url,
            apply_url=position.get("url_active_page"),
            source_updated_at=parse_timestamp(position.get("time_updated")),
            raw={"company_uid": company_uid, "token": token},
        )

    @staticmethod
    def _to_details(data: dict[str, Any], stub: JobStub) -> JobDetails:
        location = data.get("location") or {}
        # Comeet already segments the description into named parts - a
        # nicer starting point than the single HTML blob most other ATSes
        # return (responsibilities/qualifications are kept split out
        # where the source actually provides that split).
        sections = [
            (str(section.get("name", "")).strip(), html_to_text(section.get("value")))
            for section in data.get("details") or []
        ]
        by_name = {name.lower(): text for name, text in sections}
        # Anything that isn't one of the two named parts we split out
        # ("Advantages", "About the role", ...) still belongs in the
        # description - dropping custom sections would embed only a
        # fraction of what the posting actually says.
        description_parts = [
            text if name.lower() == "description" else f"{name}\n{text}"
            for name, text in sections
            if text and name.lower() not in ("responsibilities", "requirements")
        ]

        return JobDetails(
            external_job_id=str(data.get("uid", stub.external_job_id)),
            title=data.get("name", stub.title),
            department=data.get("department"),
            location_text=location.get("name") or stub.location_text,
            employment_type=map_employment_type(data.get("employment_type")),
            description="\n\n".join(description_parts) or None,
            responsibilities=by_name.get("responsibilities") or None,
            qualifications=by_name.get("requirements") or None,
            source_url=data.get("url_active_page") or stub.source_url,
            apply_url=data.get("url_active_page") or stub.apply_url,
            source_updated_at=parse_timestamp(data.get("time_updated")),
        )

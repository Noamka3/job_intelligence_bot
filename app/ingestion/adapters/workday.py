"""Workday external career sites - verified live against the sheet's real
tenants (Intel, Flex, Medtronic); see docs/job_sources.md.

POST https://{tenant}.{wdN}.myworkdayjobs.com/wday/cxs/{tenant}/{site}/jobs
     {"appliedFacets": {...}, "limit": 20, "offset": 0, "searchText": ""}
     -> {"total", "jobPostings": [{title, externalPath, locationsText,
         postedOn, bulletFields}], "facets": [...]}
GET  https://{tenant}.{wdN}.myworkdayjobs.com/wday/cxs/{tenant}/{site}{externalPath}
     -> {"jobPostingInfo": {title, jobDescription (HTML), location,
         additionalLocations, startDate, timeType, remoteType, externalUrl,
         jobReqId, country: {descriptor}}}

No auth. Not a documented public API - it is what the career site's own
JS calls, unchanged for years, so field access below is defensive.

Country filtering is server-side where the tenant exposes a location
facet (Settings.target_country): a global board like Intel's lists ~600
jobs of which ~25 are in Israel, and the facet that carries the country
is tenant-specific ("locationCountry", "Location_Country", or only
city-level "locations" values like "Israel, Haifa") - so it's discovered
from the first response rather than assumed. Israel's value id is the
same Workday reference id across tenants, but the code doesn't rely on
that either.
"""

from __future__ import annotations

import re
from typing import Any
from urllib.parse import urlparse

from app.core.config import get_settings
from app.ingestion.adapters._http import get_json, post_json
from app.ingestion.adapters._util import map_employment_type, parse_timestamp
from app.ingestion.adapters.base import JobDetails, JobStub
from app.models.career_source import CareerSource
from app.models.enums import RemoteType
from app.services.jobs.html_text import html_to_text

_PAGE_SIZE = 20  # the maximum the endpoint accepts
_MAX_PAGES = 50  # safety cap per crawl: 1000 postings
_LOCALE_SEGMENT_RE = re.compile(r"^[a-z]{2}(?:-[A-Za-z]{2,4})?$")


class WorkdayAdapter:
    def list_jobs(self, source: CareerSource) -> list[JobStub]:
        base = _cxs_base(source)
        if base is None:
            return []

        first = post_json(f"{base}/jobs", _body({}, 0))
        facets = _country_facets(first.get("facets") or [], get_settings().target_country)
        search_text = ""
        if facets:
            first = post_json(f"{base}/jobs", _body(facets, 0))
        elif get_settings().target_country:
            # No location facet at all on this tenant: full-text search is
            # the coarser fallback (matches mentions anywhere in the
            # posting; the Israel-only filter downstream cleans up).
            search_text = get_settings().target_country
            first = post_json(f"{base}/jobs", _body({}, 0, search_text))

        postings: list[dict[str, Any]] = list(first.get("jobPostings") or [])
        total = int(first.get("total") or 0)
        offset = _PAGE_SIZE
        pages = 1
        while offset < total and pages < _MAX_PAGES:
            page = post_json(f"{base}/jobs", _body(facets, offset, search_text))
            batch = page.get("jobPostings") or []
            if not batch:
                break
            postings.extend(batch)
            offset += _PAGE_SIZE
            pages += 1

        site_root = _site_root(source)
        return [
            self._to_stub(posting, site_root) for posting in postings if posting.get("externalPath")
        ]

    def fetch_job(self, source: CareerSource, stub: JobStub) -> JobDetails:
        base = _cxs_base(source)
        external_path = (stub.raw or {}).get("externalPath")
        if base is None or not external_path:
            raise ValueError(f"Workday stub without externalPath for source {source.id}")
        data = get_json(f"{base}{external_path}")
        return self._to_details(data.get("jobPostingInfo") or {}, stub)

    @staticmethod
    def _to_stub(posting: dict[str, Any], site_root: str) -> JobStub:
        external_path = str(posting["externalPath"])
        return JobStub(
            external_job_id=_job_id(external_path),
            title=str(posting.get("title") or ""),
            location_text=posting.get("locationsText"),
            source_url=f"{site_root}{external_path}",
            apply_url=f"{site_root}{external_path}",
            # Workday exposes only "Posted 3 Days Ago"-style text here and
            # no updated-at anywhere; leaving this unset makes the crawler
            # re-compare content (one GET per job) instead of never
            # noticing an edit.
            source_updated_at=None,
            raw={"externalPath": external_path},
        )

    @staticmethod
    def _to_details(info: dict[str, Any], stub: JobStub) -> JobDetails:
        return JobDetails(
            external_job_id=stub.external_job_id,
            title=str(info.get("title") or stub.title),
            location_text=_location_text(info) or stub.location_text,
            remote_type=_remote_type(info.get("remoteType")),
            employment_type=map_employment_type(_descriptor(info.get("timeType"))),
            description=html_to_text(info.get("jobDescription")),
            source_url=str(info.get("externalUrl") or stub.source_url),
            apply_url=str(info.get("externalUrl") or stub.apply_url or stub.source_url),
            source_published_at=parse_timestamp(info.get("startDate")),
            source_updated_at=None,
        )


def _location_text(info: dict[str, Any]) -> str:
    """A posting open in several countries lists every office (a Medtronic
    role: nine, 259 characters - more than the column holds). When it
    names the country we asked the board for, only those offices are
    kept: they are what the posting means here."""
    locations = [
        str(loc) for loc in (info.get("location"), *(info.get("additionalLocations") or [])) if loc
    ]
    target = get_settings().target_country.lower()
    if target and (here := [loc for loc in locations if target in loc.lower()]):
        return ", ".join(here)
    country = _descriptor(info.get("country"))
    if country and not any(country.lower() in loc.lower() for loc in locations):
        locations.append(country)
    return ", ".join(locations)


def _body(facets: dict[str, list[str]], offset: int, search_text: str = "") -> dict[str, Any]:
    return {
        "appliedFacets": facets,
        "limit": _PAGE_SIZE,
        "offset": offset,
        "searchText": search_text,
    }


def _tenant_and_site(source: CareerSource) -> tuple[str, str] | None:
    identifier = source.external_identifier or ""
    if "/" in identifier:
        tenant, site = identifier.split("/", 1)
        return tenant, site
    parsed = urlparse(source.source_url)
    tenant = (parsed.hostname or "").split(".")[0]
    segments = [s for s in parsed.path.split("/") if s]
    first_site = next((s for s in segments if not _LOCALE_SEGMENT_RE.match(s)), None)
    if not tenant or first_site is None:
        return None
    return tenant, first_site


def _cxs_base(source: CareerSource) -> str | None:
    pair = _tenant_and_site(source)
    if pair is None:
        return None
    tenant, site = pair
    host = urlparse(source.source_url).hostname or ""
    return f"https://{host}/wday/cxs/{tenant}/{site}"


def _site_root(source: CareerSource) -> str:
    pair = _tenant_and_site(source)
    host = urlparse(source.source_url).hostname or ""
    site = pair[1] if pair else ""
    return f"https://{host}/{site}"


def _job_id(external_path: str) -> str:
    # ".../Senior-Mechanical-Design-Engineer_R76497-1": the trailing
    # requisition token is the stable id; the slug before it changes
    # whenever the title is edited.
    last = external_path.rstrip("/").rsplit("/", 1)[-1]
    return last.rsplit("_", 1)[-1] if "_" in last else last


def _descriptor(value: Any) -> str | None:
    if isinstance(value, dict):
        return str(value.get("descriptor") or "") or None
    return str(value) if value else None


def _remote_type(value: Any) -> RemoteType:
    text = (_descriptor(value) or "").lower()
    if "remote" in text:
        return RemoteType.REMOTE
    if "hybrid" in text:
        return RemoteType.HYBRID
    if "on-site" in text or "onsite" in text or "on site" in text:
        return RemoteType.ONSITE
    return RemoteType.UNKNOWN


def _country_facets(facets: list[dict[str, Any]], country: str) -> dict[str, list[str]]:
    """{facetParameter: [value ids]} for every facet value whose descriptor
    names the country. Prefers a country-level facet (descriptor ==
    country) over city-level entries ("Israel, Haifa") when both exist,
    since Workday ANDs facets of different parameters together."""
    if not country:
        return {}
    needle = country.lower()
    exact: dict[str, list[str]] = {}
    partial: dict[str, list[str]] = {}

    def walk(parameter: str | None, values: list[dict[str, Any]]) -> None:
        for value in values:
            param = value.get("facetParameter") or parameter
            nested = value.get("values")
            if isinstance(nested, list):
                walk(param, nested)
            descriptor = str(value.get("descriptor") or "")
            value_id = value.get("id")
            if not value_id or not param or needle not in descriptor.lower():
                continue
            bucket = exact if descriptor.lower() == needle else partial
            bucket.setdefault(param, []).append(str(value_id))

    walk(None, facets)
    if exact:
        param = next(iter(exact))
        return {param: exact[param]}
    return partial

"""Oracle Taleo "careersection" sites - verified live against the sheet's
real one (radware.taleo.net); see docs/job_sources.md.

List: POST https://{host}/careersection/rest/jobboard/searchjobs?portal={portal}&lang=en
      Headers: Content-Type: application/json plus a `tz`/`tzname` header
      (the server 500s without one). Body must carry the full filter
      structure the page's own JS sends (trimmed arrays also 500).
      -> {"requisitionList": [{jobId, contestNo, column: [...],
          linkedColumn, locationsColumns}], "pagingData": {currentPageNo,
          pageSize, totalCount}, "facetResults": [...]}
      column[linkedColumn] is the title; column[locationsColumns[0]] is a
      JSON-encoded list of location codes ("[\"IL-IL-Tel Aviv\"]").
Detail: GET https://{host}/careersection/{section}/jobdetail.ftl?job={contestNo}&lang=en
      The description is not server-rendered HTML: it sits in an inline
      `api.fillList('requisitionDescriptionInterface', 'descRequisition',
      [...])` JS string array - [9] title, [10] contestNo, [11] description,
      [12] qualifications (both "!*!"-prefixed, URL-encoded HTML), [13]
      primary location.

The portal id is the Taleo-wide default external portal - it isn't
present in the page HTML at all, but the same id works across unrelated
Taleo hosts (verified on three). Not a documented API; field access is
defensive throughout.
"""

from __future__ import annotations

import json
import re
from datetime import UTC, datetime
from typing import Any
from urllib.parse import unquote, urlparse

from app.core.config import get_settings
from app.ingestion.adapters._http import get_text, post_json
from app.ingestion.adapters.base import JobDetails, JobStub
from app.models.career_source import CareerSource
from app.services.jobs.html_text import html_to_text

_DEFAULT_PORTAL = "101430233"
_MAX_PAGES = 20
_DISPLAY_DATE_RE = re.compile(r"^[A-Z][a-z]{2} \d{1,2}, \d{4}$")
_FILL_LIST_RE = re.compile(
    r"fillList\(\s*'requisitionDescriptionInterface'\s*,\s*'descRequisition'\s*,\s*\[(.*?)\]\s*\)",
    re.DOTALL,
)
_JS_STRING_RE = re.compile(r"'((?:[^'\\]|\\.)*)'")
_ENCODED_HTML_PREFIX = "!*!"


class TaleoAdapter:
    def list_jobs(self, source: CareerSource) -> list[JobStub]:
        host, section = _host_and_section(source.source_url)
        endpoint = f"https://{host}/careersection/rest/jobboard/searchjobs"
        params = {"portal": _DEFAULT_PORTAL, "lang": "en"}
        headers = {"tzname": get_settings().default_timezone}

        first = post_json(endpoint, _search_body(1, []), params=params, headers=headers)
        if first.get("careerSectionUnAvailable"):
            return []
        location_ids = _location_filter_ids(first.get("facetResults") or [])
        if location_ids:
            first = post_json(
                endpoint, _search_body(1, location_ids), params=params, headers=headers
            )

        requisitions: list[dict[str, Any]] = list(first.get("requisitionList") or [])
        paging = first.get("pagingData") or {}
        total = int(paging.get("totalCount") or 0)
        page_size = int(paging.get("pageSize") or 25) or 25
        page = 2
        while len(requisitions) < total and page <= _MAX_PAGES:
            data = post_json(
                endpoint, _search_body(page, location_ids), params=params, headers=headers
            )
            batch = data.get("requisitionList") or []
            if not batch:
                break
            requisitions.extend(batch)
            page += 1
            if len(batch) < page_size:
                break

        return [
            self._to_stub(requisition, host, section)
            for requisition in requisitions
            if requisition.get("contestNo") or requisition.get("jobId")
        ]

    def fetch_job(self, source: CareerSource, stub: JobStub) -> JobDetails:
        html = get_text(stub.source_url)
        fields = _fill_list_fields(html)
        description = _decode_encoded_html(fields[11]) if len(fields) > 11 else ""
        qualifications = _decode_encoded_html(fields[12]) if len(fields) > 12 else ""
        title = fields[9] if len(fields) > 9 and fields[9] else stub.title
        location = fields[13] if len(fields) > 13 and fields[13] else stub.location_text
        return JobDetails(
            external_job_id=stub.external_job_id,
            title=title,
            location_text=_clean_location(location),
            description=description or None,
            qualifications=qualifications or None,
            source_url=stub.source_url,
            apply_url=stub.apply_url,
            source_published_at=_parse_display_date((stub.raw or {}).get("posted")),
            source_updated_at=None,
        )

    @staticmethod
    def _to_stub(requisition: dict[str, Any], host: str, section: str) -> JobStub:
        contest_no = str(requisition.get("contestNo") or requisition.get("jobId"))
        columns = requisition.get("column") or []
        title_index = requisition.get("linkedColumn")
        title = (
            str(columns[title_index])
            if isinstance(title_index, int) and title_index < len(columns)
            else ""
        )
        location_indexes = requisition.get("locationsColumns") or []
        location_text: str | None = None
        if location_indexes and isinstance(location_indexes[0], int):
            index = location_indexes[0]
            if index < len(columns):
                location_text = _clean_location(_first_location(columns[index]))
        url = f"https://{host}/careersection/{section}/jobdetail.ftl?job={contest_no}&lang=en"
        # The remaining column is the display posting date ("Sep 14, 2026")
        # - the only date Taleo exposes anywhere.
        posted = next(
            (
                str(column)
                for i, column in enumerate(columns)
                if i != title_index
                and i not in location_indexes
                and _DISPLAY_DATE_RE.match(str(column))
            ),
            None,
        )
        return JobStub(
            external_job_id=contest_no,
            title=title,
            location_text=location_text,
            source_url=url,
            apply_url=url,
            source_updated_at=None,
            raw={"posted": posted},
        )


def _host_and_section(source_url: str) -> tuple[str, str]:
    parsed = urlparse(source_url)
    segments = [s for s in parsed.path.split("/") if s]
    # /careersection/{section}/moresearch.ftl - "ex" (external) is the
    # conventional default when the URL doesn't say.
    section = "ex"
    if len(segments) >= 2 and segments[0] == "careersection" and not segments[1].endswith(".ftl"):
        section = segments[1]
    return parsed.hostname or "", section


def _search_body(page_no: int, location_ids: list[str]) -> dict[str, Any]:
    return {
        "multilineEnabled": False,
        "sortingSelection": {"sortBySelectionParam": "3", "ascendingSortingOrder": "false"},
        "fieldData": {"fields": {"KEYWORD": "", "LOCATION": ""}, "valid": True},
        "filterSelectionParam": {
            "searchFilterSelections": [
                {"id": "POSTING_DATE", "selectedValues": []},
                {"id": "LOCATION", "selectedValues": list(location_ids)},
                {"id": "JOB_FIELD", "selectedValues": []},
                {"id": "JOB_TYPE", "selectedValues": []},
                {"id": "JOB_SCHEDULE", "selectedValues": []},
                {"id": "JOB_LEVEL", "selectedValues": []},
            ]
        },
        "advancedSearchFiltersSelectionParam": {
            "searchFilterSelections": [
                {"id": "ORGANIZATION", "selectedValues": []},
                {"id": "LOCATION", "selectedValues": []},
                {"id": "JOB_FIELD", "selectedValues": []},
                {"id": "URGENT_JOB", "selectedValues": []},
                {"id": "EMPLOYEE_STATUS", "selectedValues": []},
                {"id": "STUDY_LEVEL", "selectedValues": []},
                {"id": "WILL_TRAVEL", "selectedValues": []},
                {"id": "JOB_SHIFT", "selectedValues": []},
                {"id": "JOB_NUMBER", "selectedValues": []},
            ]
        },
        "pageNo": page_no,
    }


def _location_filter_ids(facets: list[dict[str, Any]]) -> list[str]:
    """Ids of LOCATION facet values naming Settings.target_country, so the
    server does the country filtering. Empty when the facet doesn't
    expose them (then everything is listed and filtered downstream)."""
    country = get_settings().target_country.lower()
    if not country:
        return []
    ids: list[str] = []
    for facet in facets:
        if str(facet.get("id") or facet.get("facetName") or "").upper() != "LOCATION":
            continue
        values = facet.get("facetValues") or facet.get("values") or facet.get("items") or []
        for value in values:
            label = str(value.get("label") or value.get("name") or value.get("description") or "")
            value_id = value.get("id") or value.get("value")
            if value_id and country in label.lower():
                ids.append(str(value_id))
    return ids


def _first_location(raw: Any) -> str | None:
    if isinstance(raw, str) and raw.startswith("["):
        try:
            decoded = json.loads(raw)
        except json.JSONDecodeError:
            return raw
        return str(decoded[0]) if decoded else None
    return str(raw) if raw else None


def _clean_location(value: str | None) -> str | None:
    # "IL-IL-Tel Aviv" -> "Tel Aviv, IL": the leading codes are the country
    # and region, the readable city comes last.
    if not value:
        return None
    parts = [part.strip() for part in value.split("-") if part.strip()]
    if len(parts) >= 3 and len(parts[0]) == 2:
        return f"{'-'.join(parts[2:])}, {parts[0]}"
    return value


def _fill_list_fields(html: str) -> list[str]:
    match = _FILL_LIST_RE.search(html)
    if not match:
        return []
    return [
        m.group(1).replace("\\'", "'").replace("\\\\", "\\")
        for m in _JS_STRING_RE.finditer(match.group(1))
    ]


def _parse_display_date(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.strptime(value, "%b %d, %Y").replace(tzinfo=UTC)
    except ValueError:
        return None


def _decode_encoded_html(value: str) -> str:
    if value.startswith(_ENCODED_HTML_PREFIX):
        value = value[len(_ENCODED_HTML_PREFIX) :]
    return html_to_text(unquote(value))

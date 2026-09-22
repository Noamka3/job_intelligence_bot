"""Job lists found in the JSON a page fetched while it was being rendered.

Many career pages draw their list from an API of their own (Ness calls
/careers/api/Careers/GetAllItems, Elbit /cron/jobs.json) and paint it as
cards without links, so the rendered HTML shows nothing to follow. The
browser adapter records the JSON responses the page's own scripts
receive; this module decides whether one of them is a list of postings
and turns it into stubs and details.

Only JSON is read, never executed, from the page's own host only, and
capped in size - a hostile site can hand the crawler nothing but a
document to parse.
"""

from __future__ import annotations

import hashlib
from datetime import datetime
from typing import Any
from urllib.parse import urljoin

from app.ingestion.adapters._util import map_employment_type, parse_timestamp
from app.ingestion.adapters.base import JobDetails, JobStub
from app.ingestion.adapters.generic_html import looks_like_a_job_title
from app.services.jobs.html_text import html_to_text

# Field names seen across real feeds, most specific first. Matching is
# case-insensitive on the key.
_TITLE_KEYS = ("jobtitle", "positionname", "position_name", "title", "position", "name", "tl")
_URL_KEYS = ("joburl", "applyurl", "url", "link", "href", "permalink", "slug")
_ID_KEYS = ("jobid", "job_id", "positionid", "position_id", "recid", "uid", "id")
_DESCRIPTION_KEYS = (
    "jobdescription",
    "description",
    "requirements",
    "summary",
    "body",
    "content",
    "text",
    "details",
    "dc",
)
_LOCATION_KEYS = ("locationname", "location", "city", "area", "region", "site", "ct")
_DATE_KEYS = ("dateposted", "publishdate", "published", "opendate", "created", "date")
_EMPLOYMENT_KEYS = ("employmenttype", "jobtype", "scope", "tp")

_MIN_ROWS = 3
_MAX_WALK_DEPTH = 6
_MAX_ROWS = 500
# A list of objects that have titles is not yet a list of jobs: a site's
# navigation, its client logos and the font catalogue Wix ships all look
# exactly like that, and they all got through when "has a title" was the
# whole test (Compie's client list came back as a job called "Bank
# Hapoalim"). Field names don't settle it either - Compie's *product*
# cards carry a "description" while its real postings keep theirs under
# "AboutTheRole". What does settle it is whether the titles read like
# job titles. Measured across the captured feeds of four real sites:
#
#   Ness      real postings  207 rows   45%      noise 0%
#   Compie    real postings   14 rows  100%      noise 0-40%
#   Bluevoyant real postings   7 rows  100%      noise 0%
#   high lander  (no postings)                   noise 0%
#
# So: the candidate with the highest share wins, and it has to clear
# this bar. The closest noise seen was a management team page at 40%,
# whose titles ("VP HR", "Deputy CEO") are job titles - of people who
# already hold them - and which score far too senior to ever surface.
_MIN_JOB_TITLE_SHARE = 0.4
# Marks a stub as carrying its whole posting in `raw`, so the crawler
# reads the details from it instead of fetching a page that doesn't exist.
_FEED_MARKER = "__from_feed__"


def is_feed_stub(stub: JobStub) -> bool:
    return bool(stub.raw and stub.raw.get(_FEED_MARKER))


def stubs_from_feeds(feeds: list[Any], page_url: str) -> list[JobStub]:
    """Stubs from whichever captured document holds the best job list.

    Every document is weighed, not just the first: a page fetches its
    menu, its product cards and its postings, often in that order.
    """
    best: list[dict[str, Any]] = []
    best_rank = (0.0, 0)
    for document in feeds:
        rows, share = _job_rows(document)
        rank = (share, len(rows))
        if rows and rank > best_rank:
            best, best_rank = rows, rank
    return [stub for row in best[:_MAX_ROWS] if (stub := _to_stub(row, page_url))]


def details_from_stub(stub: JobStub) -> JobDetails:
    row = stub.raw or {}
    description = "\n\n".join(
        text for key in _DESCRIPTION_KEYS if (text := html_to_text(_get(row, key)))
    )
    return JobDetails(
        external_job_id=stub.external_job_id,
        title=stub.title,
        location_text=stub.location_text,
        employment_type=map_employment_type(_get(row, *_EMPLOYMENT_KEYS)),
        description=description or None,
        source_url=stub.source_url,
        apply_url=stub.apply_url,
        source_published_at=_date(row),
    )


def _job_rows(document: Any) -> tuple[list[dict[str, Any]], float]:
    """(the list in this document that reads most like postings, and how
    strongly it does). Empty when nothing clears the bar."""
    best: list[dict[str, Any]] = []
    best_rank = (0.0, 0)
    for candidate in _lists_of_objects(document, 0):
        titled = [row for row in candidate if _get(row, *_TITLE_KEYS)]
        if len(titled) < _MIN_ROWS:
            continue
        share = _job_title_share(titled)
        rank = (share, len(titled))
        if share >= _MIN_JOB_TITLE_SHARE and rank > best_rank:
            best, best_rank = titled, rank
    return best, best_rank[0]


def _job_title_share(rows: list[dict[str, Any]]) -> float:
    titles = [str(_get(row, *_TITLE_KEYS) or "") for row in rows]
    return sum(1 for title in titles if looks_like_a_job_title(title)) / len(titles)


def _lists_of_objects(node: Any, depth: int) -> list[list[dict[str, Any]]]:
    if depth > _MAX_WALK_DEPTH:
        return []
    found: list[list[dict[str, Any]]] = []
    if isinstance(node, list):
        objects = [item for item in node if isinstance(item, dict)]
        if len(objects) >= _MIN_ROWS:
            found.append(objects)
        for item in node[:50]:
            found.extend(_lists_of_objects(item, depth + 1))
    elif isinstance(node, dict):
        for value in node.values():
            found.extend(_lists_of_objects(value, depth + 1))
    return found


def _to_stub(row: dict[str, Any], page_url: str) -> JobStub | None:
    title = str(_get(row, *_TITLE_KEYS) or "").strip()
    if not title:
        return None
    url = _url(row, page_url)
    location = _get(row, *_LOCATION_KEYS)
    identifier = _get(row, *_ID_KEYS)
    external_id = str(identifier) if identifier not in (None, "") else _hash(url or title, location)
    return JobStub(
        external_job_id=external_id,
        title=title,
        location_text=str(location) if location else None,
        source_url=url or page_url,
        apply_url=url,
        raw={**row, _FEED_MARKER: True},
    )


def _get(row: dict[str, Any], *keys: str) -> Any:
    """The first non-empty scalar among the keys, matched case-insensitively."""
    lowered = {str(k).lower(): v for k, v in row.items()}
    for key in keys:
        value = lowered.get(key)
        if isinstance(value, dict):
            value = value.get("name") or value.get("title") or value.get("value")
        if value not in (None, "", [], {}) and not isinstance(value, list | dict):
            return value
    return None


def _url(row: dict[str, Any], page_url: str) -> str | None:
    value = _get(row, *_URL_KEYS)
    if not isinstance(value, str) or not value.strip():
        return None
    resolved = urljoin(page_url, value.strip())
    return resolved if resolved.startswith(("http://", "https://")) else None


def _date(row: dict[str, Any]) -> datetime | None:
    return parse_timestamp(_get(row, *_DATE_KEYS))


def _hash(*parts: Any) -> str:
    return hashlib.sha256("|".join(str(p) for p in parts).encode("utf-8")).hexdigest()[:32]

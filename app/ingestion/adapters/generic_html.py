"""Generic career-page adapter (spec §17): no API, no known ATS, a plain
page listing jobs as links to their own pages. The last resort before a
real browser.

Listing pages differ wildly, so instead of a fixed selector the job list
is found from the page's own structure: every same-site link with
title-like text is grouped by its DOM shape (the anchor's tag/class plus
two ancestors), and the largest group whose links look like job pages
(job-ish URL path or role words in the text) is taken as the list -
repeated markup is what a rendered list *is*. Stray links that clearly
name a role are kept too.

A job page is read through its schema.org JobPosting JSON-LD when it has
one (many do, for Google for Jobs), else <h1>/og:title for the title and
the main content block for the description. Location is left unknown
when the page doesn't say - never guessed (spec §22).

Surveyed against the sheet's ~130 such pages (docs/job_sources.md): 59
render 5+ job links server-side; JS-rendered ones and WAF-blocked ones
need the browser fallback, not this.
"""

from __future__ import annotations

import hashlib
import logging
import re
from collections import defaultdict
from typing import Any
from urllib.parse import urldefrag, urljoin, urlparse

from bs4 import BeautifulSoup, Tag

from app.ingestion.adapters._http import get_page
from app.ingestion.adapters._util import parse_timestamp
from app.ingestion.adapters.base import JobDetails, JobStub
from app.ingestion.adapters.jsonld import _extract_job_postings, _extract_location_text
from app.ingestion.adapters.jsonld import _map_employment_type as _jsonld_employment_type
from app.models.career_source import CareerSource
from app.services.jobs.html_text import html_to_text

logger = logging.getLogger(__name__)

_MAX_JOBS_PER_LISTING = 300
_MAX_DESCRIPTION_CHARS = 20_000

_JOBISH_PATH_RE = re.compile(
    r"job|career|position|vacanc|opening|opportunit|role|משרה|משרות|דרושים|קריירה", re.I
)
_ROLE_WORD_RE = re.compile(
    r"\b(?:engineer|developer|programmer|architect|analyst|scientist|manager|designer|devops"
    r"|qa|tester|specialist|lead|intern|student|researcher|administrator|consultant|product"
    r"|data|software|backend|frontend|full[- ]?stack|support|sales|marketing|hr|finance"
    r"|operations|coordinator|director|head|officer|technician|team)\b"
    r"|מפתח|מהנדס|מנהל|אנליסט|בודק|ארכיטקט|מתכנת|סטודנט|ראש צוות|מומחה|יועץ|תומך|רכז|נציג",
    re.I,
)
_NAV_TEXT_RE = re.compile(
    r"^(?:home|careers?|jobs|about(?: us)?|contact(?: us)?|apply(?: now)?|read more|learn more"
    r"|more|all jobs|open positions|view all|see all|back|next|previous|prev|login|sign in"
    r"|share|privacy|terms|search|filter|menu|close|\d+|»|«|>|<)$",
    re.I,
)
# A link whose text is only a call to action ("View Details") names its
# job somewhere up in the card that contains it.
_CTA_TEXT_RE = re.compile(
    r"^(?:view(?: details?| job| position| more)?|read more|learn more|more info(?:rmation)?"
    r"|details|apply(?: now| here)?|see (?:more|details|job)|open|go"
    r"|לפרטים(?: נוספים)?|למשרה(?: המלאה)?|הגש(?:/י)? מועמדות|קרא(?:/י)? עוד|עוד"
    r"|פרטים(?: נוספים)?)$",
    re.I,
)
_HEADINGS = ("h1", "h2", "h3", "h4", "h5", "h6")
_TITLE_CLASS_RE = re.compile(r"title|position-name|job-name|role", re.I)
_SKIP_SCHEMES = ("mailto:", "tel:", "javascript:", "#")
_SKIP_EXTENSIONS = (".pdf", ".png", ".jpg", ".jpeg", ".gif", ".svg", ".zip", ".doc", ".docx")
_STRIP_TAGS = ("script", "style", "nav", "header", "footer", "aside", "noscript", "form")


class GenericHtmlAdapter:
    def list_jobs(self, source: CareerSource) -> list[JobStub]:
        final_url, html = get_page(source.source_url, browser_like=True)
        links = extract_job_links(html, final_url)
        return [
            JobStub(
                external_job_id=_job_id(url),
                title=title,
                source_url=url,
                apply_url=url,
                source_updated_at=None,
            )
            for url, title in links[:_MAX_JOBS_PER_LISTING]
        ]

    def fetch_job(self, source: CareerSource, stub: JobStub) -> JobDetails:
        _, html = get_page(stub.source_url, browser_like=True)
        postings = _extract_job_postings(html)
        if postings:
            return _details_from_jsonld(postings[0], stub)
        return _details_from_page(html, stub)


def extract_job_links(html: str, page_url: str) -> list[tuple[str, str]]:
    """[(absolute job URL, title)] in page order, de-duplicated."""
    soup = BeautifulSoup(html, "lxml")
    page_host = _registrable_host(urlparse(page_url).hostname or "")

    candidates: list[tuple[str, str, tuple[str, ...]]] = []
    for anchor in soup.find_all("a", href=True):
        href = str(anchor["href"]).strip()
        if not href or href.lower().startswith(_SKIP_SCHEMES):
            continue
        url, _ = urldefrag(urljoin(page_url, href))
        parsed = urlparse(url)
        if parsed.scheme not in ("http", "https"):
            continue
        if _registrable_host(parsed.hostname or "") != page_host:
            continue
        if parsed.path.lower().endswith(_SKIP_EXTENSIONS):
            continue
        if url.rstrip("/") == page_url.rstrip("/"):
            continue
        title = _anchor_title(anchor)
        if not title:
            continue
        candidates.append((url, title, _shape(anchor)))

    by_shape: dict[tuple[str, ...], list[tuple[str, str]]] = defaultdict(list)
    for url, title, shape in candidates:
        by_shape[shape].append((url, title))

    best: list[tuple[str, str]] = []
    best_score = 0
    for group in by_shape.values():
        if len(group) < 3:
            continue
        jobish = sum(1 for url, title in group if _looks_like_job(url, title))
        if jobish < max(3, len(group) // 2):
            continue
        if jobish > best_score:
            best, best_score = group, jobish

    chosen: dict[str, str] = {url: title for url, title in best}
    for url, title, _ in candidates:
        if url not in chosen and _ROLE_WORD_RE.search(title) and _JOBISH_PATH_RE.search(
            urlparse(url).path
        ):
            chosen[url] = title
    return list(chosen.items())


def _clean(text: str) -> str:
    return " ".join(text.split())


def _anchor_title(anchor: Tag) -> str | None:
    # A card-sized anchor ("<a><h3>Backend Developer</h3><span>Full-time
    # Tel Aviv</span><span>Read more</span></a>") names the job in its
    # heading, not in all of its text.
    heading = anchor.find(_HEADINGS) or anchor.find(class_=_TITLE_CLASS_RE)
    text = _clean(heading.get_text(" ", strip=True)) if isinstance(heading, Tag) else ""
    if not text:
        text = _clean(anchor.get_text(" ", strip=True))
    if not text:
        for attribute in ("aria-label", "title"):
            value = anchor.get(attribute)
            if isinstance(value, str) and value.strip():
                text = _clean(value)
                break
    if _CTA_TEXT_RE.match(text):
        text = _title_from_card(anchor) or ""
    if not text or len(text) < 3 or len(text) > 120:
        return None
    if _NAV_TEXT_RE.match(text) or not re.search(r"[A-Za-z֐-׿]", text):
        return None
    return text


def _title_from_card(anchor: Tag) -> str | None:
    node: Tag | None = anchor.parent if isinstance(anchor.parent, Tag) else None
    for _ in range(4):
        if node is None:
            return None
        heading = node.find(_HEADINGS) or node.find(class_=_TITLE_CLASS_RE)
        if isinstance(heading, Tag):
            text = _clean(heading.get_text(" ", strip=True))
            if text:
                return text
        node = node.parent if isinstance(node.parent, Tag) else None
    return None


def _shape(anchor: Tag) -> tuple[str, ...]:
    parts: list[str] = []
    node: Tag | None = anchor
    for _ in range(3):
        if node is None or not isinstance(node, Tag):
            break
        classes = node.get("class")
        first_class = classes[0] if isinstance(classes, list) and classes else ""
        parts.append(f"{node.name}.{first_class}")
        node = node.parent if isinstance(node.parent, Tag) else None
    return tuple(parts)


def _looks_like_job(url: str, title: str) -> bool:
    return bool(_JOBISH_PATH_RE.search(urlparse(url).path)) or bool(_ROLE_WORD_RE.search(title))


def _registrable_host(hostname: str) -> str:
    labels = hostname.lower().split(".")
    # Good enough for career pages: keep the last two labels, or three
    # for two-level public suffixes like .co.il / .co.uk.
    if len(labels) >= 3 and labels[-2] in ("co", "com", "org", "net", "ac", "gov"):
        return ".".join(labels[-3:])
    return ".".join(labels[-2:])


def _job_id(url: str) -> str:
    return hashlib.sha256(url.encode("utf-8")).hexdigest()[:32]


def _details_from_jsonld(posting: dict[str, Any], stub: JobStub) -> JobDetails:
    return JobDetails(
        external_job_id=stub.external_job_id,
        title=str(posting.get("title") or stub.title),
        location_text=_extract_location_text(posting),
        employment_type=_jsonld_employment_type(posting.get("employmentType")),
        description=html_to_text(posting.get("description")) or None,
        source_url=stub.source_url,
        apply_url=stub.apply_url,
        source_published_at=parse_timestamp(posting.get("datePosted")),
        source_updated_at=parse_timestamp(posting.get("dateModified")),
    )


def _details_from_page(html: str, stub: JobStub) -> JobDetails:
    soup = BeautifulSoup(html, "lxml")
    title = _page_title(soup) or stub.title

    for tag in soup(_STRIP_TAGS):
        tag.decompose()
    container = soup.select_one("main, article, [role=main]") or soup.body or soup
    description = html_to_text(str(container))[:_MAX_DESCRIPTION_CHARS]
    return JobDetails(
        external_job_id=stub.external_job_id,
        title=title,
        description=description or None,
        source_url=stub.source_url,
        apply_url=stub.apply_url,
        source_updated_at=None,
    )


def _page_title(soup: BeautifulSoup) -> str | None:
    h1 = soup.find("h1")
    if h1 is not None:
        text = " ".join(h1.get_text(" ", strip=True).split())
        if text:
            return text
    og = soup.find("meta", attrs={"property": "og:title"})
    if isinstance(og, Tag):
        content = og.get("content")
        if isinstance(content, str) and content.strip():
            return content.strip()
    if soup.title is not None and soup.title.string:
        return soup.title.string.strip().split("|")[0].split(" - ")[0].strip() or None
    return None

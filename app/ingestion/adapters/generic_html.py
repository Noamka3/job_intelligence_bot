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

import httpx
from bs4 import BeautifulSoup, Tag

from app.ingestion.adapters._http import get_page
from app.ingestion.adapters._util import parse_timestamp
from app.ingestion.adapters.base import JobDetails, JobStub, JobUnavailableError
from app.ingestion.adapters.jsonld import _extract_job_postings, _extract_location_text
from app.ingestion.adapters.jsonld import _map_employment_type as _jsonld_employment_type
from app.models.career_source import CareerSource
from app.services.jobs.html_text import html_to_text
from app.services.jobs.location import classify_country, is_confidently_non_israeli

logger = logging.getLogger(__name__)

_MAX_JOBS_PER_LISTING = 300
_MAX_DESCRIPTION_CHARS = 20_000
_LISTING_LINK_THRESHOLD = 5
_LOCATION_SCAN_LINES = 30
_MIN_BODY_CHARS = 400
_APPLY_RE = re.compile(r"apply|submit|הגש|הגישו|מועמדות|שלח(?:ו|/י)? קורות", re.I)

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
_TITLE_WORD_SPLIT_RE = re.compile(r"[\s/,\-–|]+")
_HEADINGS = ("h1", "h2", "h3", "h4", "h5", "h6")
_TITLE_CLASS_RE = re.compile(r"title|position-name|job-name|role", re.I)
_SKIP_SCHEMES = ("mailto:", "tel:", "javascript:", "#")
_SKIP_EXTENSIONS = (".pdf", ".png", ".jpg", ".jpeg", ".gif", ".svg", ".zip", ".doc", ".docx")
_STRIP_TAGS = ("script", "style", "nav", "header", "footer", "aside", "noscript", "form")


class GenericHtmlAdapter:
    def list_jobs(self, source: CareerSource) -> list[JobStub]:
        final_url, html = get_page(source.source_url, browser_like=True)
        return stubs_from_links(extract_job_links(html, final_url))

    def fetch_job(self, source: CareerSource, stub: JobStub) -> JobDetails:
        try:
            final_url, html = get_page(stub.source_url, browser_like=True)
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code in (404, 410):
                raise JobUnavailableError(stub.source_url, "job page is gone") from exc
            raise
        return details_from_html(html, final_url, stub)


def stubs_from_links(links: list[tuple[str, str]]) -> list[JobStub]:
    return [
        JobStub(
            external_job_id=job_id_for_url(url),
            title=title,
            source_url=url,
            apply_url=url,
            source_updated_at=None,
        )
        for url, title in links[:_MAX_JOBS_PER_LISTING]
    ]


def details_from_html(html: str, final_url: str, stub: JobStub) -> JobDetails:
    """A job page's content: its JSON-LD JobPosting when it has one, else
    its headline and main text. Shared with the browser fallback, which
    reads the same pages after rendering them."""
    postings = _extract_job_postings(html)
    if postings:
        return _details_from_jsonld(postings[0], stub)
    if _is_listing_page(html, final_url):
        # A listing links to what turns out to be another listing
        # (a recruiting agency's category page, "Explore open jobs
        # at ..."), not a posting - seen on real sheet pages.
        raise JobUnavailableError(stub.source_url, "page is a job listing, not a job")
    return _details_from_page(html, stub)


def extract_job_links(html: str, page_url: str) -> list[tuple[str, str]]:
    """[(absolute job URL, title)] in page order, de-duplicated."""
    soup = BeautifulSoup(html, "lxml")
    page_host = registrable_host(urlparse(page_url).hostname or "")

    candidates: list[tuple[str, str, tuple[str, ...]]] = []
    for anchor in soup.find_all("a", href=True):
        href = str(anchor["href"]).strip()
        if not href or href.lower().startswith(_SKIP_SCHEMES):
            continue
        url, _ = urldefrag(urljoin(page_url, href))
        parsed = urlparse(url)
        if parsed.scheme not in ("http", "https"):
            continue
        if registrable_host(parsed.hostname or "") != page_host:
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
        if (
            url not in chosen
            and _ROLE_WORD_RE.search(title)
            and _JOBISH_PATH_RE.search(urlparse(url).path)
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


def looks_like_a_job_title(title: str) -> bool:
    """A role word, in a title of more than one bare word. The second
    half matters: "head" and "data" are role words, and as a lone word
    they are a Wix layout node or a menu entry, not a posting."""
    words = [word for word in _TITLE_WORD_SPLIT_RE.split(title.strip()) if word]
    return len(words) >= 2 and bool(_ROLE_WORD_RE.search(title))


def registrable_host(hostname: str) -> str:
    """ "acme.co.il" for careers.acme.co.il - what "the same site" means
    for a link on a page, and for a feed the page fetched."""
    labels = hostname.lower().split(".")
    # Good enough for career pages: keep the last two labels, or three
    # for two-level public suffixes like .co.il / .co.uk.
    if len(labels) >= 3 and labels[-2] in ("co", "com", "org", "net", "ac", "gov"):
        return ".".join(labels[-3:])
    return ".".join(labels[-2:])


def job_id_for_url(url: str) -> str:
    """The stable id of a job that has nothing but its page URL. Shared
    with the WordPress adapter, so a site that moves from the generic
    reader to its REST API keeps its stored jobs instead of re-creating
    them under new ids."""
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


# A search-results header no single posting carries: "מצאנו עבורך 28
# משרות" (Nisha Group, GotFriends), "Showing 12 of 40 jobs".
_RESULTS_COUNT_RE = re.compile(
    r"מצאנו\s+(?:עבורך|לך|עבורכם)?\s*\d+\s+משרות|נמצאו\s+\d+\s+משרות|\d+\s+משרות\s+נמצאו"
    r"|showing\s+\d+\s+(?:of\s+\d+\s+)?(?:jobs|results|positions)"
    r"|\d+\s+(?:jobs|results|positions)\s+found",
    re.IGNORECASE,
)


def _is_listing_page(html: str, page_url: str) -> bool:
    """A page that itself links to many job pages and offers no way to
    apply is a listing, not a posting. A real posting with a "more jobs"
    sidebar still has its apply button/form (Island, Moveo), so those are
    kept - unless the page announces a results count, which only a
    search/category page does (the agency sites, which also render an
    apply button on those pages, stored 28 jobs as one "job" this way)."""
    job_links = len(extract_job_links(html, page_url))
    if job_links < 2:
        return False
    soup = BeautifulSoup(html, "lxml")
    if _RESULTS_COUNT_RE.search(soup.get_text(" ", strip=True)):
        return True
    if job_links < _LISTING_LINK_THRESHOLD:
        return False
    if soup.find("form") is not None:
        return False
    for anchor in soup.find_all(["a", "button"]):
        text = anchor.get_text(" ", strip=True)
        href = anchor.get("href") if anchor.name == "a" else None
        if _APPLY_RE.search(text) or (isinstance(href, str) and _APPLY_RE.search(href)):
            return False
    return True


def _guess_location(text: str) -> str | None:
    """Job pages without structured data usually print the location as a
    short line of its own near the top ("Remote US", "Rishon LeZion",
    "Tel Aviv, Israel"); only a line the location vocabulary recognizes
    is used - never a guess from prose (spec §22)."""
    for line in text.split("\n")[:_LOCATION_SCAN_LINES]:
        candidate = line.strip()
        if not candidate or len(candidate) > 60:
            continue
        if classify_country(candidate) == "Israel" or is_confidently_non_israeli(candidate):
            return candidate
    return None


def _strip_job_link_lists(soup: BeautifulSoup, page_url: str) -> None:
    """Remove "more positions" widgets: any block that is mostly links to
    other job pages. Seen on real job pages (Island, Buildots): a sidebar
    listing every open position, which otherwise ends up inside this
    job's description - and its embedding."""
    containers = soup.find_all(["ul", "ol", "aside", "nav", "section", "div"])
    # Innermost first, so a list is removed before its ancestor is judged.
    containers.sort(key=lambda node: len(list(node.parents)), reverse=True)
    for container in containers:
        if container.parent is None:  # already removed with an ancestor
            continue
        job_links = [
            anchor
            for anchor in container.find_all("a", href=True)
            if _JOBISH_PATH_RE.search(urlparse(urljoin(page_url, str(anchor["href"]))).path)
        ]
        if len(job_links) < 3:
            continue
        text_length = len(container.get_text(" ", strip=True))
        link_text_length = sum(len(a.get_text(" ", strip=True)) for a in job_links)
        if text_length and link_text_length >= 0.5 * text_length:
            container.decompose()


def _body_container(soup: BeautifulSoup) -> Tag | BeautifulSoup:
    """The block the posting itself lives in: the nearest ancestor of the
    page's <h1> with a real amount of text, rather than all of <main>."""
    heading = soup.find("h1")
    node: Tag | None = heading.parent if isinstance(heading, Tag) else None
    while isinstance(node, Tag) and node.name not in ("body", "html"):
        if len(node.get_text(" ", strip=True)) >= _MIN_BODY_CHARS:
            return node
        node = node.parent if isinstance(node.parent, Tag) else None
    return soup.select_one("main, article, [role=main]") or soup.body or soup


def _details_from_page(html: str, stub: JobStub) -> JobDetails:
    soup = BeautifulSoup(html, "lxml")
    title = _page_title(soup, stub.title)

    for tag in soup(_STRIP_TAGS):
        tag.decompose()
    _strip_job_link_lists(soup, stub.source_url)
    text = html_to_text(str(_body_container(soup)))
    if _is_generic_title(title):
        title = _first_title_like_line(text) or title
    return JobDetails(
        external_job_id=stub.external_job_id,
        title=title,
        location_text=_guess_location(text),
        description=text[:_MAX_DESCRIPTION_CHARS] or None,
        source_url=stub.source_url,
        apply_url=stub.apply_url,
        source_updated_at=None,
    )


# Section names sites put in the <h1> of every job page ("משרות" on
# logica-it.com, "Careers" on hibob.com - 330 stored jobs carried one of
# these as their title). Never a job title on its own.
_GENERIC_TITLES = frozenset(
    {
        "משרות",
        "משרה",
        "דרושים",
        "קריירה",
        "חיפוש משרה",
        "jobs",
        "job",
        "careers",
        "career",
        "open positions",
        "positions",
        "vacancies",
        "opportunities",
        "join us",
        "join our team",
        "work with us",
        "job details",
        "job description",
    }
)
_MAX_TITLE_CHARS = 120


def _is_generic_title(text: str | None) -> bool:
    return not text or text.strip().casefold() in _GENERIC_TITLES


def _page_title(soup: BeautifulSoup, stub_title: str) -> str:
    """First non-generic candidate: the page's <h1>, the link text the
    listing page used for this job, a sub-heading, og:title, <title>."""
    candidates: list[str | None] = []
    h1 = soup.find("h1")
    if h1 is not None:
        candidates.append(" ".join(h1.get_text(" ", strip=True).split()))
    candidates.append(stub_title)
    for heading in soup.find_all(("h2", "h3"), limit=4):
        candidates.append(" ".join(heading.get_text(" ", strip=True).split()))
    og = soup.find("meta", attrs={"property": "og:title"})
    if isinstance(og, Tag):
        content = og.get("content")
        if isinstance(content, str):
            candidates.append(content.strip())
    if soup.title is not None and soup.title.string:
        candidates.append(soup.title.string.strip().split("|")[0].split(" - ")[0].strip())
    for candidate in candidates:
        if not _is_generic_title(candidate) and len(str(candidate)) <= _MAX_TITLE_CHARS:
            return str(candidate)
    return stub_title


def _first_title_like_line(text: str) -> str | None:
    """Last resort when every heading is a section name: the first short
    line of the posting body that isn't one ("Verification Engineer"
    right after "משרות / חיפוש משרה" on logica-it.com)."""
    for line in text.splitlines()[:8]:
        line = line.strip()
        if line and not _is_generic_title(line) and len(line) <= _MAX_TITLE_CHARS:
            return line
    return None

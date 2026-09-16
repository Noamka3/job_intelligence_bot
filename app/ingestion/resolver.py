"""CareerSourceResolver: classifies a company's raw sheet URL into a
CareerSourceType, so the ingestion layer knows which adapter should
handle it. Never scrapes LinkedIn - see spec §14.

Resolution is hostname-based first (fast, no network call) for every ATS
this project knows about. Anything else gets one bounded HTTP probe of
the page itself, which is checked - in this order - for:

1. An *embedded* ATS. Surveyed against the real sheet (docs/job_sources.md):
   ~45 of the ~190 "custom" company pages just wrap a Comeet, Workday,
   Greenhouse, Ashby or Workable board in their own domain. The board is
   what gets crawled (`ResolvedSource.board_url`), with the adapter that
   already exists for it.
2. schema.org JobPosting JSON-LD on the page (`jsonld`).
3. Otherwise `generic_html`.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from urllib.parse import ParseResult, parse_qs, urljoin, urlparse

import httpx

from app.models.enums import CareerSourceType

logger = logging.getLogger(__name__)

_JSON_LD_BLOCK_RE = re.compile(
    r'<script[^>]+type=["\']application/ld\+json["\'][^>]*>(.*?)</script>',
    re.IGNORECASE | re.DOTALL,
)

_HOSTNAME_PATTERNS: tuple[tuple[str, CareerSourceType], ...] = (
    ("comeet.com", CareerSourceType.COMEET),
    ("comeet.co", CareerSourceType.COMEET),
    ("greenhouse.io", CareerSourceType.GREENHOUSE),
    ("lever.co", CareerSourceType.LEVER),
    ("ashbyhq.com", CareerSourceType.ASHBY),
    ("smartrecruiters.com", CareerSourceType.SMARTRECRUITERS),
    ("workable.com", CareerSourceType.WORKABLE),
    ("myworkdayjobs.com", CareerSourceType.WORKDAY),
    ("taleo.net", CareerSourceType.TALEO),
)

# Embedded boards are usually referenced from footer scripts, so the
# probe reads more of the page than JSON-LD detection alone needed.
_MAX_PROBE_BYTES = 600_000
_PROBE_TIMEOUT_SECONDS = 8.0
_PROBE_USER_AGENT = "job-intel-bot/0.1 (career source resolver probe)"

_SLUG = r"[A-Za-z0-9_.-]+"
# Comeet company uids look like "17.008", "63.00B", "F1.008".
_COMEET_UID = r"[A-Z0-9]{2}\.[A-Z0-9]{3}"
_COMEET_BOARD_LINK_RE = re.compile(
    rf"https?://(?:www\.)?comeet\.com/jobs/({_SLUG})/({_COMEET_UID})(?![A-Za-z0-9])"
)
_COMEET_INIT_UID_RE = re.compile(rf"[\"']company-uid[\"']\s*:\s*[\"']({_COMEET_UID})[\"']")
_GREENHOUSE_EMBED_RE = re.compile(
    rf"boards\.greenhouse\.io/embed/job_board(?:/js)?\?(?:[^\"'\s]*?&)?for=({_SLUG})"
)
_GREENHOUSE_BOARD_RE = re.compile(rf"(?:job-boards|boards)\.greenhouse\.io/({_SLUG})(?=[/\"'?\s])")
_LEVER_RE = re.compile(rf"jobs\.lever\.co/({_SLUG})(?=[/\"'?\s]|$)")
_ASHBY_RE = re.compile(
    rf"(?:jobs\.ashbyhq\.com|api\.ashbyhq\.com/posting-api/job-board)/({_SLUG})(?=[/\"'?\s]|$)"
)
_WORKABLE_RE = re.compile(
    rf"apply\.workable\.com/(?:api/v\d+/widget/accounts/)?({_SLUG})(?=[/\"'?\s]|$)"
)
# Protocol-relative ("//sec.wd3.myworkdayjobs.com/...") links are real
# (Samsung's page); the path group stops before any query/fragment.
_WORKDAY_RE = re.compile(
    r"(?:https?:)?//([a-z0-9-]+)\.(wd\d+)\.myworkdayjobs\.com(/[^\s\"'<>?#]*)?", re.I
)
# The Comeet WordPress plugin renders the list server-side under the
# company's own domain (Buildots, Kaltura, hibob) with no credentials in
# the HTML - but each position page carries the company uid, and the
# public board for that uid has the token. Markers seen on real pages:
_COMEET_PLUGIN_MARKER_RE = re.compile(
    r"comeet\.co/careers-api/api\.js|comeet-outer-wrapper|comeet_style-css|comeet-groups-list",
    re.I,
)
_COMEET_POSITION_LINK_RE = re.compile(rf"href=[\"']([^\"'\s]*/{_COMEET_UID}/?[^\"'\s]*)[\"']")
_COMEET_ANY_UID_RE = re.compile(rf"company[-_]uid[\"']?\s*[:=]\s*[\"']({_COMEET_UID})[\"']")
_MAX_POSITION_PROBES = 2
# Boards that a company page loads purely from JS leave no board URL in
# the HTML (JFrog, AppsFlyer) - only a hint that the ATS is in play. The
# board token is then the company's own domain label, *verified* by the
# ATS's public API answering for it: no guess is ever stored unverified.
_BoardProbe = tuple[CareerSourceType, re.Pattern[str], str, str, str | None]
_BOARD_BY_DOMAIN_LABEL: tuple[_BoardProbe, ...] = (
    (
        CareerSourceType.GREENHOUSE,
        re.compile(r"greenhouse|[?&]gh_(?:department|jid|src)=", re.I),
        "https://boards-api.greenhouse.io/v1/boards/{slug}/jobs",
        "https://job-boards.greenhouse.io/{slug}",
        "jobs",
    ),
    (
        CareerSourceType.LEVER,
        re.compile(r"lever\.co|\blever\b", re.I),
        "https://api.lever.co/v0/postings/{slug}?mode=json",
        "https://jobs.lever.co/{slug}",
        None,
    ),
    (
        CareerSourceType.ASHBY,
        re.compile(r"ashby", re.I),
        "https://api.ashbyhq.com/posting-api/job-board/{slug}",
        "https://jobs.ashbyhq.com/{slug}",
        "jobs",
    ),
)
_SMARTRECRUITERS_RE = re.compile(
    rf"(?:(?:jobs|careers)\.smartrecruiters\.com|api\.smartrecruiters\.com/v1/companies)/({_SLUG})"
    r"(?=[/\"'?\s]|$)"
)
_LOCALE_SEGMENT_RE = re.compile(r"^[a-z]{2}(?:-[A-Za-z]{2,4})?$")
_NOT_A_BOARD_SLUG = frozenset({"embed", "api", "j", "static", "assets", "css", "js", "widget"})
_NOT_A_WORKDAY_SITE = frozenset({"wday", "job", "login", "page", "en", "he"})


@dataclass(frozen=True)
class ResolvedSource:
    source_type: CareerSourceType
    external_identifier: str | None
    unsupported_reason: str | None = None
    # The URL the adapter should actually crawl when it isn't the sheet
    # cell itself - the canonical board behind a company page that embeds
    # one. None means "crawl the cell's URL".
    board_url: str | None = None


def normalize_source_url(url: str | None) -> str | None:
    """The URL form actually stored and fetched: trimmed, with a scheme.
    Sheet cells are often pasted without one ("www.comeet.com/jobs/...")
    and httpx refuses scheme-less URLs outright, so resolving against a
    prefixed copy but storing the raw cell would make every later crawl
    of that source fail.
    """
    if not url or not url.strip():
        return None
    url = url.strip()
    return url if "://" in url else f"https://{url}"


def _hostname_matches(hostname: str, domain: str) -> bool:
    # Suffix match on label boundaries, not substring: "clever.co" and
    # "greenhouse.io.example.net" must not resolve as Lever/Greenhouse.
    return hostname == domain or hostname.endswith("." + domain)


def resolve_career_source(url: str | None) -> ResolvedSource:
    url = normalize_source_url(url)
    if url is None:
        return ResolvedSource(CareerSourceType.UNSUPPORTED, None, "missing_url")

    try:
        parsed = urlparse(url)
    except ValueError:
        return ResolvedSource(CareerSourceType.UNSUPPORTED, None, "unparseable_url")
    hostname = (parsed.hostname or "").lower()

    if not hostname:
        return ResolvedSource(CareerSourceType.UNSUPPORTED, None, "unparseable_url")

    if _hostname_matches(hostname, "linkedin.com"):
        return ResolvedSource(CareerSourceType.UNSUPPORTED, None, "linkedin_not_scraped")

    for domain, source_type in _HOSTNAME_PATTERNS:
        if _hostname_matches(hostname, domain):
            if source_type == CareerSourceType.WORKDAY:
                return _workday_board(url) or ResolvedSource(source_type, hostname.split(".")[0])
            identifier = _extract_identifier(source_type, hostname, parsed)
            return ResolvedSource(source_type, identifier)

    return _probe_page(url)


def _extract_identifier(
    source_type: CareerSourceType, hostname: str, parsed: ParseResult
) -> str | None:
    segments = [segment for segment in parsed.path.split("/") if segment]

    if source_type == CareerSourceType.COMEET:
        # /jobs/{human-readable-slug}/{company_uid} - e.g. /jobs/cymotive/F1.008.
        # The company_uid (segments[2]) is what the Comeet API actually
        # needs; the slug in segments[1] is cosmetic. Verified live against
        # real companies during Phase 4 - see app/ingestion/adapters/comeet.py.
        # Note the ComeetAdapter itself does not trust this value for the
        # required API token (which isn't in the URL at all) and re-derives
        # both from the page directly - this is stored for display only.
        if len(segments) >= 3 and segments[0] == "jobs":
            return segments[2]
        return segments[-1] if segments else None

    if source_type == CareerSourceType.GREENHOUSE:
        # Embedded boards carry the token in a query param, not the path:
        # boards.greenhouse.io/embed/job_board?for=acme
        if segments and segments[0] == "embed":
            for_values = parse_qs(parsed.query).get("for")
            return for_values[0] if for_values else None
        return segments[0] if segments else None

    if source_type in (CareerSourceType.LEVER, CareerSourceType.ASHBY):
        return segments[0] if segments else None

    if source_type == CareerSourceType.TALEO:
        return hostname.split(".")[0] if hostname else None

    return None


def _workday_board(url: str) -> ResolvedSource | None:
    """Workday career sites are {tenant}.{wdN}.myworkdayjobs.com/{site},
    optionally with a locale segment first (/he-IL/MedtronicCareers) and
    deep links after (/External/page/..., /Unity/job/...). The adapter
    needs tenant + site, so the identifier is "tenant/site" and the board
    URL is the canonical site root. Verified against the real Intel, Flex,
    Medtronic, Unity, Samsung, Mastercard, Ribbon and Leidos links.
    """
    match = _WORKDAY_RE.search(url)
    if match is None:
        return None
    tenant, wd, path = match.group(1).lower(), match.group(2).lower(), match.group(3) or ""
    segments = [segment for segment in path.split("/") if segment]
    site = next(
        (
            segment
            for segment in segments
            if not _LOCALE_SEGMENT_RE.match(segment) and segment.lower() not in _NOT_A_WORKDAY_SITE
        ),
        None,
    )
    if site is None:
        return None
    return ResolvedSource(
        CareerSourceType.WORKDAY,
        f"{tenant}/{site}",
        board_url=f"https://{tenant}.{wd}.myworkdayjobs.com/{site}",
    )


def _detect_embedded_ats(body: str) -> ResolvedSource | None:
    # Boards referenced from inline JSON come with escaped slashes
    # ("https:\/\/job-boards.greenhouse.io\/similarweb\/jobs\/1").
    text = body.replace("\\/", "/")

    link = _COMEET_BOARD_LINK_RE.search(text)
    if link:
        slug, uid = link.group(1), link.group(2)
        return ResolvedSource(
            CareerSourceType.COMEET, uid, board_url=f"https://www.comeet.com/jobs/{slug}/{uid}"
        )
    init = _COMEET_INIT_UID_RE.search(text)
    if init:
        # COMEET.init({"token": ..., "company-uid": ...}) on the company's
        # own page - the adapter reads both from that page (no board_url).
        return ResolvedSource(CareerSourceType.COMEET, init.group(1))

    embed = _GREENHOUSE_EMBED_RE.search(text)
    token = embed.group(1) if embed else _first_slug(_GREENHOUSE_BOARD_RE, text)
    if token:
        return ResolvedSource(
            CareerSourceType.GREENHOUSE, token, board_url=f"https://job-boards.greenhouse.io/{token}"
        )

    client = _first_slug(_LEVER_RE, text)
    if client:
        return ResolvedSource(
            CareerSourceType.LEVER, client, board_url=f"https://jobs.lever.co/{client}"
        )

    client = _first_slug(_ASHBY_RE, text)
    if client:
        return ResolvedSource(
            CareerSourceType.ASHBY, client, board_url=f"https://jobs.ashbyhq.com/{client}"
        )

    subdomain = _first_slug(_WORKABLE_RE, text)
    if subdomain:
        return ResolvedSource(
            CareerSourceType.WORKABLE,
            subdomain,
            board_url=f"https://apply.workable.com/{subdomain}/",
        )

    for workday in _WORKDAY_RE.finditer(text):
        resolved = _workday_board(workday.group(0))
        if resolved is not None:
            return resolved

    company = _first_slug(_SMARTRECRUITERS_RE, text)
    if company:
        return ResolvedSource(
            CareerSourceType.SMARTRECRUITERS,
            company,
            board_url=f"https://jobs.smartrecruiters.com/{company}",
        )
    return None


def _first_slug(pattern: re.Pattern[str], text: str) -> str | None:
    for match in pattern.finditer(text):
        slug = match.group(1)
        if slug.lower() not in _NOT_A_BOARD_SLUG:
            return slug
    return None


def _domain_label(url: str) -> str:
    """"buildots" for buildots.com, join.jfrog.com -> "jfrog", x.co.il -> "x"."""
    labels = (urlparse(url).hostname or "").lower().split(".")
    if len(labels) >= 3 and labels[-2] in ("co", "com", "org", "net", "ac", "gov"):
        return labels[-3]
    return labels[-2] if len(labels) >= 2 else labels[0]


def _resolve_comeet_plugin(page_url: str, body: str) -> ResolvedSource | None:
    page_host = urlparse(page_url).hostname
    position_links = [
        link
        for link in (urljoin(page_url, m.group(1)) for m in _COMEET_POSITION_LINK_RE.finditer(body))
        if urlparse(link).hostname == page_host
    ]
    # Verified on real pages: Buildots and Kaltura mention Comeet nowhere
    # in the listing HTML - the distinctive "/E9.769/" position-uid path
    # segments are the only tell - while hibob has the plugin's markup but
    # links its positions without uids (nothing to follow).
    plugin_present = bool(_COMEET_PLUGIN_MARKER_RE.search(body)) or "comeet" in body.lower()
    if not position_links or not (plugin_present or len(position_links) >= 3):
        return None

    uid: str | None = None
    for link in position_links[:_MAX_POSITION_PROBES]:
        try:
            uid_match = _COMEET_ANY_UID_RE.search(_fetch_capped(link))
        except (httpx.HTTPError, httpx.InvalidURL):
            continue
        if uid_match:
            uid = uid_match.group(1)
            break
    if uid is None:
        return None

    board_url = f"https://www.comeet.com/jobs/{_domain_label(page_url)}/{uid}"
    try:
        board_html = _fetch_capped(board_url)
    except (httpx.HTTPError, httpx.InvalidURL):
        return None
    # A wrong slug redirects to comeet.com's homepage (no COMPANY_DATA).
    if "COMPANY_DATA" in board_html and uid in board_html:
        return ResolvedSource(CareerSourceType.COMEET, uid, board_url=board_url)
    return None


def _verify_board_by_domain_label(page_url: str, body: str) -> ResolvedSource | None:
    slug = _domain_label(page_url)
    for source_type, hint, api_url, board_url, list_key in _BOARD_BY_DOMAIN_LABEL:
        if not hint.search(body):
            continue
        try:
            with httpx.Client(follow_redirects=True, timeout=_PROBE_TIMEOUT_SECONDS) as client:
                response = client.get(
                    api_url.format(slug=slug), headers={"User-Agent": _PROBE_USER_AGENT}
                )
            if response.status_code != 200:
                continue
            payload = response.json()
        except (httpx.HTTPError, httpx.InvalidURL, ValueError):
            continue
        listing = payload.get(list_key) if list_key else payload
        if not isinstance(listing, list):
            continue
        logger.info(
            "career source board verified by domain label",
            extra={"url": page_url, "source_type": source_type.value, "slug": slug},
        )
        return ResolvedSource(source_type, slug, board_url=board_url.format(slug=slug))
    return None


def _probe_page(url: str) -> ResolvedSource:
    """Bounded, best-effort HTTP GET: embedded ATS first, then JSON-LD
    JobPosting markup, else generic_html. Any failure (timeout, DNS,
    non-2xx, ...) degrades to generic_html rather than raising - one
    unreachable company must never abort a whole sheet sync (spec §34).
    """
    try:
        body = _fetch_capped(url)
    except (httpx.HTTPError, httpx.InvalidURL) as exc:
        # InvalidURL is not an HTTPError subclass - a cell with a stray
        # newline or bad port would otherwise abort the whole sheet sync.
        logger.info(
            "career source probe failed, defaulting to generic_html",
            extra={"url": url, "error_type": type(exc).__name__},
        )
        return ResolvedSource(CareerSourceType.GENERIC_HTML, None)

    embedded = (
        _detect_embedded_ats(body)
        or _resolve_comeet_plugin(url, body)
        or _verify_board_by_domain_label(url, body)
    )
    if embedded is not None:
        logger.info(
            "career source embeds a known ATS",
            extra={"url": url, "source_type": embedded.source_type.value},
        )
        return embedded

    for match in _JSON_LD_BLOCK_RE.finditer(body):
        if "JobPosting" in match.group(1):
            return ResolvedSource(CareerSourceType.JSONLD, None)

    return ResolvedSource(CareerSourceType.GENERIC_HTML, None)


def _fetch_capped(url: str) -> str:
    with (
        httpx.Client(follow_redirects=True, timeout=_PROBE_TIMEOUT_SECONDS) as client,
        client.stream("GET", url, headers={"User-Agent": _PROBE_USER_AGENT}) as response,
    ):
        response.raise_for_status()
        chunks: list[bytes] = []
        total = 0
        for chunk in response.iter_bytes():
            chunks.append(chunk)
            total += len(chunk)
            if total >= _MAX_PROBE_BYTES:
                break
        return b"".join(chunks).decode(response.encoding or "utf-8", errors="ignore")

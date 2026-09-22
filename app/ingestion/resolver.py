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
3. A WordPress job post type listed by the site's REST API (`wordpress`).
4. Otherwise `generic_html`.

A few company sites publish their own JSON feed instead; those are known
by hostname (`site_feed`, adapters/site_feed.py).
"""

from __future__ import annotations

import logging
import re
from collections.abc import Iterator
from dataclasses import dataclass
from typing import Any
from urllib.parse import ParseResult, parse_qs, urljoin, urlparse

import httpx

from app.ingestion.adapters._http import BlockedUrlError, ensure_public_url
from app.ingestion.adapters.generic_html import extract_job_links
from app.ingestion.adapters.lever import postings_api_base
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
# The board's own pages, or its API called from the company page's JS
# (VIA fetches boards-api.greenhouse.io/v1/boards/via/jobs itself).
_GREENHOUSE_BOARD_RE = re.compile(
    rf"(?:job-boards\.greenhouse\.io|boards\.greenhouse\.io|boards-api\.greenhouse\.io/v1/boards)"
    rf"/({_SLUG})(?=[/\"'?\s])"
)
# Lever's EU region has its own hosts (Mobileye links jobs.eu.lever.co);
# the host is kept so the adapter asks the matching API.
_LEVER_RE = re.compile(rf"(jobs(?:\.eu)?\.lever\.co)/({_SLUG})(?=[/\"'?\s]|$)")
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
# A page built on the Comeet JS API may keep the company uid in one of
# its own scripts instead (Plus500's general.js loads the API and calls
# the positions endpoint itself), so those are read too.
_COMEET_API_PATH_RE = re.compile(rf"careers-api/2\.0/company/({_COMEET_UID})")
_SCRIPT_SRC_RE = re.compile(r"<script[^>]+src=[\"']([^\"']+)[\"']", re.I)
_MAX_SCRIPT_PROBES = 10
# Boards that a company page loads purely from JS leave no board URL in
# the HTML (JFrog, AppsFlyer) - only a hint that the ATS is in play. The
# board token is then the company's own domain label, kept only if the
# ATS's public API answers for it (_board_answers).
_BOARD_HINTS: tuple[tuple[CareerSourceType, re.Pattern[str], str], ...] = (
    (
        CareerSourceType.GREENHOUSE,
        re.compile(r"greenhouse|[?&]gh_(?:department|jid|src)=", re.I),
        "https://job-boards.greenhouse.io/{slug}",
    ),
    (
        CareerSourceType.LEVER,
        re.compile(r"lever\.co|\blever\b", re.I),
        "https://jobs.lever.co/{slug}",
    ),
    (CareerSourceType.ASHBY, re.compile(r"ashby", re.I), "https://jobs.ashbyhq.com/{slug}"),
)
_SMARTRECRUITERS_RE = re.compile(
    rf"(?:(?:jobs|careers)\.smartrecruiters\.com|api\.smartrecruiters\.com/v1/companies)/({_SLUG})"
    r"(?=[/\"'?\s]|$)"
)
# Company sites that publish their own JSON feed (adapters/site_feed.py).
_SITE_FEED_HOSTS: tuple[tuple[str, str], ...] = (
    ("elbitsystemscareer.com", "elbit"),
    ("jobs.iai.co.il", "iai"),
    ("amazon.jobs", "amazon"),
)
# A WordPress site may keep its jobs as posts of a custom type the REST
# API lists (adapters/wordpress.py); its type index names the type.
_WORDPRESS_MARKER_RE = re.compile(r"/wp-content/|/wp-json/", re.I)
_JOB_POST_TYPE_RE = re.compile(r"job|position|career|vacan|opening|drushim|misra", re.I)
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

    for domain, feed in _SITE_FEED_HOSTS:
        if _hostname_matches(hostname, domain):
            return ResolvedSource(CareerSourceType.SITE_FEED, feed)

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
    return next((board for board in _embedded_boards(text) if _board_answers(board)), None)


def _embedded_boards(text: str) -> Iterator[ResolvedSource]:
    """Every board the page references, most specific first. A page can
    carry more than one: Nexxen's still loads the Greenhouse embed it
    migrated away from next to the Ashby board it uses now, so the caller
    keeps the first one whose API actually answers."""
    if link := _COMEET_BOARD_LINK_RE.search(text):
        slug, uid = link.group(1), link.group(2)
        yield ResolvedSource(
            CareerSourceType.COMEET, uid, board_url=f"https://www.comeet.com/jobs/{slug}/{uid}"
        )
    if init := _COMEET_INIT_UID_RE.search(text):
        # COMEET.init({"token": ..., "company-uid": ...}) on the company's
        # own page - the adapter reads both from that page (no board_url).
        yield ResolvedSource(CareerSourceType.COMEET, init.group(1))

    embed = _GREENHOUSE_EMBED_RE.search(text)
    board = embed or _first_board(_GREENHOUSE_BOARD_RE, text)
    if board:
        token = board.group(1)
        yield ResolvedSource(
            CareerSourceType.GREENHOUSE,
            token,
            board_url=f"https://job-boards.greenhouse.io/{token}",
        )
    if lever := _first_board(_LEVER_RE, text):
        host, client = lever.group(1), lever.group(2)
        yield ResolvedSource(CareerSourceType.LEVER, client, board_url=f"https://{host}/{client}")
    if ashby := _first_board(_ASHBY_RE, text):
        client = ashby.group(1)
        yield ResolvedSource(
            CareerSourceType.ASHBY, client, board_url=f"https://jobs.ashbyhq.com/{client}"
        )
    if workable := _first_board(_WORKABLE_RE, text):
        subdomain = workable.group(1)
        yield ResolvedSource(
            CareerSourceType.WORKABLE,
            subdomain,
            board_url=f"https://apply.workable.com/{subdomain}/",
        )
    for workday in _WORKDAY_RE.finditer(text):
        resolved = _workday_board(workday.group(0))
        if resolved is not None:
            yield resolved
            break
    if smart := _first_board(_SMARTRECRUITERS_RE, text):
        company = smart.group(1)
        yield ResolvedSource(
            CareerSourceType.SMARTRECRUITERS,
            company,
            board_url=f"https://jobs.smartrecruiters.com/{company}",
        )


def _first_board(pattern: re.Pattern[str], text: str) -> re.Match[str] | None:
    """First match whose last group - the board slug - is a real token."""
    for match in pattern.finditer(text):
        if match.group(pattern.groups).lower() not in _NOT_A_BOARD_SLUG:
            return match
    return None


def _board_api_url(board: ResolvedSource) -> str | None:
    """The public list endpoint the adapter for this board will call;
    None for ATSes without one (Comeet needs a token, Workday a POST)."""
    slug = board.external_identifier
    match board.source_type:
        case CareerSourceType.GREENHOUSE:
            return f"https://boards-api.greenhouse.io/v1/boards/{slug}/jobs"
        case CareerSourceType.LEVER:
            host = urlparse(board.board_url or "").hostname or "jobs.lever.co"
            return f"{postings_api_base(host)}/{slug}?mode=json"
        case CareerSourceType.ASHBY:
            return f"https://api.ashbyhq.com/posting-api/job-board/{slug}"
        case CareerSourceType.WORKABLE:
            return f"https://apply.workable.com/api/v1/widget/accounts/{slug}"
    return None


def _board_answers(board: ResolvedSource) -> bool:
    """Keep a board only if its ATS answers for it, so a stale embed or a
    guessed token is never stored to fail on every crawl. Boards without a
    cheap public check are trusted as found."""
    api_url = _board_api_url(board)
    if api_url is None:
        return True
    payload = _get_json(api_url)
    listing = payload.get("jobs") if isinstance(payload, dict) else payload
    return isinstance(listing, list)


def _get(url: str) -> httpx.Response | None:
    """The 200 response at url, or None on any failure."""
    try:
        ensure_public_url(url)
        with httpx.Client(follow_redirects=True, timeout=_PROBE_TIMEOUT_SECONDS) as client:
            response = client.get(url, headers={"User-Agent": _PROBE_USER_AGENT})
    except (httpx.HTTPError, httpx.InvalidURL, BlockedUrlError):
        return None
    return response if response.status_code == 200 else None


def _get_json(url: str) -> Any:
    """The JSON at url, or None on any failure (non-200, network, not JSON)."""
    response = _get(url)
    try:
        return response.json() if response is not None else None
    except ValueError:
        return None


def _domain_label(url: str) -> str:
    """ "buildots" for buildots.com, join.jfrog.com -> "jfrog", x.co.il -> "x"."""
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

    for link in position_links[:_MAX_POSITION_PROBES]:
        try:
            uid_match = _COMEET_ANY_UID_RE.search(_fetch_capped(link))
        except (httpx.HTTPError, httpx.InvalidURL, BlockedUrlError):
            continue
        if uid_match:
            return _verified_comeet_board(page_url, uid_match.group(1))
    return None


def _resolve_comeet_from_scripts(page_url: str, body: str) -> ResolvedSource | None:
    """Plus500: the page only has Comeet's container markup; its own
    general.js loads the JS API and calls the positions endpoint with the
    company uid. The page's own scripts are read for it."""
    if "comeet" not in body.lower():
        return None
    page_host = urlparse(page_url).hostname
    scripts = [
        script
        for script in (urljoin(page_url, m.group(1)) for m in _SCRIPT_SRC_RE.finditer(body))
        if urlparse(script).hostname == page_host
    ]
    for script in scripts[:_MAX_SCRIPT_PROBES]:
        try:
            js = _fetch_capped(script)
        except (httpx.HTTPError, httpx.InvalidURL, BlockedUrlError):
            continue
        uid_match = _COMEET_ANY_UID_RE.search(js) or _COMEET_API_PATH_RE.search(js)
        if uid_match:
            return _verified_comeet_board(page_url, uid_match.group(1))
    return None


def _verified_comeet_board(page_url: str, uid: str) -> ResolvedSource | None:
    """The public board for a company uid is comeet.com/jobs/{slug}/{uid}
    with the company's domain label as slug. A wrong slug redirects to
    Comeet's homepage, so the guess is kept only when the board page
    serves COMPANY_DATA for that uid."""
    board_url = f"https://www.comeet.com/jobs/{_domain_label(page_url)}/{uid}"
    try:
        board_html = _fetch_capped(board_url)
    except (httpx.HTTPError, httpx.InvalidURL, BlockedUrlError):
        return None
    if "COMPANY_DATA" in board_html and uid in board_html:
        return ResolvedSource(CareerSourceType.COMEET, uid, board_url=board_url)
    return None


def _verify_board_by_domain_label(page_url: str, body: str) -> ResolvedSource | None:
    slug = _domain_label(page_url)
    for source_type, hint, board_url in _BOARD_HINTS:
        if not hint.search(body):
            continue
        guess = ResolvedSource(source_type, slug, board_url=board_url.format(slug=slug))
        if _board_answers(guess):
            logger.info(
                "career source board verified by domain label",
                extra={"url": page_url, "source_type": source_type.value, "slug": slug},
            )
            return guess
    return None


def _probe_page(url: str) -> ResolvedSource:
    """Bounded, best-effort HTTP GET: embedded ATS first, then JSON-LD
    JobPosting markup, else generic_html. Any failure (timeout, DNS,
    non-2xx, ...) degrades to generic_html rather than raising - one
    unreachable company must never abort a whole sheet sync (spec §34).
    """
    try:
        body = _fetch_capped(url)
    except (httpx.HTTPError, httpx.InvalidURL, BlockedUrlError) as exc:
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
        or _resolve_comeet_from_scripts(url, body)
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

    return _resolve_wordpress(url, body) or ResolvedSource(CareerSourceType.GENERIC_HTML, None)


def _resolve_wordpress(page_url: str, body: str) -> ResolvedSource | None:
    """A WordPress site whose job list is drawn by JS may still keep its
    jobs as posts of a custom type (Comblack, One, OMC, Tap): the REST
    type index names it. The type is taken only when it lists more jobs
    than the page itself shows the plain reader - Logica-it's page lists
    251 jobs from a plugin while its `job_listing` type holds 68 others."""
    if not _WORDPRESS_MARKER_RE.search(body):
        return None
    parsed = urlparse(page_url)
    origin = f"{parsed.scheme}://{parsed.netloc}"
    types = _get_json(f"{origin}/wp-json/wp/v2/types")
    if not isinstance(types, dict):
        return None
    listed_on_page = len(extract_job_links(body, page_url))
    for slug, info in types.items():
        rest_base = str(info.get("rest_base") or slug) if isinstance(info, dict) else slug
        if not _JOB_POST_TYPE_RE.search(f"{slug} {rest_base}"):
            continue
        response = _get(f"{origin}/wp-json/wp/v2/{rest_base}?per_page=1")
        if response is None:
            continue
        total = int(response.headers.get("x-wp-total") or 0)
        if total > listed_on_page:
            return ResolvedSource(CareerSourceType.WORDPRESS, rest_base)
    return None


def _fetch_capped(url: str) -> str:
    ensure_public_url(url)
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

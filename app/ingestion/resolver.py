"""CareerSourceResolver: classifies a company's raw sheet URL into a
CareerSourceType, so the ingestion layer knows which adapter should
eventually handle it (Phase 4+). Never scrapes LinkedIn - see spec §14.

Resolution is hostname-based first (fast, no network call) for every ATS
this project knows about. Only URLs that don't match a known hostname
pattern trigger a single bounded HTTP probe to check for schema.org
JSON-LD JobPosting markup before falling back to generic_html.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from urllib.parse import ParseResult, parse_qs, urlparse

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

_MAX_PROBE_BYTES = 200_000
_PROBE_TIMEOUT_SECONDS = 8.0
_PROBE_USER_AGENT = "job-intel-bot/0.1 (career source resolver probe)"


@dataclass(frozen=True)
class ResolvedSource:
    source_type: CareerSourceType
    external_identifier: str | None
    unsupported_reason: str | None = None


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

    if source_type in (CareerSourceType.WORKDAY, CareerSourceType.TALEO):
        return hostname.split(".")[0] if hostname else None

    return None


def _probe_page(url: str) -> ResolvedSource:
    """Bounded, best-effort HTTP GET to check for JSON-LD JobPosting
    markup. Any failure (timeout, DNS, non-2xx, ...) degrades to
    generic_html rather than raising - one unreachable company must never
    abort a whole sheet sync (spec §34).
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

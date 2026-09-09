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
from urllib.parse import urlparse

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


def resolve_career_source(url: str | None) -> ResolvedSource:
    if not url or not url.strip():
        return ResolvedSource(CareerSourceType.UNSUPPORTED, None, "missing_url")

    url = url.strip()
    parsed = urlparse(url if "://" in url else f"https://{url}")
    hostname = (parsed.hostname or "").lower()

    if not hostname:
        return ResolvedSource(CareerSourceType.UNSUPPORTED, None, "unparseable_url")

    if "linkedin.com" in hostname:
        return ResolvedSource(CareerSourceType.UNSUPPORTED, None, "linkedin_not_scraped")

    for pattern, source_type in _HOSTNAME_PATTERNS:
        if pattern in hostname:
            identifier = _extract_identifier(source_type, hostname, parsed.path)
            return ResolvedSource(source_type, identifier)

    return _probe_page(url)


def _extract_identifier(source_type: CareerSourceType, hostname: str, path: str) -> str | None:
    segments = [segment for segment in path.split("/") if segment]

    if source_type == CareerSourceType.COMEET:
        if len(segments) >= 2 and segments[0] == "jobs":
            return segments[1]
        return segments[0] if segments else None

    if source_type in (CareerSourceType.GREENHOUSE, CareerSourceType.LEVER, CareerSourceType.ASHBY):
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
    except httpx.HTTPError as exc:
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

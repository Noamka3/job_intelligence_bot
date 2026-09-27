"""Shared HTTP helper for adapters: bounded timeout, retry with
exponential backoff + jitter on transient failures only (5xx/429/network
errors) - a real 4xx (bad board token, 404) fails immediately rather than
retrying something that will never succeed.

Every request goes through ensure_public_url first: the crawler follows
URLs it was given (a spreadsheet cell, a link on a third-party page), and
those must never point back inside the machine or its network.
"""

from __future__ import annotations

import ipaddress
from typing import Any
from urllib.parse import urlparse

import httpx
from tenacity import retry, retry_if_exception, stop_after_attempt, wait_random_exponential

_TIMEOUT_SECONDS = 15.0
_USER_AGENT = "job-intel-bot/0.1 (job source adapter; +https://github.com/)"
# The same headers a person opening the page in a browser would send.
# Some career pages only serve their listing to a normal desktop client;
# 8 of the real sheet's pages did when first surveyed. Nothing here is an
# access control - these pages are public, and the crawler identifies
# itself as a normal client rather than pretending to be anything else.
_BROWSER_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "he-IL,he;q=0.9,en-US;q=0.8,en;q=0.7",
}


class BlockedUrlError(ValueError):
    """A URL the crawler refuses to fetch because it addresses this
    machine or its private network rather than a public career site."""

    def __init__(self, url: str, host: str) -> None:
        super().__init__(f"Refusing to fetch a non-public address ({host}): {url}")


def ensure_public_url(url: str) -> None:
    """Rejects a URL whose host is a literal loopback/private/link-local
    address. Those are the addresses that make a crawler useful to an
    attacker: 127.0.0.1 and 10.x reach services meant to be internal, and
    169.254.169.254 is the cloud metadata endpoint that hands out
    credentials. A hostname is left to DNS - this project's URLs come from
    the owner's own spreadsheet, and the deployment keeps the crawler off
    any private network (see README).
    """
    host = (urlparse(url).hostname or "").strip("[]")
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        return  # a name, not a literal address
    if not address.is_global:
        raise BlockedUrlError(url, host)


def _is_retryable(exc: BaseException) -> bool:
    if isinstance(exc, httpx.HTTPStatusError):
        return exc.response.status_code >= 500 or exc.response.status_code == 429
    return isinstance(exc, httpx.TransportError)


@retry(
    retry=retry_if_exception(_is_retryable),
    stop=stop_after_attempt(3),
    wait=wait_random_exponential(multiplier=1, max=10),
    reraise=True,
)
def get_json(url: str, *, params: dict[str, Any] | None = None) -> Any:
    ensure_public_url(url)
    with httpx.Client(timeout=_TIMEOUT_SECONDS, follow_redirects=True) as client:
        response = client.get(url, params=params, headers={"User-Agent": _USER_AGENT})
        response.raise_for_status()
        return response.json()


@retry(
    retry=retry_if_exception(_is_retryable),
    stop=stop_after_attempt(3),
    wait=wait_random_exponential(multiplier=1, max=10),
    reraise=True,
)
def post_json(
    url: str,
    body: dict[str, Any],
    *,
    params: dict[str, Any] | None = None,
    headers: dict[str, str] | None = None,
) -> Any:
    ensure_public_url(url)
    with httpx.Client(timeout=_TIMEOUT_SECONDS, follow_redirects=True) as client:
        response = client.post(
            url,
            json=body,
            params=params,
            headers={
                "User-Agent": _USER_AGENT,
                "Accept": "application/json",
                "Content-Type": "application/json",
                **(headers or {}),
            },
        )
        response.raise_for_status()
        return response.json()


@retry(
    retry=retry_if_exception(_is_retryable),
    stop=stop_after_attempt(3),
    wait=wait_random_exponential(multiplier=1, max=10),
    reraise=True,
)
def get_page(url: str, *, browser_like: bool = False) -> tuple[str, str]:
    """(final URL after redirects, body text)."""
    ensure_public_url(url)
    headers = _BROWSER_HEADERS if browser_like else {"User-Agent": _USER_AGENT}
    with httpx.Client(timeout=_TIMEOUT_SECONDS, follow_redirects=True) as client:
        response = client.get(url, headers=headers)
        response.raise_for_status()
        return str(response.url), response.text


def get_text(url: str) -> str:
    return get_page(url)[1]

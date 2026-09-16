"""Shared HTTP helper for adapters: bounded timeout, retry with
exponential backoff + jitter on transient failures only (5xx/429/network
errors) - a real 4xx (bad board token, 404) fails immediately rather than
retrying something that will never succeed. See spec §18/§34.
"""

from __future__ import annotations

from typing import Any

import httpx
from tenacity import retry, retry_if_exception, stop_after_attempt, wait_random_exponential

_TIMEOUT_SECONDS = 15.0
_USER_AGENT = "job-intel-bot/0.1 (job source adapter; +https://github.com/)"
# Plain company career pages (the generic adapter) frequently sit behind
# a WAF that answers anything without a browser-looking User-Agent with
# 403 - 8 of the real sheet's pages did in the Phase 8 survey. A normal
# desktop UA is what the same person would send by opening the page.
_BROWSER_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "he-IL,he;q=0.9,en-US;q=0.8,en;q=0.7",
}


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
    headers = _BROWSER_HEADERS if browser_like else {"User-Agent": _USER_AGENT}
    with httpx.Client(timeout=_TIMEOUT_SECONDS, follow_redirects=True) as client:
        response = client.get(url, headers=headers)
        response.raise_for_status()
        return str(response.url), response.text


def get_text(url: str) -> str:
    return get_page(url)[1]

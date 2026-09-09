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
def get_text(url: str) -> str:
    with httpx.Client(timeout=_TIMEOUT_SECONDS, follow_redirects=True) as client:
        response = client.get(url, headers={"User-Agent": _USER_AGENT})
        response.raise_for_status()
        return response.text

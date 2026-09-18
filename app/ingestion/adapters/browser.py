"""Browser fallback (spec §17's last resort): a career page whose job
list or job pages are drawn by JavaScript is rendered in headless
Chromium and then read exactly like a plain career page. The crawler
hands a source here after the plain reader found nothing on it three
crawls in a row, or was refused with 403 (app/services/jobs/ingestion.py).

Cost is what makes it a fallback: Chromium takes seconds and a few
hundred MB per page. So renders are serialised per worker process, each
in its own short-lived browser with media blocked, and a job page is
fetched plainly first - many JS-listed sites still serve the posting
itself as HTML. Browsers exist only in the worker image (Dockerfile);
the Playwright import is deferred so the API process never needs them.
"""

from __future__ import annotations

import contextlib
import threading

import httpx

from app.ingestion.adapters._http import _BROWSER_HEADERS
from app.ingestion.adapters.base import JobDetails, JobStub, JobUnavailableError
from app.ingestion.adapters.generic_html import (
    GenericHtmlAdapter,
    details_from_html,
    extract_job_links,
    stubs_from_links,
)
from app.models.career_source import CareerSource

_PAGE_TIMEOUT_MS = 30_000
# How long to wait for the page's own requests to go quiet after it
# loaded; a page that never goes quiet (analytics) still has its content.
_SETTLE_TIMEOUT_MS = 8_000
_SCROLL_PX = 20_000
_AFTER_SCROLL_MS = 2_000
# A plainly fetched posting shorter than this is a JS shell, not the job.
_MIN_PLAIN_DESCRIPTION_CHARS = 200
_SKIPPED_RESOURCES = frozenset({"image", "media", "font"})
_RENDER_LOCK = threading.Lock()


class BrowserAdapter:
    def __init__(self) -> None:
        self._plain = GenericHtmlAdapter()

    def list_jobs(self, source: CareerSource) -> list[JobStub]:
        final_url, html = render(source.source_url)
        return stubs_from_links(extract_job_links(html, final_url))

    def fetch_job(self, source: CareerSource, stub: JobStub) -> JobDetails:
        try:
            details = self._plain.fetch_job(source, stub)
        except JobUnavailableError:
            raise
        except httpx.HTTPError:
            details = None
        if details is not None and len(details.description or "") >= _MIN_PLAIN_DESCRIPTION_CHARS:
            return details
        final_url, html = render(stub.source_url)
        return details_from_html(html, final_url, stub)


def render(url: str) -> tuple[str, str]:
    """(final URL, the page's HTML after its own scripts ran)."""
    from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
    from playwright.sync_api import sync_playwright

    with _RENDER_LOCK, sync_playwright() as playwright:
        browser = playwright.chromium.launch(args=["--disable-gpu", "--no-sandbox"])
        try:
            page = browser.new_page(user_agent=_BROWSER_HEADERS["User-Agent"], locale="he-IL")
            page.route(
                "**/*",
                lambda route: (
                    route.abort()
                    if route.request.resource_type in _SKIPPED_RESOURCES
                    else route.continue_()
                ),
            )
            page.goto(url, wait_until="domcontentloaded", timeout=_PAGE_TIMEOUT_MS)
            with contextlib.suppress(PlaywrightTimeoutError):
                page.wait_for_load_state("networkidle", timeout=_SETTLE_TIMEOUT_MS)
            # Lists that render on scroll (Team8's Comeet widget) need a nudge.
            page.mouse.wheel(0, _SCROLL_PX)
            page.wait_for_timeout(_AFTER_SCROLL_MS)
            return page.url, page.content()
        finally:
            browser.close()

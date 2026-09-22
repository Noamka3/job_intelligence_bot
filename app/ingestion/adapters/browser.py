"""Browser fallback (spec §17's last resort): a career page whose job
list or job pages are drawn by JavaScript is rendered in headless
Chromium and then read exactly like a plain career page. The crawler
hands a source here after the plain reader found nothing on it three
crawls in a row, or was refused with 403 (app/services/jobs/ingestion.py).

Rendering also shows what the page *does*, not only what it draws, and
that is read too. Surveyed across the 65 real sources that reach this
adapter:

- the JSON its own scripts fetch becomes the job list when the rendered
  HTML has no links to follow (Ness paints 200 cards, no anchors, from
  /careers/api/Careers/GetAllItems - see sniffed_feed.py);
- an ATS API it calls means the page is a front for a board that already
  has a proper adapter (biocatch's page calls Comeet's positions
  endpoint), so the crawler is told to re-point the source at the board
  rather than scrape a rendering (BoardBehindPage);
- a consent dialog can sit on top of everything until it is clicked.

Cost is what makes it a fallback: Chromium takes seconds and a few
hundred MB per page. So renders are serialised per worker process, each
in its own short-lived browser with media blocked, and a job page is
fetched plainly first - many JS-listed sites still serve the posting
itself as HTML. Browsers exist only in the worker image (Dockerfile);
the Playwright import is deferred so the API process never needs them.

Security: this renders hostile third-party pages all day. Chromium's own
sandbox stays on, the container runs as a non-root user, and captured
JSON is *parsed, never executed*, only from the page's own site, and only
up to a size cap - so a page can hand the crawler a document to read and
nothing more.
"""

from __future__ import annotations

import contextlib
import json
import threading
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlparse

import httpx

from app.ingestion.adapters import sniffed_feed
from app.ingestion.adapters._http import _BROWSER_HEADERS, ensure_public_url
from app.ingestion.adapters.base import BoardBehindPage, JobDetails, JobStub, JobUnavailableError
from app.ingestion.adapters.generic_html import (
    GenericHtmlAdapter,
    details_from_html,
    extract_job_links,
    registrable_host,
    stubs_from_links,
)
from app.ingestion.resolver import board_behind_page
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
# A JSON response is kept only if it came from the page's own site and is
# small enough to be a job list rather than a data dump.
_MAX_FEED_BYTES = 2_000_000
_MAX_FEEDS = 20
# Consent dialogs from the two vendors seen on the real sheet's pages
# (OneTrust on EY, Cookiebot on BDO) sit on top of everything until
# clicked; the accept button is the one thing a person would click.
_CONSENT_BUTTONS = (
    "#onetrust-accept-btn-handler",
    "#CybotCookiebotDialogButtonLevelOptinAllowAll",
)
_CONSENT_CLICK_TIMEOUT_MS = 1_500
_RENDER_LOCK = threading.Lock()


@dataclass
class Rendered:
    """What one page visit produced."""

    url: str
    html: str
    # JSON documents the page fetched from its own site, parsed.
    feeds: list[Any] = field(default_factory=list)
    # Every URL the page requested - read only to spot a known ATS.
    requested: list[str] = field(default_factory=list)


class BrowserAdapter:
    def __init__(self) -> None:
        self._plain = GenericHtmlAdapter()

    def list_jobs(self, source: CareerSource) -> list[JobStub]:
        rendered = render(source.source_url)

        links = extract_job_links(rendered.html, rendered.url)
        if links:
            return stubs_from_links(links)

        # No links to follow. Before scraping a rendering, check whether
        # the page is simply a front for a board we can read properly.
        board = board_behind_page(rendered.requested, rendered.url)
        if board is not None:
            raise BoardBehindPage(board)

        return sniffed_feed.stubs_from_feeds(rendered.feeds, rendered.url)

    def fetch_job(self, source: CareerSource, stub: JobStub) -> JobDetails:
        # A stub built from a feed already carries the posting's own
        # fields; there is no separate page worth fetching.
        if sniffed_feed.is_feed_stub(stub):
            return sniffed_feed.details_from_stub(stub)

        try:
            details = self._plain.fetch_job(source, stub)
        except JobUnavailableError:
            raise
        except httpx.HTTPError:
            details = None
        if details is not None and len(details.description or "") >= _MIN_PLAIN_DESCRIPTION_CHARS:
            return details

        rendered = render(stub.source_url)
        return details_from_html(rendered.html, rendered.url, stub)


def render(url: str) -> Rendered:
    """Visit the page and report what it drew and what it fetched."""
    from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
    from playwright.sync_api import sync_playwright

    ensure_public_url(url)
    site = registrable_host(urlparse(url).hostname or "")
    feeds: list[Any] = []
    requested: list[str] = []

    with _RENDER_LOCK, sync_playwright() as playwright:
        # Chromium's own sandbox stays on: this renders untrusted pages,
        # and it is the layer between a browser exploit and the worker.
        # It works because the image runs as a non-root user (Dockerfile).
        browser = playwright.chromium.launch(args=["--disable-gpu"])
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
            page.on("request", lambda request: requested.append(request.url))
            page.on("response", lambda response: _capture_feed(response, site, feeds))

            page.goto(url, wait_until="domcontentloaded", timeout=_PAGE_TIMEOUT_MS)
            _accept_consent(page)
            with contextlib.suppress(PlaywrightTimeoutError):
                page.wait_for_load_state("networkidle", timeout=_SETTLE_TIMEOUT_MS)
            # Lists that render on scroll (Team8's Comeet widget) need a nudge.
            page.mouse.wheel(0, _SCROLL_PX)
            page.wait_for_timeout(_AFTER_SCROLL_MS)
            return Rendered(url=page.url, html=page.content(), feeds=feeds, requested=requested)
        finally:
            browser.close()


def _accept_consent(page: Any) -> None:
    """Click a known consent dialog away, if one is in the way."""
    from playwright.sync_api import Error as PlaywrightError
    from playwright.sync_api import TimeoutError as PlaywrightTimeoutError

    for selector in _CONSENT_BUTTONS:
        with contextlib.suppress(PlaywrightTimeoutError, PlaywrightError):
            button = page.locator(selector)
            if button.count():
                button.first.click(timeout=_CONSENT_CLICK_TIMEOUT_MS)
                return


def _capture_feed(response: Any, site: str, feeds: list[Any]) -> None:
    """Keep a JSON document the page fetched from its own site.

    Same-site only, by design: a rendered page pulls JSON from a dozen
    third parties (Wix internals, cookie vendors, form widgets, ad
    pixels) and none of it is this company's job list. It is also the
    security boundary - the crawler reads data from the site it was
    pointed at, not from whoever that site embeds.
    """
    if len(feeds) >= _MAX_FEEDS or response.status != 200:
        return
    if "json" not in response.headers.get("content-type", "").lower():
        return
    if registrable_host(urlparse(response.url).hostname or "") != site:
        return
    with contextlib.suppress(Exception):  # a body that won't load or parse is simply not a feed
        body = response.text()
        if len(body) <= _MAX_FEED_BYTES:
            feeds.append(json.loads(body))

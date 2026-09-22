from __future__ import annotations

from collections.abc import Callable

import httpx
import pytest
import respx

from app.ingestion.adapters import browser
from app.ingestion.adapters.browser import BrowserAdapter, Rendered
from app.models.career_source import CareerSource
from app.models.enums import CareerSourceType

_SOURCE = CareerSource(
    source_type=CareerSourceType.PLAYWRIGHT, source_url="https://acme.example/careers"
)
_RENDERED_LISTING = (
    "<ul>"
    + "".join(
        f'<li class="job"><a href="/jobs/{n}">Backend Developer {n}</a></li>' for n in (1, 2, 3)
    )
    + "</ul>"
)
_POSTING_TEXT = "Build and run the services behind our product. " * 8
_RENDERED_POSTING = (
    "<html><body><h1>Backend Developer 1</h1>"
    f"<main><p>{_POSTING_TEXT}</p><a href='/apply'>Apply now</a></main></body></html>"
)
_JS_SHELL = '<html><body><div id="root"></div><script src="/app.js"></script></body></html>'


def _renders(html: str) -> Callable[[str], Rendered]:
    return lambda url: Rendered(url=url, html=html)


def test_list_jobs_reads_the_rendered_page_like_a_plain_one(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(browser, "render", _renders(_RENDERED_LISTING))

    stubs = BrowserAdapter().list_jobs(_SOURCE)

    assert [stub.title for stub in stubs] == [
        "Backend Developer 1",
        "Backend Developer 2",
        "Backend Developer 3",
    ]
    assert stubs[0].source_url == "https://acme.example/jobs/1"


@respx.mock
def test_fetch_job_renders_only_when_the_plain_page_is_a_shell(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Many JS-listed sites still serve the posting itself as HTML; the
    browser is spent only on pages that come back as an empty shell."""
    adapter = BrowserAdapter()
    monkeypatch.setattr(browser, "render", _renders(_RENDERED_LISTING))
    (stub, *_) = adapter.list_jobs(_SOURCE)
    respx.get(stub.source_url).mock(return_value=httpx.Response(200, text=_JS_SHELL))
    monkeypatch.setattr(browser, "render", _renders(_RENDERED_POSTING))

    details = adapter.fetch_job(_SOURCE, stub)

    assert details.title == "Backend Developer 1"
    assert details.description is not None
    assert "Build and run the services" in details.description

    # A posting that arrives complete is never rendered.
    respx.get(stub.source_url).mock(return_value=httpx.Response(200, text=_RENDERED_POSTING))
    monkeypatch.setattr(browser, "render", lambda url: pytest.fail("rendered a complete page"))

    plain = adapter.fetch_job(_SOURCE, stub)

    assert plain.title == "Backend Developer 1"

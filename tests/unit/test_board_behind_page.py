"""A rendered page that turns out to be a front for a real ATS board."""

from __future__ import annotations

import httpx
import respx

from app.ingestion.resolver import board_behind_page
from app.models.enums import CareerSourceType

_PAGE = "https://www.biocatch.example/cybersecurity-careers"


@respx.mock
def test_a_comeet_api_call_resolves_to_the_public_board() -> None:
    """Verbatim from the live run: the page mentions Comeet nowhere in
    its HTML and fetches its own cards from Comeet's positions endpoint.
    The call carries the company uid but not the token the adapter needs,
    so the public board for that uid is what gets crawled."""
    respx.get("https://www.comeet.com/jobs/biocatch/03.00E").mock(
        return_value=httpx.Response(
            200, text='var COMPANY_DATA = {"company_uid": "03.00E", "token": "abc"};'
        )
    )
    requested = [
        "https://www.biocatch.example/assets/app.js",
        "https://www.comeet.co/careers-api/2.0/company/03.00e/positions?token=30EF&details=false",
    ]

    board = board_behind_page(requested, _PAGE)

    assert board is not None
    assert board.source_type == CareerSourceType.COMEET
    assert board.external_identifier == "03.00E"
    assert board.board_url == "https://www.comeet.com/jobs/biocatch/03.00E"


@respx.mock
def test_a_greenhouse_api_call_resolves_to_its_board() -> None:
    respx.get("https://boards-api.greenhouse.io/v1/boards/acme/jobs").mock(
        return_value=httpx.Response(200, json={"jobs": [{"id": 1}]})
    )

    board = board_behind_page(
        ["https://boards-api.greenhouse.io/v1/boards/acme/jobs?content=true"], _PAGE
    )

    assert board is not None
    assert board.source_type == CareerSourceType.GREENHOUSE
    assert board.board_url == "https://job-boards.greenhouse.io/acme"


@respx.mock
def test_a_page_that_calls_no_ats_resolves_to_nothing() -> None:
    """The ordinary case: analytics, fonts, cookie vendors and the site's
    own assets say nothing about where its jobs live."""
    requested = [
        "https://www.google-analytics.com/g/collect?v=2",
        "https://cdn.cookielaw.org/consent/abc",
        "https://siteassets.parastorage.com/pages/pages/thunderbolt",
        "https://www.biocatch.example/assets/app.js",
    ]

    assert board_behind_page(requested, _PAGE) is None


@respx.mock
def test_a_comeet_board_that_does_not_verify_is_not_used() -> None:
    """A wrong slug redirects to Comeet's homepage; guessing is never
    enough on its own."""
    respx.get("https://www.comeet.com/jobs/biocatch/03.00E").mock(
        return_value=httpx.Response(200, text="<html>Comeet homepage</html>")
    )

    requested = ["https://www.comeet.co/careers-api/2.0/company/03.00e/positions?token=x"]

    assert board_behind_page(requested, _PAGE) is None

from __future__ import annotations

import httpx
import respx

from app.ingestion.resolver import ResolvedSource, resolve_career_source
from app.models.enums import CareerSourceType


def test_missing_url_is_unsupported() -> None:
    resolved = resolve_career_source(None)
    assert resolved.source_type == CareerSourceType.UNSUPPORTED
    assert resolved.unsupported_reason == "missing_url"

    resolved_blank = resolve_career_source("   ")
    assert resolved_blank.source_type == CareerSourceType.UNSUPPORTED
    assert resolved_blank.unsupported_reason == "missing_url"


def test_linkedin_is_never_scraped() -> None:
    for url in (
        "https://www.linkedin.com/in/some-recruiter/",
        "https://www.linkedin.com/company/some-co/jobs/",
    ):
        resolved = resolve_career_source(url)
        assert resolved.source_type == CareerSourceType.UNSUPPORTED
        assert resolved.unsupported_reason == "linkedin_not_scraped"


def test_known_ats_hostnames_classify_without_network_call() -> None:
    cases = {
        "https://www.comeet.com/jobs/acme/F1.008": (CareerSourceType.COMEET, "F1.008"),
        "https://job-boards.greenhouse.io/torq": (CareerSourceType.GREENHOUSE, "torq"),
        "https://jobs.lever.co/acme": (CareerSourceType.LEVER, "acme"),
        "https://jobs.ashbyhq.com/acme": (CareerSourceType.ASHBY, "acme"),
        "https://intel.wd1.myworkdayjobs.com/External": (
            CareerSourceType.WORKDAY,
            "intel/External",
        ),
        "https://radware.taleo.net/careersection/ex/moresearch.ftl": (
            CareerSourceType.TALEO,
            "radware",
        ),
    }
    for url, (expected_type, expected_identifier) in cases.items():
        resolved = resolve_career_source(url)
        assert resolved.source_type == expected_type, url
        assert resolved.external_identifier == expected_identifier, url
        assert resolved.unsupported_reason is None


def test_hostname_match_is_on_label_boundaries_not_substrings() -> None:
    """ "clever.co" contains "lever.co" - a substring match would classify
    it as a Lever board with identifier "jobs" and 404 on every crawl."""
    cases = {
        "https://clever.co/jobs": CareerSourceType.LEVER,
        "https://www.clever.co/careers": CareerSourceType.LEVER,
        "https://greenhouse.io.example.net/x": CareerSourceType.GREENHOUSE,
    }
    for url, wrongly_matched_type in cases.items():
        with respx.mock:
            respx.get(url).mock(return_value=httpx.Response(200, text="<html></html>"))
            resolved = resolve_career_source(url)
        assert resolved.source_type != wrongly_matched_type, url


def test_greenhouse_embedded_board_takes_token_from_query_param() -> None:
    resolved = resolve_career_source("https://boards.greenhouse.io/embed/job_board?for=acme")
    assert resolved.source_type == CareerSourceType.GREENHOUSE
    assert resolved.external_identifier == "acme"


def test_scheme_less_url_is_normalized_with_https() -> None:
    from app.ingestion.resolver import normalize_source_url

    assert normalize_source_url("www.comeet.com/jobs/acme/A1.234") == (
        "https://www.comeet.com/jobs/acme/A1.234"
    )
    assert normalize_source_url("  https://x.example/  ") == "https://x.example/"
    assert normalize_source_url("   ") is None
    assert normalize_source_url(None) is None

    resolved = resolve_career_source("www.comeet.com/jobs/acme/A1.234")
    assert resolved.source_type == CareerSourceType.COMEET


@respx.mock
def test_probe_with_an_invalid_url_falls_back_instead_of_raising() -> None:
    """httpx.InvalidURL is not an HTTPError - a sheet cell with a stray
    newline used to escape the probe and abort the entire sheet sync."""
    resolved = resolve_career_source("https://careers.example.com/jobs\nhttps://other.example/")
    assert resolved.source_type == CareerSourceType.GENERIC_HTML


def test_workday_hostname_resolves_to_tenant_site_and_canonical_board() -> None:
    """Real sheet URLs: Intel's deep link, Medtronic's locale prefix."""
    intel = resolve_career_source("https://intel.wd1.myworkdayjobs.com/External/page/2938de27fd")
    assert intel.source_type == CareerSourceType.WORKDAY
    assert intel.external_identifier == "intel/External"
    assert intel.board_url == "https://intel.wd1.myworkdayjobs.com/External"

    medtronic = resolve_career_source(
        "https://medtronic.wd1.myworkdayjobs.com/he-IL/MedtronicCareers?redirect=/en-US/x"
    )
    assert medtronic.external_identifier == "medtronic/MedtronicCareers"
    assert medtronic.board_url == "https://medtronic.wd1.myworkdayjobs.com/MedtronicCareers"


def _probe(html: str) -> ResolvedSource:
    respx.get("https://company.example.com/careers").mock(
        return_value=httpx.Response(200, text=html, headers={"content-type": "text/html"})
    )
    return resolve_career_source("https://company.example.com/careers")


@respx.mock
def test_probe_detects_comeet_js_api_embed() -> None:
    """Verbatim shape from eToro's / Checkmarx's real pages."""
    resolved = _probe(
        '<script> window.comeetInit = function() { COMEET.init({ "token": "1495245246", '
        '"company-uid": "41.009", "font-size": "16px", //optional\n }); }</script>'
    )
    assert resolved.source_type == CareerSourceType.COMEET
    assert resolved.external_identifier == "41.009"
    assert resolved.board_url is None


@respx.mock
def test_probe_detects_comeet_board_links_and_derives_the_board_url() -> None:
    """Atera/Cyera: the page lists positions as links into comeet.com."""
    resolved = _probe(
        '<a href="https://www.comeet.com/jobs/atera/63.00B/controller/3D.B6A">View</a>'
        '<a href="https://www.comeet.com/jobs/atera/63.00B/qa-engineer/FD.E6A">View</a>'
    )
    assert resolved.source_type == CareerSourceType.COMEET
    assert resolved.external_identifier == "63.00B"
    assert resolved.board_url == "https://www.comeet.com/jobs/atera/63.00B"


@respx.mock
def test_probe_detects_greenhouse_embed_including_json_escaped_links() -> None:
    embed = _probe(
        '<script src="https://boards.greenhouse.io/embed/job_board/js?for=nexxen"></script>'
    )
    assert embed.source_type == CareerSourceType.GREENHOUSE
    assert embed.external_identifier == "nexxen"
    assert embed.board_url == "https://job-boards.greenhouse.io/nexxen"

    # SimilarWeb inlines its job list as JSON with escaped slashes.
    inline = _probe(
        '{"jobs":[{"link":"https:\\/\\/job-boards.greenhouse.io\\/similarweb\\/jobs\\/8203750"}]}'
    )
    assert inline.external_identifier == "similarweb"


@respx.mock
def test_probe_detects_ashby_workable_and_embedded_workday() -> None:
    ashby = _probe(
        '<div id="ashby_embed"></div><script src="https://jobs.ashbyhq.com/nexxen/embed?version=2"></script>'
    )
    assert (ashby.source_type, ashby.external_identifier) == (CareerSourceType.ASHBY, "nexxen")

    workable = _probe("var url = 'https://apply.workable.com/api/v1/widget/accounts/anzu'")
    assert (workable.source_type, workable.external_identifier) == (
        CareerSourceType.WORKABLE,
        "anzu",
    )
    assert workable.board_url == "https://apply.workable.com/anzu/"

    # Samsung links its board protocol-relative; Medtronic-style query
    # strings must not leak into the site name.
    workday = _probe('<a href="//sec.wd3.myworkdayjobs.com/Samsung_Careers?src=x">Search jobs</a>')
    assert workday.external_identifier == "sec/Samsung_Careers"
    assert workday.board_url == "https://sec.wd3.myworkdayjobs.com/Samsung_Careers"
    workday = _probe('<a href="https://leidos.wd5.myworkdayjobs.com/External/login">Login</a>')
    assert workday.external_identifier == "leidos/External"
    assert workday.board_url == "https://leidos.wd5.myworkdayjobs.com/External"


@respx.mock
def test_probe_follows_comeet_plugin_position_links_to_a_verified_board() -> None:
    """Buildots/Kaltura: the WordPress plugin renders positions under the
    company's domain with no credentials on the listing page. A position
    page carries the company uid; the public board for it (slug = domain
    label) is accepted only after it actually serves COMPANY_DATA."""
    respx.get("https://buildots.example/careers/").mock(
        return_value=httpx.Response(
            200,
            text=(
                '<a href="/careers/E9.769/">Business Process Manager</a>'
                '<a href="/careers/32.D65/">Customer Enablement Manager</a>'
                '<a href="/careers/B3.76C/">Account Executive</a>'
            ),
        )
    )
    respx.get("https://buildots.example/careers/E9.769/").mock(
        return_value=httpx.Response(
            200, text="COMEET.init({'company-uid': \"36.004\", 'candidate-source-storage': false})"
        )
    )
    respx.get("https://www.comeet.com/jobs/buildots/36.004").mock(
        return_value=httpx.Response(
            200, text='var COMPANY_DATA = {"company_uid": "36.004", "token": "abc"};'
        )
    )

    resolved = resolve_career_source("https://buildots.example/careers/")

    assert resolved.source_type == CareerSourceType.COMEET
    assert resolved.external_identifier == "36.004"
    assert resolved.board_url == "https://www.comeet.com/jobs/buildots/36.004"


@respx.mock
def test_probe_rejects_a_comeet_board_guess_that_does_not_verify() -> None:
    respx.get("https://buildots.example/careers/").mock(
        return_value=httpx.Response(
            200,
            text=(
                '<a href="/careers/E9.769/">A</a><a href="/careers/32.D65/">B</a>'
                '<a href="/careers/B3.76C/">C</a>'
            ),
        )
    )
    respx.get("https://buildots.example/careers/E9.769/").mock(
        return_value=httpx.Response(200, text='"company-uid": "36.004"')
    )
    # A wrong slug redirects to the Comeet homepage - no COMPANY_DATA.
    respx.get("https://www.comeet.com/jobs/buildots/36.004").mock(
        return_value=httpx.Response(200, text="<html>Comeet homepage</html>")
    )

    resolved = resolve_career_source("https://buildots.example/careers/")

    assert resolved.source_type == CareerSourceType.GENERIC_HTML


@respx.mock
def test_probe_verifies_a_board_by_domain_label_only_with_an_ats_hint() -> None:
    """JFrog/AppsFlyer: the board is loaded purely from JS; the page only
    hints at Greenhouse (gh_department params). The domain label is tried
    as the board token and kept only if Greenhouse's API answers for it."""
    respx.get("https://join.jfrog.example/").mock(
        return_value=httpx.Response(
            200, text='<a href="/positions?gh_department=39295">R&amp;D 6 Open Positions</a>'
        )
    )
    respx.get("https://boards-api.greenhouse.io/v1/boards/jfrog/jobs").mock(
        return_value=httpx.Response(200, json={"jobs": [{"id": 1}], "meta": {"total": 1}})
    )
    resolved = resolve_career_source("https://join.jfrog.example/")
    assert resolved.source_type == CareerSourceType.GREENHOUSE
    assert resolved.external_identifier == "jfrog"
    assert resolved.board_url == "https://job-boards.greenhouse.io/jfrog"

    # Same page shape, but the API doesn't know the token: no guess kept.
    respx.get("https://join.acme.example/").mock(
        return_value=httpx.Response(200, text='<a href="/positions?gh_department=1">x</a>')
    )
    respx.get("https://boards-api.greenhouse.io/v1/boards/acme/jobs").mock(
        return_value=httpx.Response(404, json={"error": "not found"})
    )
    assert resolve_career_source("https://join.acme.example/").source_type == (
        CareerSourceType.GENERIC_HTML
    )

    # And without any hint the API is never asked at all.
    respx.get("https://www.island.example/careers").mock(
        return_value=httpx.Response(200, text="<html>plain page</html>")
    )
    api = respx.get("https://boards-api.greenhouse.io/v1/boards/island/jobs")
    assert resolve_career_source("https://www.island.example/careers").source_type == (
        CareerSourceType.GENERIC_HTML
    )
    assert not api.called


@respx.mock
def test_probe_prefers_embedded_board_over_jsonld() -> None:
    resolved = _probe(
        '<script type="application/ld+json">{"@type": "JobPosting"}</script>'
        '<script src="https://jobs.ashbyhq.com/crusoe/embed"></script>'
    )
    assert resolved.source_type == CareerSourceType.ASHBY


@respx.mock
def test_probe_detects_jsonld_job_posting() -> None:
    html = """
    <html><head>
    <script type="application/ld+json">
    {"@context": "https://schema.org/", "@type": "JobPosting", "title": "Junior Engineer"}
    </script>
    </head></html>
    """
    respx.get("https://careers.example.com/").mock(
        return_value=httpx.Response(200, text=html, headers={"content-type": "text/html"})
    )

    resolved = resolve_career_source("https://careers.example.com/")

    assert resolved.source_type == CareerSourceType.JSONLD


@respx.mock
def test_probe_falls_back_to_generic_html_without_jsonld() -> None:
    respx.get("https://careers.example.com/").mock(
        return_value=httpx.Response(200, text="<html><body>Jobs here</body></html>")
    )

    resolved = resolve_career_source("https://careers.example.com/")

    assert resolved.source_type == CareerSourceType.GENERIC_HTML


@respx.mock
def test_probe_failure_falls_back_to_generic_html_instead_of_raising() -> None:
    respx.get("https://careers.example.com/").mock(side_effect=httpx.ConnectTimeout("timed out"))

    resolved = resolve_career_source("https://careers.example.com/")

    assert resolved.source_type == CareerSourceType.GENERIC_HTML

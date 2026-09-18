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


def _board_api(url: str, *, alive: bool = True) -> None:
    """A board reference is kept only if its ATS's public API answers."""
    response = httpx.Response(200, json={"jobs": []}) if alive else httpx.Response(404)
    respx.get(url).mock(return_value=response)


@respx.mock
def test_probe_detects_greenhouse_embed_including_json_escaped_links() -> None:
    _board_api("https://boards-api.greenhouse.io/v1/boards/nexxen/jobs")
    _board_api("https://boards-api.greenhouse.io/v1/boards/similarweb/jobs")
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
    _board_api("https://api.ashbyhq.com/posting-api/job-board/nexxen")
    _board_api("https://apply.workable.com/api/v1/widget/accounts/anzu")
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
def test_probe_keeps_the_board_whose_api_answers() -> None:
    """Nexxen's page still loads the Greenhouse embed it migrated away
    from next to the Ashby board it uses now; the dead token used to be
    stored and 404 on every crawl."""
    _board_api("https://boards-api.greenhouse.io/v1/boards/nexxen/jobs", alive=False)
    _board_api("https://api.ashbyhq.com/posting-api/job-board/nexxen")

    resolved = _probe(
        '<script src="https://boards.greenhouse.io/embed/job_board/js?for=nexxen"></script>'
        '<script src="https://jobs.ashbyhq.com/nexxen/embed?version=2"></script>'
    )

    assert (resolved.source_type, resolved.external_identifier) == (
        CareerSourceType.ASHBY,
        "nexxen",
    )


@respx.mock
def test_probe_detects_a_board_api_called_from_the_page_and_lever_eu() -> None:
    """VIA's page fetches boards-api.greenhouse.io itself; Mobileye links
    its postings on Lever's EU region, which has its own API host."""
    _board_api("https://boards-api.greenhouse.io/v1/boards/via/jobs")
    via = _probe(
        "const fetchURL = { jobs: "
        '"https://boards-api.greenhouse.io/v1/boards/via/jobs?content=true" }'
    )
    assert (via.source_type, via.external_identifier) == (CareerSourceType.GREENHOUSE, "via")

    respx.get("https://api.eu.lever.co/v0/postings/mobileye", params={"mode": "json"}).mock(
        return_value=httpx.Response(200, json=[{"id": "x"}])
    )
    mobileye = _probe('<a href="https://jobs.eu.lever.co/mobileye/bb661a53-79b8/apply">Apply</a>')
    assert (mobileye.source_type, mobileye.external_identifier) == (
        CareerSourceType.LEVER,
        "mobileye",
    )
    assert mobileye.board_url == "https://jobs.eu.lever.co/mobileye"


@respx.mock
def test_probe_reads_the_comeet_uid_from_the_pages_own_script() -> None:
    """Plus500: the page loads the Comeet JS API, the company uid sits only
    in its general.js, and the public board is verified as for the plugin."""
    respx.get("https://company.example.com/js/general.js").mock(
        return_value=httpx.Response(
            200,
            text='url: "https://www.comeet.co/careers-api/2.0/company/A1.00F/positions?token=" + t',
        )
    )
    respx.get("https://www.comeet.com/jobs/example/A1.00F").mock(
        return_value=httpx.Response(
            200, text='var COMPANY_DATA = {"company_uid": "A1.00F", "token": "abc"};'
        )
    )

    resolved = _probe(
        '<script src="//www.comeet.co/careers-api/api.js"></script>'
        '<script src="/js/general.js"></script>'
    )

    assert resolved.source_type == CareerSourceType.COMEET
    assert resolved.external_identifier == "A1.00F"
    assert resolved.board_url == "https://www.comeet.com/jobs/example/A1.00F"


@respx.mock
def test_probe_prefers_embedded_board_over_jsonld() -> None:
    _board_api("https://api.ashbyhq.com/posting-api/job-board/crusoe")
    resolved = _probe(
        '<script type="application/ld+json">{"@type": "JobPosting"}</script>'
        '<script src="https://jobs.ashbyhq.com/crusoe/embed"></script>'
    )
    assert resolved.source_type == CareerSourceType.ASHBY


def test_site_feed_hosts_resolve_without_a_probe() -> None:
    cases = {
        "https://elbitsystemscareer.com/": "elbit",
        "https://jobs.iai.co.il/jobs/": "iai",
        "https://www.amazon.jobs/en/search?country=ISR": "amazon",
    }
    for url, feed in cases.items():
        resolved = resolve_career_source(url)
        assert (resolved.source_type, resolved.external_identifier) == (
            CareerSourceType.SITE_FEED,
            feed,
        ), url


@respx.mock
def test_probe_finds_a_wordpress_job_post_type() -> None:
    """Comblack: the list is drawn by JS, but the jobs are posts of a
    custom `careers` type the REST API lists."""
    types = "https://company.example.com/wp-json/wp/v2/types"
    respx.get(types).mock(
        return_value=httpx.Response(
            200, json={"post": {"rest_base": "posts"}, "careers": {"rest_base": "careers"}}
        )
    )
    respx.get("https://company.example.com/wp-json/wp/v2/careers", params={"per_page": "1"}).mock(
        return_value=httpx.Response(200, json=[{"id": 1}], headers={"X-WP-Total": "12"})
    )
    page = '<link href="/wp-content/themes/x/style.css">'

    resolved = _probe(page)

    assert (resolved.source_type, resolved.external_identifier) == (
        CareerSourceType.WORDPRESS,
        "careers",
    )

    # A page that itself lists more jobs than the type holds is read as it
    # is (Logica-it: 251 on the page, 68 in its job_listing type).
    listing = (
        page
        + "<ul>"
        + "".join(
            f'<li class="job"><a href="/jobs/{n}">Backend Developer {n}</a></li>' for n in range(20)
        )
        + "</ul>"
    )
    assert _probe(listing).source_type == CareerSourceType.GENERIC_HTML

    # A WordPress site without a job-like type stays generic.
    respx.get(types).mock(
        return_value=httpx.Response(200, json={"post": {"rest_base": "posts"}, "page": {}})
    )
    assert _probe(page).source_type == CareerSourceType.GENERIC_HTML


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

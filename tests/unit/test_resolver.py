from __future__ import annotations

import httpx
import respx

from app.ingestion.resolver import resolve_career_source
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
        "https://intel.wd1.myworkdayjobs.com/External": (CareerSourceType.WORKDAY, "intel"),
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
    """"clever.co" contains "lever.co" - a substring match would classify
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

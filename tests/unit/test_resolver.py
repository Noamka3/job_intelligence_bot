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
        "https://www.comeet.com/jobs/acme/F1.008": (CareerSourceType.COMEET, "acme"),
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

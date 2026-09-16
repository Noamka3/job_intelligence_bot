from __future__ import annotations

import httpx
import respx

from app.ingestion.adapters.base import JobStub
from app.ingestion.adapters.generic_html import GenericHtmlAdapter, extract_job_links
from app.models.career_source import CareerSource
from app.models.enums import CareerSourceType

_SOURCE = CareerSource(
    source_type=CareerSourceType.GENERIC_HTML,
    source_url="https://www.acme.co.il/careers/",
    external_identifier=None,
)

_LISTING = """
<html><body>
<nav><a href="/">Home</a><a href="/careers/">Careers</a><a href="/about">About us</a></nav>
<main>
  <h1>Open positions</h1>
  <ul class="positions">
    <li class="position"><a href="/careers/backend-developer-1234">Backend Developer</a></li>
    <li class="position"><a href="/careers/qa-engineer-1235">QA Engineer</a></li>
    <li class="position">
      <a href="https://www.acme.co.il/careers/data-analyst-1236#apply">Data Analyst</a>
    </li>
    <li class="position"><a href="/careers/product-manager-1237">מנהל/ת מוצר</a></li>
  </ul>
  <p>Read our <a href="/blog/culture">culture blog</a> and <a href="/careers/faq">FAQ</a>.</p>
  <a href="https://www.linkedin.com/company/acme">LinkedIn</a>
  <a href="/files/benefits.pdf">Benefits PDF</a>
</main>
<footer><a href="/privacy">Privacy</a><a href="/terms">Terms</a></footer>
</body></html>
"""


def test_extract_job_links_finds_the_repeated_list_and_ignores_navigation() -> None:
    links = extract_job_links(_LISTING, "https://www.acme.co.il/careers/")

    assert links == [
        ("https://www.acme.co.il/careers/backend-developer-1234", "Backend Developer"),
        ("https://www.acme.co.il/careers/qa-engineer-1235", "QA Engineer"),
        ("https://www.acme.co.il/careers/data-analyst-1236", "Data Analyst"),
        ("https://www.acme.co.il/careers/product-manager-1237", "מנהל/ת מוצר"),
    ]


def test_extract_job_links_needs_a_real_list_not_just_any_links() -> None:
    html = '<a href="/careers/faq">FAQ</a><a href="/about">About</a><a href="/blog">Blog</a>'
    assert extract_job_links(html, "https://www.acme.co.il/careers/") == []


def test_extract_job_links_keeps_stray_role_links_with_jobish_paths() -> None:
    html = (
        '<p>We are hiring a <a href="/jobs/senior-devops-engineer">Senior DevOps Engineer</a>!</p>'
    )
    assert extract_job_links(html, "https://www.acme.co.il/") == [
        ("https://www.acme.co.il/jobs/senior-devops-engineer", "Senior DevOps Engineer")
    ]


@respx.mock
def test_list_jobs_uses_the_listing_and_stable_hash_ids() -> None:
    respx.get("https://www.acme.co.il/careers/").mock(
        return_value=httpx.Response(200, text=_LISTING)
    )

    stubs = GenericHtmlAdapter().list_jobs(_SOURCE)

    assert len(stubs) == 4
    assert stubs[0].title == "Backend Developer"
    assert len(stubs[0].external_job_id) == 32
    assert stubs[0].external_job_id == GenericHtmlAdapter().list_jobs(_SOURCE)[0].external_job_id


@respx.mock
def test_fetch_job_prefers_jsonld_when_the_job_page_has_it() -> None:
    respx.get("https://www.acme.co.il/careers/backend-developer-1234").mock(
        return_value=httpx.Response(
            200,
            text="""
            <html><head><script type="application/ld+json">
            {"@type": "JobPosting", "title": "Backend Developer", "datePosted": "2026-09-01",
             "description": "<p>Build APIs.</p>", "employmentType": "FULL_TIME",
             "jobLocation": {"address": {"addressLocality": "Tel Aviv", "addressCountry": "IL"}}}
            </script></head><body><h1>Different H1</h1></body></html>
            """,
        )
    )
    url, title = extract_job_links(_LISTING, "https://www.acme.co.il/careers/")[0]

    details = GenericHtmlAdapter().fetch_job(
        _SOURCE, JobStub(external_job_id="x", title=title, source_url=url)
    )

    assert details.title == "Backend Developer"
    assert details.description == "Build APIs."
    assert details.location_text == "Tel Aviv, IL"
    assert details.source_published_at is not None


def test_card_anchors_use_their_heading_and_cta_anchors_use_the_card_title() -> None:
    """Real listings (Buildots, Island, Moveo): the anchor wraps a whole
    card, or is just a "View Details" button next to the title."""
    html = """
    <div class="jobs">
      <a class="card" href="/careers/E9.769/"><h3>Business Process Manager</h3>
        <span>Full-time</span><span>Tel-Aviv</span><span>Read more</span></a>
      <a class="card" href="/careers/32.D65/"><h3>Customer Enablement Manager</h3>
        <span>Full-time</span><span>Tel-Aviv</span><span>Read more</span></a>
      <a class="card" href="/careers/B3.76C/"><h3>Account Executive</h3><span>Read more</span></a>
    </div>
    <ul class="list">
      <li><h4>Network Security Engineer</h4><a href="/jobs/network-security">View Details</a></li>
      <li><h4>Integration Engineer</h4><a href="/jobs/integration-engineer">View Details</a></li>
      <li><h4>Systems Analyst</h4><a href="/jobs/systems-analyst">View Details</a></li>
    </ul>
    """
    titles = [title for _, title in extract_job_links(html, "https://acme.example/careers/")]
    assert titles == [
        "Business Process Manager",
        "Customer Enablement Manager",
        "Account Executive",
        "Network Security Engineer",
        "Integration Engineer",
        "Systems Analyst",
    ]


@respx.mock
def test_fetch_job_falls_back_to_h1_and_main_content() -> None:
    respx.get("https://www.acme.co.il/careers/qa-engineer-1235").mock(
        return_value=httpx.Response(
            200,
            text="""
            <html><head><title>QA Engineer | Acme</title></head><body>
            <header><nav><a href="/">Home</a></nav></header>
            <main><h1>QA Engineer</h1><p>Test everything.</p><ul><li>2+ years</li></ul></main>
            <footer>Acme 2026</footer><script>var x = 1;</script>
            </body></html>
            """,
        )
    )

    details = GenericHtmlAdapter().fetch_job(
        _SOURCE,
        JobStub(
            external_job_id="y",
            title="QA Engineer",
            source_url="https://www.acme.co.il/careers/qa-engineer-1235",
        ),
    )

    assert details.title == "QA Engineer"
    assert details.description == "QA Engineer\nTest everything.\n2+ years"
    assert details.location_text is None

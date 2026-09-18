from __future__ import annotations

import httpx
import pytest
import respx

from app.ingestion.adapters.base import JobStub, JobUnavailableError
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
def test_fetch_job_rejects_a_page_that_is_itself_a_listing() -> None:
    """GotFriends/Intuit-style: the listing linked to a category page or
    a marketing page listing more jobs - many job links, nothing to apply
    to. Must not become a "job" row."""
    listing_like = "".join(
        f'<li class="p"><a href="/jobs/role-{i}">Backend Developer {i}</a></li>' for i in range(8)
    )
    respx.get("https://www.acme.co.il/jobs/category/software").mock(
        return_value=httpx.Response(
            200, text=f"<html><body><h1>Software jobs</h1><ul>{listing_like}</ul></body></html>"
        )
    )
    with pytest.raises(JobUnavailableError):
        GenericHtmlAdapter().fetch_job(
            _SOURCE,
            JobStub(
                external_job_id="cat",
                title="Software jobs",
                source_url="https://www.acme.co.il/jobs/category/software",
            ),
        )


@respx.mock
def test_fetch_job_keeps_a_real_posting_that_has_a_more_jobs_sidebar() -> None:
    """Island/Moveo-style: a real posting with an apply button and a
    sidebar listing every other position."""
    sidebar = "".join(
        f'<li class="s"><a href="/positions/position-{i}">Engineer {i}</a></li>' for i in range(8)
    )
    body = " ".join(["Own the rollout across every customer team."] * 12)
    respx.get("https://www.acme.co.il/positions/position-1").mock(
        return_value=httpx.Response(
            200,
            text=(
                f"<html><body><main><aside><h4>All Positions</h4><ul>{sidebar}</ul></aside>"
                "<article><h1>Adoption Specialist</h1><p>Remote US</p><p>Full-time</p>"
                f'<p>{body}</p><a class="btn" href="#apply-form">Apply for this job</a>'
                "</article></main></body></html>"
            ),
        )
    )

    details = GenericHtmlAdapter().fetch_job(
        _SOURCE,
        JobStub(
            external_job_id="p1",
            title="Adoption Specialist",
            source_url="https://www.acme.co.il/positions/position-1",
        ),
    )

    assert details.title == "Adoption Specialist"
    assert details.location_text == "Remote US"  # short recognized line near the top
    assert details.description is not None
    assert "Own the rollout" in details.description
    # The "All Positions" sidebar is not part of *this* job's description.
    assert "Engineer 3" not in details.description
    assert "All Positions" not in details.description


@respx.mock
def test_fetch_job_treats_a_404_as_the_job_being_gone() -> None:
    respx.get("https://www.acme.co.il/careers/old-role").mock(return_value=httpx.Response(404))
    with pytest.raises(JobUnavailableError):
        GenericHtmlAdapter().fetch_job(
            _SOURCE,
            JobStub(
                external_job_id="old",
                title="Old",
                source_url="https://www.acme.co.il/careers/old-role",
            ),
        )


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


@respx.mock
def test_fetch_job_ignores_a_section_name_h1_and_uses_the_listing_title() -> None:
    """logica-it.com puts <h1>משרות</h1> on every job page (259 stored jobs
    were titled "משרות"); hibob.com does the same with "Careers"."""
    respx.get("https://www.acme.co.il/jobs/21072/").mock(
        return_value=httpx.Response(
            200,
            text="""
            <html><head><title>משרות | Acme</title></head><body>
            <h1>משרות</h1><h2>חיפוש משרה</h2>
            <main><p>Verification Engineer</p><p>מספר משרה: 21072</p>
            <p>לחברת מדיקל מובילה דרוש/ה מהנדס/ת וריפיקציה עם ניסיון בסביבת לינוקס, סקריפטים
            בפייתון ועבודה מול צוותי פיתוח. המשרה מיועדת לנשים וגברים כאחד.</p></main>
            </body></html>
            """,
        )
    )

    details = GenericHtmlAdapter().fetch_job(
        _SOURCE,
        JobStub(
            external_job_id="x",
            title="Verification Engineer",
            source_url="https://www.acme.co.il/jobs/21072/",
        ),
    )

    assert details.title == "Verification Engineer"


@respx.mock
def test_fetch_job_falls_back_to_the_first_real_line_when_every_heading_is_generic() -> None:
    respx.get("https://www.acme.co.il/jobs/21071/").mock(
        return_value=httpx.Response(
            200,
            text="""
            <html><body><h1>Careers</h1>
            <main><p>Jobs</p><p>איש/אשת טלפוניה, IVR ותקשורת</p><p>מספר משרה: 21071</p>
            <p>דרוש/ה איש/אשת טלפוניה לתפקיד מאתגר בסביבת תקשורת ארגונית, כולל עבודה עם מרכזיות
            IP, מערכות IVR, ניטור ותמיכה בלקוחות פנימיים. עבודה מלאה במרכז הארץ.</p></main>
            </body></html>
            """,
        )
    )

    details = GenericHtmlAdapter().fetch_job(
        _SOURCE,
        JobStub(external_job_id="y", title="Jobs", source_url="https://www.acme.co.il/jobs/21071/"),
    )

    assert details.title == "איש/אשת טלפוניה, IVR ותקשורת"


@respx.mock
def test_fetch_job_treats_a_search_results_page_as_unavailable() -> None:
    """Agency sites (Nisha Group, GotFriends) link category pages that say
    "מצאנו עבורך 28 משרות" and list them all - stored as one "job" with
    28 postings in its description, and an apply button on the page kept
    the link-count heuristic from catching it."""
    links = "".join(
        f'<li><a href="/jobs/backend-{i}">Backend Developer {i}</a></li>' for i in range(3)
    )
    respx.get("https://www.acme.co.il/jobs/backend/").mock(
        return_value=httpx.Response(
            200,
            text=f"""
            <html><body><h1>דרושים Backend Engineer</h1>
            <p>מצאנו עבורך 3 משרות Backend Engineer</p><ul>{links}</ul>
            <a href="/apply">הגש מועמדות</a>
            </body></html>
            """,
        )
    )

    with pytest.raises(JobUnavailableError):
        GenericHtmlAdapter().fetch_job(
            _SOURCE,
            JobStub(
                external_job_id="z",
                title="דרושים Backend Engineer",
                source_url="https://www.acme.co.il/jobs/backend/",
            ),
        )

"""Turning the JSON a page fetched into job stubs - the shapes real
career sites serve, and the many shapes that are not a job list."""

from __future__ import annotations

from app.ingestion.adapters import sniffed_feed

_PAGE = "https://www.acme.co.il/careers/"


def _ness_shaped_feed() -> list[dict[str, object]]:
    """Ness's /careers/api/Careers/GetAllItems: cards, no links anywhere
    in the rendered HTML, the whole posting in the JSON."""
    return [
        {
            "id": 4821,
            "title": "Junior Java Developer",
            "city": "Tel Aviv",
            "description": "<p>Join our backend team.</p>",
            "requirements": "<ul><li>Java</li><li>SQL</li></ul>",
            "url": "/careers/4821",
            "publishDate": "2026-09-20T08:00:00",
        },
        {
            "id": 4822,
            "title": "QA Automation Engineer",
            "city": "Haifa",
            "description": "Own the automation suite.",
        },
        {
            "id": 4823,
            "title": "DevOps Engineer",
            "city": "Tel Aviv",
            "description": "Run the pipelines.",
        },
    ]


def test_a_job_feed_becomes_stubs_with_absolute_urls() -> None:
    stubs = sniffed_feed.stubs_from_feeds([_ness_shaped_feed()], _PAGE)

    assert [stub.title for stub in stubs] == [
        "Junior Java Developer",
        "QA Automation Engineer",
        "DevOps Engineer",
    ]
    assert stubs[0].external_job_id == "4821"
    assert stubs[0].source_url == "https://www.acme.co.il/careers/4821"
    assert stubs[0].location_text == "Tel Aviv"
    # A row without a URL still points somewhere real - the listing page.
    assert stubs[1].source_url == _PAGE
    assert stubs[1].apply_url is None


def test_details_come_from_the_row_itself() -> None:
    (stub, *_) = sniffed_feed.stubs_from_feeds([_ness_shaped_feed()], _PAGE)

    assert sniffed_feed.is_feed_stub(stub)
    details = sniffed_feed.details_from_stub(stub)

    assert details.title == "Junior Java Developer"
    assert details.description is not None
    assert "Join our backend team." in details.description
    assert "Java" in details.description  # requirements are part of the text
    assert details.source_published_at is not None


def test_the_job_list_is_found_however_deeply_it_is_nested() -> None:
    wrapped = {"d": {"results": {"items": _ness_shaped_feed()}, "total": 3}}

    stubs = sniffed_feed.stubs_from_feeds([wrapped], _PAGE)

    assert len(stubs) == 3


def test_the_largest_titled_list_wins_over_menus_and_filters() -> None:
    """A page's data blob carries its navigation and filter options too;
    the postings are the longest list that reads as jobs."""
    document = {
        "menu": [{"title": "Home"}, {"title": "About"}, {"title": "Careers"}],
        "openings": _ness_shaped_feed()
        + [{"id": 4824, "title": "Data Analyst", "description": "Build reports."}],
    }

    stubs = sniffed_feed.stubs_from_feeds([document], _PAGE)

    assert len(stubs) == 4
    assert "Data Analyst" in {stub.title for stub in stubs}


def test_a_titled_list_whose_titles_are_not_job_titles_is_rejected() -> None:
    """Verbatim from the live run before this rule existed: Compie's
    client logos came back as a job called "Bank Hapoalim", its product
    cards as "Compie Software", and a Wix site's layout nodes as "head"
    - which is a role word, but only as a lone one."""
    client_logos = [
        {"id": 1, "title": "Bank Hapoalim", "logoPath": "/l/1.png", "categoryName": "Finance"},
        {"id": 2, "title": "Teva", "logoPath": "/l/2.png", "categoryName": "Pharma"},
        {"id": 3, "title": "Elbit", "logoPath": "/l/3.png", "categoryName": "Defence"},
        {"id": 4, "title": "Bezeq", "logoPath": "/l/4.png", "categoryName": "Telecom"},
    ]
    product_cards = [
        {"id": 1, "title": "AI/ML Cloud Solutions", "description": "What we build"},
        {"id": 2, "title": "Cloud Infrastructure", "description": "How we run it"},
        {"id": 3, "title": "XaaS - Everything-as-a-Service", "description": "Our model"},
        {"id": 4, "title": "Mission Command", "description": "Defence work"},
    ]
    wix_layout_nodes = [
        {"name": "head", "content": "c1", "position": "top"},
        {"name": "body", "content": "c2", "position": "mid"},
        {"name": "footer", "content": "c3", "position": "end"},
    ]

    for document in (client_logos, product_cards, wix_layout_nodes):
        assert sniffed_feed.stubs_from_feeds([document], _PAGE) == []


def test_the_postings_win_over_the_product_cards_on_the_same_page() -> None:
    """Compie fetches both. The product cards even have the "description"
    field the postings lack, so only the titles tell them apart."""
    product_cards = [
        {"id": 1, "title": "Compie Software", "description": "d"},
        {"id": 2, "title": "Compie Cloud", "description": "d"},
        {"id": 3, "title": "Defense", "description": "d"},
        {"id": 4, "title": "Compie Pro - Outsourcing", "description": "d"},
    ]
    postings = [
        {"id": 11, "title": "Full Stack Developer - Tel Aviv", "AboutTheRole": "Build things"},
        {"id": 12, "title": "Senior System Administrator - North", "AboutTheRole": "Run things"},
        {"id": 13, "title": "Mobile Developer (iOS / Android)", "AboutTheRole": "Ship apps"},
    ]

    stubs = sniffed_feed.stubs_from_feeds([product_cards, postings], _PAGE)

    assert [stub.title for stub in stubs] == [
        "Full Stack Developer - Tel Aviv",
        "Senior System Administrator - North",
        "Mobile Developer (iOS / Android)",
    ]


def test_documents_that_are_not_job_lists_are_ignored() -> None:
    """Verbatim shapes captured from the real sites: a contact form's
    validation rules, a cookie-consent config, and a font catalogue."""
    not_jobs = [
        [{"error": "required", "field": "email", "rule": "r1"}] * 4,
        [{"id": 1, "pdf_hash": "a"}, {"id": 2, "pdf_hash": "b"}, {"id": 3, "pdf_hash": "c"}],
        [{"cdnName": "x", "fontFamily": "Arial", "genericFamily": "sans"}] * 5,
        {"total": 0, "jobs": []},
        [],
    ]

    for document in not_jobs:
        assert sniffed_feed.stubs_from_feeds([document], _PAGE) == []


def test_a_row_without_a_title_is_skipped_and_a_short_list_is_not_a_feed() -> None:
    assert sniffed_feed.stubs_from_feeds([[{"id": 1}, {"id": 2}, {"id": 3}]], _PAGE) == []
    # Two postings are too few to tell a list from a coincidence.
    two = [{"title": "Backend Engineer"}, {"title": "Frontend Engineer"}]
    assert sniffed_feed.stubs_from_feeds([two], _PAGE) == []


def test_a_list_only_partly_made_of_job_titles_still_counts() -> None:
    """Ness's real feed is 207 postings of which 45% carry a recognised
    role word - the rest are titles like "מיישמ/ת SAP SD" that no
    vocabulary will ever cover. A job list does not have to be pure."""
    feed = [
        {"title": "Full Stack Developer", "description": "d"},
        {"title": "מפתח/ת תוכנה", "description": "d"},
        {"title": "מיישמ/ת SAP SD", "description": "d"},
        {"title": "הדרכה והטמעה", "description": "d"},
    ]

    stubs = sniffed_feed.stubs_from_feeds([feed], _PAGE)

    assert len(stubs) == 4


def test_hebrew_keys_and_nested_values_are_read() -> None:
    """Israeli feeds name things their own way, and wrap values in objects."""
    feed = [
        {"tl": "מפתח/ת Full Stack", "ct": {"name": "אשדוד"}, "dc": "תיאור התפקיד", "id": 7},
        {"tl": "מהנדס/ת תוכנה", "ct": "חיפה", "dc": "פיתוח מערכות", "id": 8},
        {"tl": "בודק/ת תוכנה", "ct": "תל אביב", "dc": "בדיקות תוכנה", "id": 9},
    ]

    stubs = sniffed_feed.stubs_from_feeds([feed], _PAGE)

    assert stubs[0].title == "מפתח/ת Full Stack"
    assert stubs[0].location_text == "אשדוד"
    assert sniffed_feed.details_from_stub(stubs[0]).description == "תיאור התפקיד"


def test_a_dangerous_url_in_a_feed_never_becomes_a_link() -> None:
    feed = [
        {"title": "Backend Engineer", "url": "javascript:alert(1)"},
        {"title": "Frontend Engineer", "url": "https://acme.co.il/jobs/2"},
        {"title": "Data Engineer", "url": "/jobs/3"},
    ]

    stubs = sniffed_feed.stubs_from_feeds([feed], _PAGE)
    by_title = {stub.title: stub for stub in stubs}

    assert by_title["Backend Engineer"].apply_url is None
    assert by_title["Frontend Engineer"].apply_url == "https://acme.co.il/jobs/2"
    assert by_title["Data Engineer"].apply_url == "https://www.acme.co.il/jobs/3"


def test_the_first_document_that_holds_a_job_list_is_the_one_used() -> None:
    noise = [{"error": "required", "field": "email"}] * 5
    stubs = sniffed_feed.stubs_from_feeds([noise, _ness_shaped_feed()], _PAGE)

    assert len(stubs) == 3

from __future__ import annotations

from app.services.jobs.html_text import html_to_text


def test_strips_tags_and_scripts() -> None:
    html = "<div><p>Hello <b>World</b></p><script>evil()</script></div>"
    assert html_to_text(html) == "Hello World"


def test_separates_paragraphs_with_newlines() -> None:
    html = "<p>First paragraph</p><p>Second paragraph</p>"
    assert html_to_text(html) == "First paragraph\nSecond paragraph"


def test_handles_none_and_empty() -> None:
    assert html_to_text(None) == ""
    assert html_to_text("") == ""

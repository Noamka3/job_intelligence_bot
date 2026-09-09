from __future__ import annotations

from app.services.candidate.normalization import normalize_text


def test_collapses_runs_of_whitespace() -> None:
    assert normalize_text("Hello   \t  World") == "Hello World"


def test_strips_control_characters() -> None:
    assert normalize_text("Hello\x00\x1fWorld") == "HelloWorld"


def test_collapses_excess_blank_lines() -> None:
    result = normalize_text("Line one\n\n\n\n\nLine two")
    assert result == "Line one\n\nLine two"


def test_strips_trailing_and_leading_whitespace_per_line() -> None:
    result = normalize_text("  Line one  \n  Line two  ")
    assert result == "Line one\nLine two"

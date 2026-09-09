from __future__ import annotations

from app.ingestion.adapters.base import JobDetails
from app.models.enums import EmploymentType
from app.services.jobs.normalization import (
    build_embedding_text,
    content_hash_for,
    normalize_job_title,
    normalize_location,
    strip_boilerplate,
)


def test_strip_boilerplate_removes_eeo_and_privacy_lines() -> None:
    text = (
        "We build great software.\n"
        "Acme is an Equal Opportunity Employer.\n"
        "See our Privacy Policy for details.\n"
        "Join our team!"
    )
    result = strip_boilerplate(text)
    assert "Equal Opportunity Employer" not in result
    assert "Privacy Policy" not in result
    assert "We build great software." in result
    assert "Join our team!" in result


def test_normalize_job_title_collapses_case_and_whitespace() -> None:
    assert normalize_job_title("  Software  Engineer I  ") == "software engineer i"


def test_normalize_location_applies_known_aliases() -> None:
    assert normalize_location("Tel Aviv-Yafo") == "tel aviv"
    assert normalize_location("Be'er Sheva") == "beer sheva"
    assert normalize_location(None) is None


def test_build_embedding_text_includes_key_fields() -> None:
    details = JobDetails(
        external_job_id="1",
        title="Junior Software Engineer",
        department="Engineering",
        location_text="Tel Aviv",
        employment_type=EmploymentType.FULL_TIME,
        required_skills=["Python", "SQL"],
        preferred_skills=["Docker"],
        responsibilities="Write code.",
        qualifications="0-2 years experience.",
        source_url="https://example.com/job/1",
    )
    text = build_embedding_text(details)
    assert "Junior Software Engineer" in text
    assert "Engineering" in text
    assert "Tel Aviv" in text
    assert "Python" in text
    assert "Docker" in text
    assert "Write code." in text
    assert "0-2 years experience." in text


def test_content_hash_is_stable_and_sensitive_to_change() -> None:
    text_a = "Junior Software Engineer\nPython"
    text_b = "Junior Software Engineer\nJava"
    assert content_hash_for(text_a) == content_hash_for(text_a)
    assert content_hash_for(text_a) != content_hash_for(text_b)

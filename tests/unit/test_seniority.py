from __future__ import annotations

from app.models.enums import SeniorityLevel
from app.services.matching.seniority import (
    assess_seniority,
    extract_min_years_required,
    score_seniority,
)


def test_senior_title_flags_senior_regardless_of_requirements_text() -> None:
    assessment = assess_seniority("Senior Backend Engineer", "5+ years of experience required")
    assert assessment.level == SeniorityLevel.SENIOR


def test_tl_abbreviation_flags_lead() -> None:
    """Found via live testing against real job titles ("Engineering TL")
    - the abbreviation wasn't originally covered.
    """
    assessment = assess_seniority("Engineering TL - Growth Team", "")
    assert assessment.level == SeniorityLevel.LEAD


def test_team_leader_flags_lead() -> None:
    """Also found via live testing ("R&D Team Leader") - \\blead\\b alone
    doesn't match inside "leader".
    """
    assessment = assess_seniority("R&D Team Leader", "")
    assert assessment.level == SeniorityLevel.LEAD


def test_body_text_mentioning_senior_does_not_flag_the_job() -> None:
    """spec §9's explicit counter-example: "work closely with senior
    engineers" in body text must not make an otherwise-junior job read
    as senior.
    """
    assessment = assess_seniority(
        "Software Engineer", "You will work closely with senior engineers."
    )
    assert assessment.level != SeniorityLevel.SENIOR


def test_software_engineer_one_reads_as_junior() -> None:
    assessment = assess_seniority("Software Engineer I", "0-2 years of experience")
    assert assessment.level == SeniorityLevel.JUNIOR


def test_software_engineer_two_does_not_read_as_junior_via_roman_suffix() -> None:
    assessment = assess_seniority("Software Engineer II", "")
    assert assessment.level != SeniorityLevel.JUNIOR


def test_junior_backend_developer_reads_as_junior() -> None:
    assessment = assess_seniority("Junior Backend Developer", "")
    assert assessment.level == SeniorityLevel.JUNIOR


def test_graduate_title_reads_as_junior_without_the_word_junior() -> None:
    assessment = assess_seniority("Graduate Platform Engineer", "")
    assert assessment.level == SeniorityLevel.JUNIOR


def test_plain_title_with_years_requirement_infers_level_from_years() -> None:
    assessment = assess_seniority("Software Engineer", "Requires 5+ years of experience")
    assert assessment.level == SeniorityLevel.SENIOR
    assert assessment.min_years_required == 5


def test_extract_min_years_required_various_phrasings() -> None:
    assert extract_min_years_required("5+ years of experience") == 5
    assert extract_min_years_required("3-5 years") == 3
    assert extract_min_years_required("minimum 2 years") == 2
    assert extract_min_years_required("2 years of relevant experience") == 2
    assert extract_min_years_required("no requirement mentioned") is None


def test_score_seniority_penalizes_senior_titles_despite_similarity() -> None:
    assessment = assess_seniority("Senior Backend Engineer", "7+ years")
    score, _ = score_seniority(assessment, max_expected_years=2)
    assert score <= 0.2


def test_score_seniority_rewards_junior_titles() -> None:
    assessment = assess_seniority("Software Engineer I", "0-2 years")
    score, _ = score_seniority(assessment, max_expected_years=2)
    assert score == 1.0


def test_score_seniority_scales_down_with_years_gap() -> None:
    # 4 years lands in the MID bucket (not a title-level "strongly senior"
    # signal), so this exercises the gradual years-gap falloff rather than
    # the flat penalty a title-level senior signal gets.
    assessment = assess_seniority("Software Engineer", "Requires 4 years of experience")
    assert assessment.level == SeniorityLevel.MID
    score, _ = score_seniority(assessment, max_expected_years=2)
    assert score == 0.5

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


def test_company_blurb_years_do_not_count_when_a_requirements_section_exists() -> None:
    """Reproduced on a real Greenhouse-style description: "With over 15
    years of experience in cybersecurity, Acme..." in the intro made a
    junior-friendly job read as SENIOR."""
    description = (
        "About us\n"
        "With over 15 years of experience in cybersecurity, Acme protects thousands "
        "of customers.\n"
        "Requirements\n"
        "2+ years of experience with Python\n"
        "BSc in Computer Science"
    )
    assessment = assess_seniority("Backend Developer", description)
    assert assessment.min_years_required == 2
    assert assessment.level == SeniorityLevel.JUNIOR


def test_implausible_years_are_ignored_even_without_a_section_heading() -> None:
    text = "With over 20 years in the industry, Acme builds great things."
    assert extract_min_years_required(text) is None


def test_several_requirements_take_the_largest_stated_minimum() -> None:
    text = "Requirements\n5+ years of backend experience. 1-2 years with Kubernetes is a plus."
    assert extract_min_years_required(text) == 5


def test_spelled_out_numbers_and_apostrophes_are_understood() -> None:
    assert extract_min_years_required("at least three years of experience") == 3
    assert extract_min_years_required("3 years' experience in Java") == 3
    assert extract_min_years_required("more than two years of hands-on experience") == 2


def test_software_engineer_digit_one_reads_as_junior() -> None:
    assert assess_seniority("Software Engineer 1", "").level == SeniorityLevel.JUNIOR
    assert assess_seniority("Software Engineer 11", "").level != SeniorityLevel.JUNIOR


def test_student_and_hebrew_titles_are_recognized() -> None:
    # A student position is INTERN, not JUNIOR: it is open only to people
    # still studying, so the dashboard keeps it in its own bucket.
    assert assess_seniority("Student Software Engineer", "").level == SeniorityLevel.INTERN
    assert assess_seniority("Software Developer - Student Position", "").level == (
        SeniorityLevel.INTERN
    )
    assert assess_seniority("סטודנט/ית לפיתוח תוכנה", "").level == SeniorityLevel.INTERN
    assert assess_seniority("Graduate Software Engineer", "").level == SeniorityLevel.JUNIOR
    assert assess_seniority("מפתח/ת Backend בכיר/ה", "").level == SeniorityLevel.SENIOR
    assert assess_seniority("מפתח/ת Fullstack ג'וניור", "").level == SeniorityLevel.JUNIOR
    assert assess_seniority("ראש צוות פיתוח", "").level == SeniorityLevel.LEAD


def test_score_seniority_penalizes_senior_titles_despite_similarity() -> None:
    assessment = assess_seniority("Senior Backend Engineer", "7+ years")
    score, _ = score_seniority(assessment, max_expected_years=2)
    assert score <= 0.2


def test_score_seniority_rewards_junior_titles() -> None:
    assessment = assess_seniority("Software Engineer I", "0-2 years")
    score, _ = score_seniority(assessment, max_expected_years=2)
    assert score == 1.0


def test_stated_years_outrank_a_junior_looking_title() -> None:
    """Live: "מפתח/ת Full Stack" asking for "לפחות 2-3 שנות ניסיון - חובה"
    scored 91% for a candidate with no experience, because two years read
    as "junior" and the junior label short-circuited the years check."""
    assessment = assess_seniority("מפתח/ת Full Stack", "לפחות 2-3 שנות ניסיון בפיתוח - חובה")
    assert assessment.min_years_required == 2

    score, reason = score_seniority(assessment, max_expected_years=1)

    assert score == 0.75
    assert "1 more than your target ceiling" in reason


def test_hebrew_years_requirements_are_read() -> None:
    """Live: "לפחות 4 שנות ניסיון" (AI Platform Developer, Shavit) read as
    "no clear seniority signal" and the job showed at 61% to a candidate
    with no experience."""
    assert extract_min_years_required("דרישות\nלפחות 4 שנות ניסיון בפיתוח Backend") == 4
    assert extract_min_years_required("ניסיון ב-SQL: 3+ שנות ניסיון מעשי") == 3
    assert extract_min_years_required("ניסיון של 5 שנים בפיתוח") == 5
    assert extract_min_years_required("חמש שנות ניסיון בתחום") == 5
    assert extract_min_years_required("ניסיון של שנתיים לפחות") == 2
    assert extract_min_years_required("שנה ניסיון בפייתון") == 1
    assert extract_min_years_required("ניסיון של שנה לפחות בפיתוח") == 1


def test_hebrew_ranges_contribute_their_lower_bound() -> None:
    assert extract_min_years_required("3-5 שנות ניסיון בפיתוח") == 3
    assert extract_min_years_required("דרישות\n3 עד 5 שנות ניסיון") == 3
    assert extract_min_years_required("Requirements\n3-5 years of experience in Java") == 3


def test_hebrew_years_without_experience_context_do_not_count() -> None:
    """ "תואר ראשון (3 שנות לימוד)" is the length of a degree, not a
    requirement of the candidate."""
    assert extract_min_years_required("דרישות\nתואר ראשון במדעי המחשב, 3 שנות לימוד") is None
    assert extract_min_years_required("החברה קיימת 20 שנים") is None


def test_hebrew_number_words_need_word_boundaries() -> None:
    assert extract_min_years_required("ניסיון של 13 שנים") == 13
    assert extract_min_years_required("ניסיון של 3 שנים") == 3


def test_posting_that_says_no_experience_needed_reads_as_junior() -> None:
    for text in (
        "דרישות\nללא ניסיון קודם, תואר במדעי המחשב",
        "דרישות\nניסיון לא חובה, נכונות ללמוד",
        "מה אנחנו מחפשים\nמשרה לבוגרים, אין צורך בניסיון",
        "Requirements\nNo prior experience required - we will train you",
        "Requirements\nFresh graduates are welcome",
    ):
        assessment = assess_seniority("Backend Developer", text)
        assert assessment.level == SeniorityLevel.JUNIOR, text
        assert assessment.min_years_required == 0


def test_explicit_years_beat_a_no_experience_phrase() -> None:
    text = "Requirements\n5+ years of backend experience. No prior Kubernetes experience required."
    assessment = assess_seniority("Backend Developer", text)
    assert assessment.level == SeniorityLevel.SENIOR
    assert assessment.min_years_required == 5


def test_negated_no_experience_phrase_is_not_a_junior_signal() -> None:
    text = "דרישות\nלא יתקבלו מועמדים ללא ניסיון"
    assert assess_seniority("Backend Developer", text).level == SeniorityLevel.UNKNOWN


def test_score_seniority_scales_down_with_years_gap() -> None:
    # 4 years lands in the MID bucket (not a title-level "strongly senior"
    # signal), so this exercises the gradual years-gap falloff rather than
    # the flat penalty a title-level senior signal gets.
    assessment = assess_seniority("Software Engineer", "Requires 4 years of experience")
    assert assessment.level == SeniorityLevel.MID
    score, _ = score_seniority(assessment, max_expected_years=2)
    assert score == 0.5

"""Seniority detection and scoring (spec §9).

Title is the strongest, most reliable signal - a senior/junior word
appearing only in body text ("work closely with senior engineers") must
NOT by itself flag a job's seniority; only the title and explicit numeric
experience requirements do. All token matching uses regex word
boundaries (not `in` substring checks) to avoid false positives like "sr"
matching inside "Assurance".
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from app.models.enums import SeniorityLevel

# English and Hebrew - Israeli ATSes (Comeet especially) post plenty of
# Hebrew titles ("מפתח/ת Backend בכיר/ה").
_SENIOR_TITLE_TOKENS: tuple[tuple[str, SeniorityLevel], ...] = (
    ("director", SeniorityLevel.DIRECTOR),
    ("head of", SeniorityLevel.DIRECTOR),
    ("vice president", SeniorityLevel.DIRECTOR),
    ("vp", SeniorityLevel.DIRECTOR),
    ("manager", SeniorityLevel.MANAGER),
    ("מנהל", SeniorityLevel.MANAGER),
    ("מנהלת", SeniorityLevel.MANAGER),
    ("principal", SeniorityLevel.PRINCIPAL),
    ("staff", SeniorityLevel.STAFF),
    ("lead", SeniorityLevel.LEAD),
    ("leader", SeniorityLevel.LEAD),
    ("tl", SeniorityLevel.LEAD),
    ("ראש צוות", SeniorityLevel.LEAD),
    ("architect", SeniorityLevel.SENIOR),
    ("senior", SeniorityLevel.SENIOR),
    ("sr.", SeniorityLevel.SENIOR),
    ("sr", SeniorityLevel.SENIOR),
    ("בכיר", SeniorityLevel.SENIOR),
    ("בכירה", SeniorityLevel.SENIOR),
    ("סניור", SeniorityLevel.SENIOR),
)

_JUNIOR_TITLE_TOKENS: tuple[tuple[str, SeniorityLevel], ...] = (
    ("internship", SeniorityLevel.INTERN),
    ("intern", SeniorityLevel.INTERN),
    ("trainee", SeniorityLevel.INTERN),
    ("junior", SeniorityLevel.JUNIOR),
    ("jr.", SeniorityLevel.JUNIOR),
    ("jr", SeniorityLevel.JUNIOR),
    ("graduate", SeniorityLevel.JUNIOR),
    ("new grad", SeniorityLevel.JUNIOR),
    ("entry level", SeniorityLevel.JUNIOR),
    ("entry-level", SeniorityLevel.JUNIOR),
    ("early career", SeniorityLevel.JUNIOR),
    ("associate", SeniorityLevel.JUNIOR),
    ("campus", SeniorityLevel.JUNIOR),
    ("student", SeniorityLevel.JUNIOR),
    ("students", SeniorityLevel.JUNIOR),
    ("ג'וניור", SeniorityLevel.JUNIOR),
    ("ג׳וניור", SeniorityLevel.JUNIOR),
    ("סטודנט", SeniorityLevel.JUNIOR),
    ("סטודנטית", SeniorityLevel.JUNIOR),
    ("מתחיל", SeniorityLevel.JUNIOR),
    ("מתחילה", SeniorityLevel.JUNIOR),
    ("בוגר", SeniorityLevel.JUNIOR),
    ("בוגרת", SeniorityLevel.JUNIOR),
)

# "Software Engineer I" / "Software Engineer 1" - a well-known entry-tier
# title convention (also the spec's own example). Deliberately only
# matches a trailing " I"/" 1", never "II"/"III"/"IV"/"11" (those read as
# progressively senior, not junior).
_ROMAN_ONE_SUFFIX = re.compile(r"(?<![I\d])\b(?:I|1)\b\s*$")

_WORD_NUMBERS = {
    "one": 1,
    "two": 2,
    "three": 3,
    "four": 4,
    "five": 5,
    "six": 6,
    "seven": 7,
    "eight": 8,
    "nine": 9,
    "ten": 10,
}
_NUMBER = r"(\d+|" + "|".join(_WORD_NUMBERS) + r")"
_YEARS = r"years?'?"

_YEARS_PATTERNS = [
    re.compile(rf"{_NUMBER}\s*\+\s*{_YEARS}", re.IGNORECASE),
    re.compile(rf"{_NUMBER}\s*(?:-|–|to)\s*(?:\d+|[a-z]+)\s*{_YEARS}", re.IGNORECASE),
    re.compile(
        rf"(?:minimum|min\.?|at least|over|more than)\s*(?:of\s*)?{_NUMBER}\s*{_YEARS}",
        re.IGNORECASE,
    ),
    re.compile(
        rf"{_NUMBER}\s*{_YEARS}\s*(?:of\s*)?"
        r"(?:relevant\s*|hands-on\s*|professional\s*|proven\s*|practical\s*|prior\s*)?"
        r"(?:experience|exp\b)",
        re.IGNORECASE,
    ),
]

# Nobody requires more than this of a candidate; larger numbers are
# company blurbs ("with over 20 years in the industry, Acme...").
_MAX_PLAUSIBLE_YEARS = 15

# Where a description switches from "about us / the role" to what it
# asks of the candidate. Most ATS descriptions (Greenhouse, Lever, Ashby,
# JSON-LD) arrive as one HTML blob with no structured requirements field,
# so this is the only way to keep "15 years in cybersecurity" from the
# company intro out of the experience-requirement scan.
_REQUIREMENTS_HEADING = re.compile(
    r"^\s*(?:"
    r"requirements?|qualifications?|minimum qualifications?|basic qualifications?"
    r"|what you(?:'ll| will)? (?:need|bring)|what we(?:'re| are) looking for"
    r"|who you are|about you|your profile|your experience|your background"
    r"|skills?(?: (?:and|&) (?:experience|qualifications?))?|must[- ]haves?"
    r"|you (?:have|bring|are)|we(?:'re| are) looking for"
    r"|דרישות(?: התפקיד)?|כישורים(?: נדרשים)?"
    r")\b[^\n]{0,60}$",
    re.IGNORECASE | re.MULTILINE,
)


def requirements_section(text: str) -> str:
    """The part of a description from its requirements heading onward, or
    the whole text when no such heading is found."""
    match = _REQUIREMENTS_HEADING.search(text)
    return text[match.start() :] if match else text


@dataclass(frozen=True)
class SeniorityAssessment:
    level: SeniorityLevel
    min_years_required: int | None
    signal: str


def _title_signal(title: str) -> tuple[SeniorityLevel, str] | None:
    for token, level in _SENIOR_TITLE_TOKENS:
        if re.search(rf"\b{re.escape(token)}\b", title, re.IGNORECASE):
            return level, f"title contains {token!r}"
    for token, level in _JUNIOR_TITLE_TOKENS:
        if re.search(rf"\b{re.escape(token)}\b", title, re.IGNORECASE):
            return level, f"title contains {token!r}"
    if _ROMAN_ONE_SUFFIX.search(title.strip()):
        return SeniorityLevel.JUNIOR, "title ends in 'I' (entry-level tier convention)"
    return None


def _as_years(token: str) -> int:
    return int(token) if token.isdigit() else _WORD_NUMBERS[token.lower()]


def extract_min_years_required(text: str) -> int | None:
    """The experience floor a posting states. With several requirements
    ("5+ years backend, 1-2 years with Kubernetes") that's the largest of
    the stated minimums, not the smallest - a range contributes its lower
    bound. Only the requirements section is scanned when the text has a
    recognizable one.
    """
    if not text:
        return None
    scope = requirements_section(text)
    candidates = [
        years
        for pattern in _YEARS_PATTERNS
        for match in pattern.finditer(scope)
        if (years := _as_years(match.group(1))) <= _MAX_PLAUSIBLE_YEARS
    ]
    return max(candidates) if candidates else None


def assess_seniority(title: str, requirements_text: str | None) -> SeniorityAssessment:
    """requirements_text is ideally qualifications/requirements copy; a
    whole description is acceptable - extract_min_years_required narrows
    it to the requirements section itself where it can - but the title is
    the only thing senior/junior *words* are read from: spec §9's
    counter-example ("work closely with senior engineers") is exactly the
    kind of unrelated sentence a description contains.
    """
    min_years = extract_min_years_required(requirements_text or "")

    title_signal = _title_signal(title)
    if title_signal is not None:
        level, signal = title_signal
        return SeniorityAssessment(level, min_years, signal)

    if min_years is not None:
        if min_years >= 5:
            return SeniorityAssessment(
                SeniorityLevel.SENIOR, min_years, f"requires {min_years}+ years"
            )
        if min_years <= 2:
            return SeniorityAssessment(
                SeniorityLevel.JUNIOR, min_years, f"requires {min_years} years (entry-friendly)"
            )
        return SeniorityAssessment(SeniorityLevel.MID, min_years, f"requires {min_years} years")

    return SeniorityAssessment(
        SeniorityLevel.UNKNOWN, None, "no clear seniority signal in title or requirements"
    )


_STRONGLY_SENIOR_LEVELS = frozenset(
    {
        SeniorityLevel.SENIOR,
        SeniorityLevel.STAFF,
        SeniorityLevel.PRINCIPAL,
        SeniorityLevel.LEAD,
        SeniorityLevel.MANAGER,
        SeniorityLevel.DIRECTOR,
    }
)


def score_seniority(
    assessment: SeniorityAssessment, max_expected_years: int | None
) -> tuple[float, str]:
    """(score in [0,1], human-readable explanation). max_expected_years
    comes from the TargetRole being scored against.

    A title-level senior signal is a strong penalty on its own (spec §9:
    "title = Senior Software Engineer -> strong penalty or exclusion"),
    independent of whether explicit years-of-experience text exists.
    """
    if assessment.level in _STRONGLY_SENIOR_LEVELS:
        return 0.05, f"looks senior-level ({assessment.signal})"

    if assessment.level in (SeniorityLevel.JUNIOR, SeniorityLevel.INTERN):
        return 1.0, f"entry-level signal ({assessment.signal})"

    if max_expected_years is not None and assessment.min_years_required is not None:
        gap = assessment.min_years_required - max_expected_years
        if gap <= 0:
            return 1.0, f"requires {assessment.min_years_required}y, within your target range"
        score = max(0.0, 1.0 - gap * 0.25)
        return (
            score,
            f"requires {assessment.min_years_required}y, {gap} more than your target ceiling",
        )

    return 0.5, "no clear seniority signal"

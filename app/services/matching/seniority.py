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

_SENIOR_TITLE_TOKENS: tuple[tuple[str, SeniorityLevel], ...] = (
    ("director", SeniorityLevel.DIRECTOR),
    ("head of", SeniorityLevel.DIRECTOR),
    ("vice president", SeniorityLevel.DIRECTOR),
    ("vp", SeniorityLevel.DIRECTOR),
    ("manager", SeniorityLevel.MANAGER),
    ("principal", SeniorityLevel.PRINCIPAL),
    ("staff", SeniorityLevel.STAFF),
    ("lead", SeniorityLevel.LEAD),
    ("leader", SeniorityLevel.LEAD),
    ("tl", SeniorityLevel.LEAD),
    ("architect", SeniorityLevel.SENIOR),
    ("senior", SeniorityLevel.SENIOR),
    ("sr.", SeniorityLevel.SENIOR),
    ("sr", SeniorityLevel.SENIOR),
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
)

# "Software Engineer I" - a well-known entry-tier title convention (also
# the spec's own example). Deliberately only matches a trailing " I",
# never "II"/"III"/"IV" (those read as progressively senior, not junior).
_ROMAN_ONE_SUFFIX = re.compile(r"(?<!I)\bI\b\s*$")

_YEARS_PATTERNS = [
    re.compile(r"(\d+)\s*\+\s*years?"),
    re.compile(r"(\d+)\s*-\s*(\d+)\s*years?"),
    re.compile(r"(?:minimum|min\.?|at least)\s*(\d+)\s*years?"),
    re.compile(r"(\d+)\s*years?\s*(?:of)?\s*(?:relevant\s*)?experience"),
]


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


def extract_min_years_required(text: str) -> int | None:
    if not text:
        return None
    candidates = [
        int(match.group(1)) for pattern in _YEARS_PATTERNS for match in pattern.finditer(text)
    ]
    return min(candidates) if candidates else None


def assess_seniority(title: str, requirements_text: str | None) -> SeniorityAssessment:
    """requirements_text should be qualifications/requirements copy, not
    the whole job description - spec §9's counter-example ("work closely
    with senior engineers") is exactly the kind of unrelated sentence a
    full description can contain.
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

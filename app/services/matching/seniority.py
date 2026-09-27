"""Seniority detection and scoring.

Title is the strongest, most reliable signal - a senior/junior word
appearing only in body text ("work closely with senior engineers") must
NOT by itself flag a job's seniority; only the title and explicit numeric
experience requirements do. All token matching uses regex word
boundaries (not `in` substring checks) to avoid false positives like "sr"
matching inside "Assurance".
"""

from __future__ import annotations

import math
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
    # INTERN means "you have to be a student to take it" - an
    # internship or a part-time student position, not a junior
    # full-time role. The dashboard tags those and keeps them out of
    # the default view (seniority_fit_expression in queries.py).
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
    ("student", SeniorityLevel.INTERN),
    ("students", SeniorityLevel.INTERN),
    ("ג'וניור", SeniorityLevel.JUNIOR),
    ("ג׳וניור", SeniorityLevel.JUNIOR),
    ("סטודנט", SeniorityLevel.INTERN),
    ("סטודנטית", SeniorityLevel.INTERN),
    ("מתחיל", SeniorityLevel.JUNIOR),
    ("מתחילה", SeniorityLevel.JUNIOR),
    ("בוגר", SeniorityLevel.JUNIOR),
    ("בוגרת", SeniorityLevel.JUNIOR),
)

# "Software Engineer I" / "Software Engineer 1" - a well-known entry-tier
# title convention. Deliberately only
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
# Hebrew postings write the number as a word at least as often as a
# digit: "ניסיון של שנתיים", "שלוש שנות ניסיון". "שנה"/"שנתיים" are the
# unit and the number in one word, so they get their own patterns.
_HEBREW_WORD_NUMBERS = {
    "שלוש": 3,
    "שלושה": 3,
    "ארבע": 4,
    "ארבעה": 4,
    "חמש": 5,
    "חמישה": 5,
    "שש": 6,
    "שישה": 6,
    "שבע": 7,
    "שבעה": 7,
    "שמונה": 8,
    "תשע": 9,
    "תשעה": 9,
    "עשר": 10,
    "עשרה": 10,
}
_NUMBER = r"(\d+(?:\.\d)?|" + "|".join(_WORD_NUMBERS) + r")"
# "years", "years'", "year's" - with either apostrophe.
_YEARS = r"year(?:s|[’']s|s[’'])?"
# (?<!\w)/(?!\w) rather than \b: "חמש" must not match inside "וחמש", and
# "3" must not match inside "13".
_HE_NUMBER = r"(?<!\w)(\d+|" + "|".join(_HEBREW_WORD_NUMBERS) + r")(?!\w)"
_HE_YEARS = r"שנ(?:ים|ות)"

# (pattern, is_hebrew). Ranges are read first and *removed* from the text
# before the single-number patterns run: "3-5 years of experience"
# contributes its lower bound (3), and the "5 years of experience" left
# in it must not win.
_RANGE_PATTERNS: list[tuple[re.Pattern[str], bool]] = [
    (re.compile(rf"{_NUMBER}\s*(?:-|–|to)\s*(?:\d+|[a-z]+)\s*{_YEARS}", re.IGNORECASE), False),
    (re.compile(rf"{_HE_NUMBER}\s*(?:-|–|עד)\s*\d+\s*{_HE_YEARS}"), True),
]
# What follows "N years" when the years are a requirement: "of
# experience", "of proven record in", "hands-on test automation
# experience", "in managing", "as a developer", "with Python". Up to
# three words may sit between the years and this.
_EXPERIENCE_NOUN = (
    r"(?:experience|exp\b|record|success|background|work(?:ing)?\b"
    r"|in\b|as\b|with\b|developing|building|managing|leading|writing)"
)
_SINGLE_PATTERNS: list[tuple[re.Pattern[str], bool]] = [
    (re.compile(rf"{_NUMBER}\s*\+\s*{_YEARS}", re.IGNORECASE), False),
    (
        re.compile(
            rf"(?:minimum|min\.?|at least|over|more than)\s*(?:of\s*)?{_NUMBER}\s*{_YEARS}",
            re.IGNORECASE,
        ),
        False,
    ),
    (
        re.compile(
            rf"{_NUMBER}\s*\+?\s*{_YEARS}\s*(?:of\s+)?(?:[\w/'’-]+\s+){{0,3}}?{_EXPERIENCE_NOUN}",
            re.IGNORECASE,
        ),
        False,
    ),
    # "3 שנות ניסיון", "לפחות 4 שנים", "3+ שנים", "ניסיון של 5 שנים",
    # "חמש שנות ניסיון" - all number + unit; the "ניסיון" context check
    # keeps "תואר של 3 שנים" out.
    (re.compile(rf"{_HE_NUMBER}\s*\+?\s*{_HE_YEARS}"), True),
]
# "ניסיון של שנתיים", "שנתיים ניסיון", "שנה ניסיון לפחות", "ניסיון של שנה".
_HE_ONE_TWO_PATTERNS = [
    re.compile(r"ניסיון\s+(?:של\s+)?(?:לפחות\s+)?(שנה|שנתיים)(?!\w)"),
    re.compile(r"(?<!\w)(שנה|שנתיים)\s+(?:של\s+)?(?:לפחות\s+)?ניסיון"),
]
_HE_ONE_TWO = {"שנה": 1, "שנתיים": 2}
# A Hebrew number-of-years only counts as an experience requirement when
# "ניסיון" is nearby; English postings say "years" mostly in that sense
# already, Hebrew ones also give the length of a degree in years.
_HE_CONTEXT_CHARS = 60
# An English "N years" that is not a requirement: age, company history,
# time left in a degree. Checked on the match and the words after it.
_NOT_A_REQUIREMENT_RE = re.compile(
    r"\b(?:old|of age|ago|in a row|remaining|left|graduation)\b", re.IGNORECASE
)
_EN_CONTEXT_CHARS = 30

# Postings that say, in so many words, that experience is not required.
# Read from the requirements section only, and only when no explicit
# years requirement contradicts it ("no prior Kubernetes experience
# required" next to "5+ years backend").
_NO_EXPERIENCE_RE = re.compile(
    r"ללא\s+ניסיון|לא\s+נדרש\s+ניסיון|אין\s+צורך\s+בניסיון"
    r"|ניסיון\s+(?:קודם\s+)?(?:לא|אינו)\s+(?:חובה|נדרש|הכרחי)"
    r"|תחילת\s+דרך|משרה\s+לבוגרים|בוגרי\s+תואר|סטודנטים\s+ובוגרים"
    r"|no\s+(?:prior\s+|previous\s+)?(?:work\s+)?experience\s+(?:is\s+)?"
    r"(?:required|needed|necessary)"
    r"|no\s+prior\s+experience|(?:fresh|recent|new)\s+graduates?|entry[- ]level",
    re.IGNORECASE,
)
# "לא יתקבלו מועמדים ללא ניסיון" says the opposite.
_NO_EXPERIENCE_NEGATION_RE = re.compile(r"לא\s+יתקבל|not\s+(?:be\s+)?consider", re.IGNORECASE)

# Nobody requires more than this of a candidate; larger numbers are
# company blurbs ("with over 20 years in the industry, Acme...").
_MAX_PLAUSIBLE_YEARS = 15

# Where a description switches from "about us / the role" to what it
# asks of the candidate. Most ATS descriptions (Greenhouse, Lever, Ashby,
# JSON-LD) arrive as one HTML blob with no structured requirements field,
# so this is the only way to keep "15 years in cybersecurity" from the
# company intro out of the experience-requirement scan. "Preferred
# qualifications" is deliberately not here: it follows the required ones,
# and starting the section there lost "4+ years of DevOps experience".
_REQUIREMENTS_HEADING = re.compile(
    r"^\s*(?:"
    r"requirements?|(?:required |minimum |basic )?qualifications?|all you need"
    r"|what(?:'s| is) needed|what you(?:'ll| will)? (?:need|bring)|what we(?:'re| are) looking for"
    r"|experience (?:and|&) skills"
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
    if token[0].isdigit():
        # "1.5 years" asks for more than one year, so it rounds up.
        return math.ceil(float(token))
    return _WORD_NUMBERS.get(token.lower()) or _HEBREW_WORD_NUMBERS[token]


def _mentions_experience_nearby(scope: str, match: re.Match[str]) -> bool:
    window = scope[max(0, match.start() - _HE_CONTEXT_CHARS) : match.end() + _HE_CONTEXT_CHARS]
    return "ניסיון" in window


def extract_min_years_required(text: str) -> int | None:
    """The experience floor a posting states, in English or Hebrew. With
    several requirements ("5+ years backend, 1-2 years with Kubernetes")
    that's the largest of the stated minimums, not the smallest - a range
    contributes its lower bound. Only the requirements section is scanned
    when the text has a recognizable one.
    """
    if not text:
        return None
    scope = requirements_section(text)
    candidates: list[int] = []

    def collect(pattern: re.Pattern[str], hebrew: bool) -> None:
        for match in pattern.finditer(scope):
            if hebrew and not _mentions_experience_nearby(scope, match):
                continue
            if not hebrew and _NOT_A_REQUIREMENT_RE.search(
                scope[match.start() : match.end() + _EN_CONTEXT_CHARS]
            ):
                continue
            years = _as_years(match.group(1))
            if years <= _MAX_PLAUSIBLE_YEARS:
                candidates.append(years)

    for pattern, hebrew in _RANGE_PATTERNS:
        collect(pattern, hebrew)
        scope = pattern.sub(" ", scope)
    for pattern, hebrew in _SINGLE_PATTERNS:
        collect(pattern, hebrew)
    for pattern in _HE_ONE_TWO_PATTERNS:
        candidates.extend(_HE_ONE_TWO[match.group(1)] for match in pattern.finditer(scope))
    return max(candidates) if candidates else None


def says_no_experience_needed(text: str) -> bool:
    """True when the requirements say experience isn't needed (Hebrew or
    English) and don't negate it in the same breath."""
    if not text:
        return False
    scope = requirements_section(text)
    for match in _NO_EXPERIENCE_RE.finditer(scope):
        before = scope[max(0, match.start() - 40) : match.start()]
        if not _NO_EXPERIENCE_NEGATION_RE.search(before):
            return True
    return False


def assess_seniority(title: str, requirements_text: str | None) -> SeniorityAssessment:
    """requirements_text is ideally qualifications/requirements copy; a
    whole description is acceptable - extract_min_years_required narrows
    it to the requirements section itself where it can - but the title is
    the only thing senior/junior *words* are read from: the classic
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

    if says_no_experience_needed(requirements_text or ""):
        return SeniorityAssessment(SeniorityLevel.JUNIOR, 0, "posting says no experience is needed")

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

    A title-level senior signal is a strong penalty on its own (a "Senior
    Software Engineer" title is a strong penalty or an exclusion),
    independent of whether explicit years-of-experience text exists.
    """
    if assessment.level in _STRONGLY_SENIOR_LEVELS:
        return 0.05, f"looks senior-level ({assessment.signal})"

    # Stated years outrank an entry-level read of the title: "2-3 years
    # mandatory" is a requirement whatever the title looks like.
    if max_expected_years is not None and assessment.min_years_required is not None:
        gap = assessment.min_years_required - max_expected_years
        if gap <= 0:
            return 1.0, f"requires {assessment.min_years_required}y, within your target range"
        score = max(0.0, 1.0 - gap * 0.25)
        return (
            score,
            f"requires {assessment.min_years_required}y, {gap} more than your target ceiling",
        )

    if assessment.level in (SeniorityLevel.JUNIOR, SeniorityLevel.INTERN):
        return 1.0, f"entry-level signal ({assessment.signal})"

    return 0.5, "no clear seniority signal"

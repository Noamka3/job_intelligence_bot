"""Would a recruiter shortlist this CV for this posting, as Jev judges
it. TypeSafe's own composite-scoring recipe is resume screening, and
this is that recipe: one yes/no on the whole, then the parts a human
would check - skill coverage and the level match - so a disagreement
with the rule-based score can be read, not just counted.

Judged once per (match, posting text): the content hash is stored with
the answers. Only postings that passed the role gate are judged, both
because that is where the hybrid score is least sure and because it
keeps the calls to hundreds, not thousands.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

from typesafe_sdk import Choice, ChoiceAnswer, Noul, NoulAnswer, NoulCriteria, Score, ScoreAnswer

from app.models.candidate_profile import CandidateProfile
from app.models.job_match import JobMatch
from app.models.job_posting import JobPosting
from app.models.target_role import TargetRole
from app.services.jev.client import MAX_STATE_CHARS, Question, ask, is_enabled

# The role gate's role_fit below which a posting is not judged: the
# score already says it is not the role, and Jev is not asked to argue.
JUDGED_ABOVE_ROLE_FIT = 0.5

SKILLS_COVERAGE_LEVELS = [
    "None of the required skills appear in the resume",
    "A few of the required skills",
    "About half of the required skills",
    "Most of the required skills",
    "All of the required skills",
]
EXPERIENCE_LEVELS = {
    "below": "The posting asks for more experience than the candidate has",
    "matches": "The candidate's experience fits what the posting asks for",
    "above": "The candidate has clearly more experience than the posting needs",
}
_QUESTIONS: dict[str, Question] = {
    "would_be_considered": Noul(
        instructions="A recruiter would consider this candidate a plausible applicant "
        "for this posting",
        criteria=NoulCriteria(
            true="The candidate's background matches the posting's field, level and "
            "core requirements closely enough to be shortlisted",
            false="The candidate is in a different field, at a clearly different level, "
            "or misses most of the core requirements",
        ),
    ),
    "skills_coverage": Score(
        instructions="How many of the skills the posting requires the candidate demonstrably has",
        criteria=SKILLS_COVERAGE_LEVELS,
    ),
    "experience_level": Choice(
        instructions="The candidate's experience relative to what the posting asks for",
        criteria=EXPERIENCE_LEVELS,
    ),
}


@dataclass(frozen=True)
class FitJudgement:
    model: str
    content_hash: str
    would_be_considered: float
    skills_coverage: float  # expected level on the 0-4 rubric
    experience_level: str
    experience_level_confidence: float


def needs_judgement(match: JobMatch, job: JobPosting) -> bool:
    """True when Jev is on and has not judged this text of the posting."""
    if not is_enabled():
        return False
    stored = match.jev_fit or {}
    return stored.get("content_hash") != job.content_hash


def fit_probability(match: JobMatch) -> float | None:
    """Jev's P(a recruiter would shortlist) from a stored judgement."""
    return float(match.jev_fit["would_be_considered"]) if match.jev_fit else None


def judge_fit(
    candidate: CandidateProfile, target_role: TargetRole, job: JobPosting
) -> dict[str, Any] | None:
    """Jev's judgement of `candidate` for `job`, as the dict stored in
    JobMatch.jev_fit, or None when Jev is off or the call failed. The
    target role says what the candidate is looking for - without it, a
    graduate's CV was judged a fine fit for student positions."""
    response = ask(_state(candidate, target_role, job), _QUESTIONS)
    if response is None:
        return None
    answers = response.answers
    considered, coverage = answers["would_be_considered"], answers["skills_coverage"]
    level = answers["experience_level"]
    assert isinstance(considered, NoulAnswer) and isinstance(coverage, ScoreAnswer)
    assert isinstance(level, ChoiceAnswer)
    return asdict(
        FitJudgement(
            model=response.model,
            content_hash=job.content_hash,
            would_be_considered=considered.noul,
            skills_coverage=coverage.score,
            experience_level=level.choice,
            experience_level_confidence=level.confidence,
        )
    )


def _state(candidate: CandidateProfile, target_role: TargetRole, job: JobPosting) -> dict[str, Any]:
    text = job.normalized_description or job.description or ""
    return {
        "resume": candidate.normalized_text[:MAX_STATE_CHARS],
        "looking_for": target_role.description or target_role.canonical_name,
        "job_posting": {"title": job.title, "description": text[:MAX_STATE_CHARS]},
    }

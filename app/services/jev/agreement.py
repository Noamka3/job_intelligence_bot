"""How well each scorer predicts the feedback given in the dashboard -
the number JEV_WEIGHT is tuned against, instead of a feeling.

Positive feedback (interested, applied, saved) should score above
negative feedback (not relevant, too senior, wrong field, rejected).
Pairwise accuracy is the share of (positive, negative) pairs a scorer
ranks that way: 1.0 is perfect, 0.5 is a coin flip. "Wrong location" is
left out - it says nothing about fit.
"""

from __future__ import annotations

from dataclasses import dataclass
from itertools import product

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.enums import JobFeedbackAction
from app.models.job_feedback import JobFeedback
from app.models.job_match import JobMatch
from app.models.job_posting import JobPosting
from app.models.target_role import TargetRole
from app.services.candidate.profile_service import get_active_profile
from app.services.jev.fit import fit_probability
from app.services.matching.scoring import candidate_skill_set, compute_match

POSITIVE = frozenset(
    {JobFeedbackAction.INTERESTED, JobFeedbackAction.APPLIED, JobFeedbackAction.SAVED}
)
NEGATIVE = frozenset(
    {
        JobFeedbackAction.NOT_RELEVANT,
        JobFeedbackAction.TOO_SENIOR,
        JobFeedbackAction.WRONG_FIELD,
        JobFeedbackAction.REJECTED,
    }
)


@dataclass(frozen=True)
class Agreement:
    positives: int
    negatives: int
    rules_pairwise: float | None
    jev_pairwise: float | None
    judged_positives: int
    judged_negatives: int


def pairwise_accuracy(positives: list[float], negatives: list[float]) -> float | None:
    """Share of (positive, negative) pairs ranked positive-first; a tie
    counts half. None without at least one of each."""
    if not positives or not negatives:
        return None
    right = sum(1.0 if p > n else 0.5 if p == n else 0.0 for p, n in product(positives, negatives))
    return right / (len(positives) * len(negatives))


def measure(db: Session) -> Agreement | None:
    """Rules-only scores are recomputed so the comparison stays fair once
    Jev has a weight in the stored score. None without an active CV."""
    candidate = get_active_profile(db)
    if candidate is None:
        return None
    latest = (
        select(JobFeedback.job_id, func.max(JobFeedback.id).label("feedback_id"))
        .group_by(JobFeedback.job_id)
        .subquery()
    )
    rows = db.execute(
        select(JobFeedback.action, JobMatch, JobPosting, TargetRole)
        .join(latest, JobFeedback.id == latest.c.feedback_id)
        .join(JobMatch, JobMatch.job_id == JobFeedback.job_id)
        .join(JobPosting, JobPosting.id == JobMatch.job_id)
        .join(TargetRole, TargetRole.id == JobMatch.target_role_id)
        .where(JobMatch.candidate_profile_id == candidate.id)
    ).all()

    skills = candidate_skill_set(candidate)
    rules: dict[bool, list[float]] = {True: [], False: []}
    jev: dict[bool, list[float]] = {True: [], False: []}
    for action, match, job, role in rows:
        if action not in POSITIVE and action not in NEGATIVE:
            continue
        positive = action in POSITIVE
        rules[positive].append(
            compute_match(candidate, role, job, candidate_skills=skills).final_score
        )
        if (fit := fit_probability(match)) is not None:
            jev[positive].append(fit)
    return Agreement(
        positives=len(rules[True]),
        negatives=len(rules[False]),
        rules_pairwise=pairwise_accuracy(rules[True], rules[False]),
        jev_pairwise=pairwise_accuracy(jev[True], jev[False]),
        judged_positives=len(jev[True]),
        judged_negatives=len(jev[False]),
    )

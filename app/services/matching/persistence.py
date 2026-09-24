"""Persists a MatchResult as a JobMatch row - one per (candidate_profile,
target_role, job), recomputed in place rather than appended (the
unique constraint is the source of truth for this).
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.job_match import JobMatch
from app.services.matching.scoring import MatchResult


def save_match(
    db: Session, candidate_profile_id: int, target_role_id: int, job_id: int, result: MatchResult
) -> JobMatch:
    existing = db.execute(
        select(JobMatch).where(
            JobMatch.candidate_profile_id == candidate_profile_id,
            JobMatch.target_role_id == target_role_id,
            JobMatch.job_id == job_id,
        )
    ).scalar_one_or_none()

    if existing is None:
        existing = JobMatch(
            candidate_profile_id=candidate_profile_id,
            target_role_id=target_role_id,
            job_id=job_id,
        )
        db.add(existing)

    existing.candidate_semantic_score = result.candidate_semantic_score
    existing.intent_semantic_score = result.intent_semantic_score
    existing.skill_score = result.skill_score
    existing.role_score = result.role_score
    existing.seniority_score = result.seniority_score
    existing.location_score = result.location_score
    existing.recency_score = result.recency_score
    existing.final_score = result.final_score
    existing.reasons = result.reasons
    existing.concerns = result.concerns

    db.flush()
    return existing

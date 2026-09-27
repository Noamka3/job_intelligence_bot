"""Orchestrates running the scorer across jobs / target roles and
persisting results - what the CLI `score-all` command calls now, and what
the crawl pipeline will call per-job from Phase 6 onward. Kept separate
from scoring.py so the pure scoring math has no DB/session dependency and
stays trivially unit-testable.
"""

from __future__ import annotations

import logging

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.candidate_profile import CandidateProfile
from app.models.enums import JobStatus
from app.models.job_posting import JobPosting
from app.models.target_role import TargetRole
from app.services.candidate.profile_service import get_active_profile
from app.services.jev import JUDGED_ABOVE_ROLE_FIT, fit_probability, judge_fit, needs_judgement
from app.services.matching.persistence import save_match
from app.services.matching.scoring import candidate_skill_set, compute_match

logger = logging.getLogger(__name__)


def score_job(db: Session, job: JobPosting) -> int:
    """Scores one job against the active candidate profile and every
    enabled TargetRole. Returns how many JobMatch rows were written (0 if
    there's no active profile or no enabled target role yet).
    """
    candidate = get_active_profile(db)
    if candidate is None:
        return 0
    return _score_job_for(db, candidate, candidate_skill_set(candidate), job)


def _score_job_for(
    db: Session, candidate: CandidateProfile, candidate_skills: set[str], job: JobPosting
) -> int:
    target_roles = db.execute(select(TargetRole).where(TargetRole.enabled.is_(True))).scalars()
    count = 0
    for target_role in target_roles:
        result = compute_match(candidate, target_role, job, candidate_skills=candidate_skills)
        match = save_match(db, candidate.id, target_role.id, job.id, result)
        if result.role_score >= JUDGED_ABOVE_ROLE_FIT and needs_judgement(match, job):
            match.jev_fit = judge_fit(candidate, job)
        if (fit := fit_probability(match)) is not None:
            # Scored again with Jev's judgement taking its share (JEV_WEIGHT).
            with_jev = compute_match(
                candidate, target_role, job, candidate_skills=candidate_skills, jev_fit=fit
            )
            save_match(db, candidate.id, target_role.id, job.id, with_jev)
        count += 1
    return count


def score_all_active_jobs(db: Session) -> int:
    candidate = get_active_profile(db)
    if candidate is None:
        return 0
    # Extracted once here rather than once per job - it's the same CV
    # text every time, and the regex pass over it isn't free.
    candidate_skills = candidate_skill_set(candidate)
    jobs = (
        db.execute(
            select(JobPosting).where(
                JobPosting.status == JobStatus.ACTIVE, JobPosting.archived_at.is_(None)
            )
        )
        .scalars()
        .all()
    )
    total_matches = 0
    for index, job in enumerate(jobs, start=1):
        total_matches += _score_job_for(db, candidate, candidate_skills, job)
        if index % 500 == 0:
            db.commit()  # keep transactions short - see crawl_source for why
    db.commit()
    logger.info(
        "scored active jobs", extra={"job_count": len(jobs), "matches_written": total_matches}
    )
    return total_matches

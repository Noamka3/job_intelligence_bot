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

from app.models.enums import JobStatus
from app.models.job_posting import JobPosting
from app.models.target_role import TargetRole
from app.services.candidate.profile_service import get_active_profile
from app.services.matching.persistence import save_match
from app.services.matching.scoring import compute_match

logger = logging.getLogger(__name__)


def score_job(db: Session, job: JobPosting) -> int:
    """Scores one job against the active candidate profile and every
    enabled TargetRole. Returns how many JobMatch rows were written (0 if
    there's no active profile or no enabled target role yet).
    """
    candidate = get_active_profile(db)
    if candidate is None:
        return 0

    target_roles = db.execute(select(TargetRole).where(TargetRole.enabled.is_(True))).scalars()
    count = 0
    for target_role in target_roles:
        result = compute_match(candidate, target_role, job)
        save_match(db, candidate.id, target_role.id, job.id, result)
        count += 1
    return count


def score_all_active_jobs(db: Session) -> int:
    jobs = (
        db.execute(select(JobPosting).where(JobPosting.status == JobStatus.ACTIVE)).scalars().all()
    )
    total_matches = 0
    for job in jobs:
        total_matches += score_job(db, job)
    db.commit()
    logger.info(
        "scored active jobs", extra={"job_count": len(jobs), "matches_written": total_matches}
    )
    return total_matches

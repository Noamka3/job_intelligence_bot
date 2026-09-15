from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.models.company import Company
from app.models.enums import JobStatus
from app.models.job_match import JobMatch
from app.models.job_posting import JobPosting
from app.schemas.match import MatchRead
from app.services.candidate.profile_service import get_active_profile

router = APIRouter(prefix="/matches", tags=["matches"])


@router.get("/top", response_model=list[MatchRead])
def top_matches(
    target_role_id: int | None = None,
    min_score: float = Query(0.0, ge=0.0, le=100.0),
    limit: int = Query(50, ge=1, le=200),
    db: Session = Depends(get_db),
) -> list[MatchRead]:
    candidate = get_active_profile(db)
    if candidate is None:
        return []

    query = (
        select(JobMatch, JobPosting, Company)
        .join(JobPosting, JobMatch.job_id == JobPosting.id)
        .join(Company, JobPosting.company_id == Company.id)
        .where(
            JobMatch.candidate_profile_id == candidate.id,
            JobMatch.final_score >= min_score,
            # JobMatch rows are kept when a job closes (history), so the
            # filter has to happen here - a great match for a job that's
            # gone is not something to apply to.
            JobPosting.status == JobStatus.ACTIVE,
        )
        .order_by(JobMatch.final_score.desc())
        .limit(limit)
    )
    if target_role_id is not None:
        query = query.where(JobMatch.target_role_id == target_role_id)

    rows = db.execute(query).all()
    return [
        MatchRead(
            id=match.id,
            job_id=job.id,
            target_role_id=match.target_role_id,
            final_score=match.final_score,
            candidate_semantic_score=match.candidate_semantic_score,
            intent_semantic_score=match.intent_semantic_score,
            skill_score=match.skill_score,
            role_score=match.role_score,
            seniority_score=match.seniority_score,
            location_score=match.location_score,
            recency_score=match.recency_score,
            reasons=match.reasons,
            concerns=match.concerns,
            created_at=match.created_at,
            job_title=job.title,
            company_name=company.name,
            location_text=job.location_text,
            apply_url=job.apply_url,
        )
        for match, job, company in rows
    ]

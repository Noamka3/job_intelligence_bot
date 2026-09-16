from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.schemas.match import MatchRead
from app.services.candidate.profile_service import get_active_profile
from app.services.matching.queries import (
    MatchFilters,
    MatchRow,
    get_match_for_job,
    list_top_matches,
)

router = APIRouter(prefix="/matches", tags=["matches"])


def _to_read(row: MatchRow) -> MatchRead:
    match, job, company, source = row.match, row.job, row.company, row.source
    return MatchRead(
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
        company_id=company.id,
        company_name=company.name,
        location_text=job.location_text,
        country=job.country,
        source_type=source.source_type,
        source_url=job.source_url,
        apply_url=job.apply_url,
        source_published_at=job.source_published_at,
        first_seen_at=job.first_seen_at,
        last_feedback=row.last_feedback,
    )


@router.get("/top", response_model=list[MatchRead])
def top_matches(
    target_role_id: int | None = None,
    min_score: float = Query(0.0, ge=0.0, le=100.0),
    israel_only: bool = True,
    days: int | None = Query(
        None, ge=1, le=365, description="Only jobs discovered in the last N days"
    ),
    q: str | None = Query(None, max_length=100, description="Title or company contains"),
    hide_dismissed: bool = Query(
        True, description="Hide jobs whose latest feedback was not relevant / too senior / ..."
    ),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
) -> list[MatchRead]:
    candidate = get_active_profile(db)
    if candidate is None:
        return []
    rows = list_top_matches(
        db,
        candidate.id,
        MatchFilters(
            min_score=min_score,
            target_role_id=target_role_id,
            israel_only=israel_only,
            discovered_within_days=days,
            query=q,
            hide_dismissed=hide_dismissed,
            limit=limit,
            offset=offset,
        ),
    )
    return [_to_read(row) for row in rows]


@router.get("/job/{job_id}", response_model=MatchRead)
def match_for_job(job_id: int, db: Session = Depends(get_db)) -> MatchRead:
    """The active profile's best match row for one job - the score
    breakdown a job page shows."""
    candidate = get_active_profile(db)
    if candidate is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No active candidate profile")
    row = get_match_for_job(db, candidate.id, job_id)
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No match for this job yet")
    return _to_read(row)

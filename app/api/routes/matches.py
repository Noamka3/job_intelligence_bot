from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.schemas.match import MatchRead
from app.services.candidate.profile_service import get_active_profile
from app.services.embeddings import get_embedding_provider
from app.services.embeddings.base import EmbeddingProvider
from app.services.jobs.location import REGIONS
from app.services.matching.queries import (
    MatchFilters,
    MatchRow,
    MatchSort,
    SeniorityFilter,
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
        region=job.region,
        source_type=source.source_type,
        source_url=job.source_url,
        apply_url=job.apply_url,
        source_published_at=job.source_published_at,
        first_seen_at=job.first_seen_at,
        last_feedback=row.last_feedback,
        seniority_fit=row.seniority_fit,
        job_seniority=job.seniority,
        experience_min_years=job.experience_min_years,
    )


@router.get("/top", response_model=list[MatchRead])
def top_matches(
    target_role_id: int | None = None,
    min_score: float = Query(0.0, ge=0.0, le=100.0),
    israel_only: bool = True,
    days: int | None = Query(
        None,
        ge=0,
        le=365,
        description="Only jobs found or published since local midnight N days ago (0 = today)",
    ),
    q: str | None = Query(
        None,
        max_length=200,
        description="Hybrid search: exact title/company hits first, then jobs whose "
        "embedding is close to the query's meaning",
    ),
    region: str | None = Query(None, description="Israeli region key, see /matches/regions"),
    sort: MatchSort = Query("recent", description="recent = newest posting first"),
    hide_dismissed: bool = Query(
        True, description="Hide jobs whose latest feedback was not relevant / too senior / ..."
    ),
    seniority: SeniorityFilter = Query(
        "all",
        description="fit = only postings that read as entry-level; not_experienced = also "
        "those that say nothing about experience; student = only student/internship "
        "positions, which the other two leave out; all = everything",
    ),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
    embedding_provider: EmbeddingProvider = Depends(get_embedding_provider),
) -> list[MatchRead]:
    candidate = get_active_profile(db)
    if candidate is None:
        return []
    if region is not None and region not in REGIONS:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, f"Unknown region {region!r}")
    query_text = q.strip() if q else None
    rows = list_top_matches(
        db,
        candidate.id,
        MatchFilters(
            min_score=min_score,
            target_role_id=target_role_id,
            israel_only=israel_only,
            discovered_within_days=days,
            query=query_text,
            query_embedding=embedding_provider.embed_one(query_text) if query_text else None,
            region=region,
            hide_dismissed=hide_dismissed,
            seniority=seniority,
            sort=sort,
            limit=limit,
            offset=offset,
        ),
    )
    return [_to_read(row) for row in rows]


@router.get("/regions", response_model=dict[str, str])
def regions() -> dict[str, str]:
    """Region keys accepted by `region`, with their display labels."""
    return dict(REGIONS)


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

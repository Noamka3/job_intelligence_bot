from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi import status as http_status
from sqlalchemy import not_, select
from sqlalchemy.orm import Session

from app.core.timezone import interpret_naive_as_default_timezone
from app.db.session import get_db
from app.models.enums import JobStatus
from app.models.job_feedback import JobFeedback
from app.models.job_posting import JobPosting
from app.schemas.feedback import JobFeedbackCreate, JobFeedbackRead
from app.schemas.job import JobDetailRead, JobRead
from app.services.jobs.location import NON_ISRAEL_LOCATION_SQL_REGEX

router = APIRouter(prefix="/jobs", tags=["jobs"])

_SINCE_DESCRIPTION = (
    "ISO-8601 timestamp; without an offset it's read in DEFAULT_TIMEZONE (Asia/Jerusalem)."
)


@router.get("", response_model=list[JobRead])
def list_jobs(
    company_id: int | None = None,
    career_source_id: int | None = None,
    status: JobStatus | None = None,
    title: str | None = None,
    location: str | None = None,
    israel_only: bool = True,
    discovered_since: datetime | None = Query(None, description=_SINCE_DESCRIPTION),
    published_since: datetime | None = Query(None, description=_SINCE_DESCRIPTION),
    limit: int = Query(100, ge=1, le=500),
    db: Session = Depends(get_db),
) -> list[JobRead]:
    query = select(JobPosting).order_by(JobPosting.first_seen_at.desc()).limit(limit)
    if company_id is not None:
        query = query.where(JobPosting.company_id == company_id)
    if career_source_id is not None:
        query = query.where(JobPosting.career_source_id == career_source_id)
    if status is not None:
        query = query.where(JobPosting.status == status)
    if title:
        query = query.where(JobPosting.normalized_title.ilike(f"%{title.lower()}%"))
    if location:
        query = query.where(JobPosting.normalized_location.ilike(f"%{location.lower()}%"))
    if israel_only:
        # Same decision ingestion/matching make in Python (see
        # app/services/jobs/location.py): keep anything classified as
        # Israel, keep anything unrecognized rather than risk hiding a
        # real Israeli listing, and drop only an explicit non-Israel
        # signal - matched on word boundaries, so "USA" can't hide
        # "JerUSAlem".
        query = query.where(
            (JobPosting.country == "Israel")
            | JobPosting.location_text.is_(None)
            | not_(JobPosting.location_text.regexp_match(NON_ISRAEL_LOCATION_SQL_REGEX, flags="i"))
        )
    if discovered_since is not None:
        query = query.where(
            JobPosting.first_seen_at >= interpret_naive_as_default_timezone(discovered_since)
        )
    if published_since is not None:
        query = query.where(
            JobPosting.source_published_at >= interpret_naive_as_default_timezone(published_since)
        )

    jobs = db.execute(query).scalars()
    return [JobRead.model_validate(job) for job in jobs]


@router.get("/{job_id}", response_model=JobDetailRead)
def get_job(job_id: int, db: Session = Depends(get_db)) -> JobDetailRead:
    job = db.get(JobPosting, job_id)
    if job is None:
        raise HTTPException(http_status.HTTP_404_NOT_FOUND, "Job not found")
    return JobDetailRead.model_validate(job)


@router.post(
    "/{job_id}/feedback", response_model=JobFeedbackRead, status_code=http_status.HTTP_201_CREATED
)
def submit_feedback(
    job_id: int, data: JobFeedbackCreate, db: Session = Depends(get_db)
) -> JobFeedbackRead:
    job = db.get(JobPosting, job_id)
    if job is None:
        raise HTTPException(http_status.HTTP_404_NOT_FOUND, "Job not found")

    feedback = JobFeedback(job_id=job_id, action=data.action)
    db.add(feedback)
    db.commit()
    db.refresh(feedback)
    return JobFeedbackRead.model_validate(feedback)

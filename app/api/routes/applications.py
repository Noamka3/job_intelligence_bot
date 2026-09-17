from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.models.job_posting import JobPosting
from app.schemas.application import ApplicationRead, ApplicationUpdate
from app.services.applications import (
    ApplicationNotFoundError,
    ApplicationRow,
    ensure_application,
    list_applications,
    update_application,
)

router = APIRouter(prefix="/applications", tags=["applications"])


def _to_read(row: ApplicationRow) -> ApplicationRead:
    return ApplicationRead(
        id=row.application.id,
        job_id=row.job.id,
        status=row.application.status,
        applied_at=row.application.applied_at,
        updated_at=row.application.updated_at,
        notes=row.application.notes,
        job_title=row.job.title,
        company_name=row.company.name,
        location_text=row.job.location_text,
        apply_url=row.job.apply_url,
        source_url=row.job.source_url,
        job_status=row.job.status.value,
    )


@router.get("", response_model=list[ApplicationRead])
def get_applications(db: Session = Depends(get_db)) -> list[ApplicationRead]:
    return [_to_read(row) for row in list_applications(db)]


@router.post("/{job_id}", response_model=ApplicationRead, status_code=status.HTTP_201_CREATED)
def open_application(job_id: int, db: Session = Depends(get_db)) -> ApplicationRead:
    """Marks a job as applied to (idempotent) - the same thing "applied"
    feedback does."""
    if db.get(JobPosting, job_id) is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Job not found")
    ensure_application(db, job_id)
    db.commit()
    row = next(r for r in list_applications(db) if r.job.id == job_id)
    return _to_read(row)


@router.patch("/{job_id}", response_model=ApplicationRead)
def patch_application(
    job_id: int, data: ApplicationUpdate, db: Session = Depends(get_db)
) -> ApplicationRead:
    fields = data.model_dump(exclude_unset=True)
    try:
        update_application(
            db,
            job_id,
            status=fields.get("status"),
            notes=fields.get("notes"),
            clear_notes="notes" in fields and fields["notes"] is None,
        )
    except ApplicationNotFoundError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc
    row = next(r for r in list_applications(db) if r.job.id == job_id)
    return _to_read(row)

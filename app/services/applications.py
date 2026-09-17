"""Feedback + the applications it opens.

"applied" is the one reaction that means something happened in the real
world, so it also opens an Application row the user then moves through
the process (screening, interview, ...). Everything else stays a plain
JobFeedback entry.
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.application import Application
from app.models.company import Company
from app.models.enums import ApplicationStatus, JobFeedbackAction
from app.models.job_feedback import JobFeedback
from app.models.job_posting import JobPosting


class ApplicationNotFoundError(LookupError):
    def __init__(self, job_id: int) -> None:
        super().__init__(f"No application for job {job_id}")


@dataclass(frozen=True)
class ApplicationRow:
    application: Application
    job: JobPosting
    company: Company


def record_feedback(db: Session, job_id: int, action: JobFeedbackAction) -> JobFeedback:
    feedback = JobFeedback(job_id=job_id, action=action)
    db.add(feedback)
    if action is JobFeedbackAction.APPLIED:
        ensure_application(db, job_id)
    db.commit()
    db.refresh(feedback)
    return feedback


def ensure_application(db: Session, job_id: int) -> Application:
    existing = db.execute(
        select(Application).where(Application.job_id == job_id)
    ).scalar_one_or_none()
    if existing is not None:
        return existing
    application = Application(job_id=job_id, status=ApplicationStatus.APPLIED)
    db.add(application)
    db.flush()
    return application


def list_applications(db: Session) -> list[ApplicationRow]:
    rows = db.execute(
        select(Application, JobPosting, Company)
        .join(JobPosting, Application.job_id == JobPosting.id)
        .join(Company, JobPosting.company_id == Company.id)
        .order_by(Application.updated_at.desc())
    ).all()
    return [ApplicationRow(application=a, job=j, company=c) for a, j, c in rows]


def update_application(
    db: Session,
    job_id: int,
    *,
    status: ApplicationStatus | None = None,
    notes: str | None = None,
    clear_notes: bool = False,
) -> Application:
    application = db.execute(
        select(Application).where(Application.job_id == job_id)
    ).scalar_one_or_none()
    if application is None:
        raise ApplicationNotFoundError(job_id)
    if status is not None:
        application.status = status
    if clear_notes:
        application.notes = None
    elif notes is not None:
        application.notes = notes.strip() or None
    db.commit()
    db.refresh(application)
    return application

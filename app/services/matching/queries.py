"""Read-side queries over JobMatch that both the JSON API and the web
dashboard use, so the two can't drift on what "top matches" means
(active jobs only, Israel-only by default, one row per job)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy import Select, func, or_, select
from sqlalchemy.orm import Session

from app.core.timezone import utc_now
from app.models.career_source import CareerSource
from app.models.company import Company
from app.models.enums import JobFeedbackAction, JobStatus
from app.models.job_feedback import JobFeedback
from app.models.job_match import JobMatch
from app.models.job_posting import JobPosting
from app.services.jobs.israel_filter import israel_only_clause


@dataclass(frozen=True)
class MatchRow:
    match: JobMatch
    job: JobPosting
    company: Company
    source: CareerSource
    last_feedback: JobFeedbackAction | None


@dataclass(frozen=True)
class MatchFilters:
    min_score: float = 0.0
    target_role_id: int | None = None
    israel_only: bool = True
    discovered_within_days: int | None = None
    query: str | None = None
    hide_dismissed: bool = True
    limit: int = 50
    offset: int = 0


_DISMISSING_FEEDBACK = (
    JobFeedbackAction.NOT_RELEVANT,
    JobFeedbackAction.TOO_SENIOR,
    JobFeedbackAction.WRONG_FIELD,
    JobFeedbackAction.WRONG_LOCATION,
    JobFeedbackAction.REJECTED,
)


def _latest_feedback_subquery() -> Select[tuple[int, JobFeedbackAction]]:
    latest = (
        select(JobFeedback.job_id, func.max(JobFeedback.id).label("feedback_id"))
        .group_by(JobFeedback.job_id)
        .subquery()
    )
    return select(JobFeedback.job_id, JobFeedback.action).join(
        latest, JobFeedback.id == latest.c.feedback_id
    )


def list_top_matches(
    db: Session, candidate_profile_id: int, filters: MatchFilters
) -> list[MatchRow]:
    latest_feedback = _latest_feedback_subquery().subquery()

    query = (
        select(JobMatch, JobPosting, Company, CareerSource, latest_feedback.c.action)
        .join(JobPosting, JobMatch.job_id == JobPosting.id)
        .join(Company, JobPosting.company_id == Company.id)
        .join(CareerSource, JobPosting.career_source_id == CareerSource.id)
        .outerjoin(latest_feedback, latest_feedback.c.job_id == JobPosting.id)
        .where(
            JobMatch.candidate_profile_id == candidate_profile_id,
            JobMatch.final_score >= filters.min_score,
            # JobMatch rows are kept when a job closes (history); a great
            # match for a job that's gone is not something to apply to.
            JobPosting.status == JobStatus.ACTIVE,
        )
        .order_by(JobMatch.final_score.desc(), JobPosting.first_seen_at.desc())
        .limit(filters.limit)
        .offset(filters.offset)
    )
    if filters.israel_only:
        query = query.where(israel_only_clause())
    if filters.target_role_id is not None:
        query = query.where(JobMatch.target_role_id == filters.target_role_id)
    if filters.discovered_within_days is not None:
        since: datetime = utc_now() - timedelta(days=filters.discovered_within_days)
        query = query.where(JobPosting.first_seen_at >= since)
    if filters.query:
        needle = f"%{filters.query.strip().lower()}%"
        query = query.where(
            or_(JobPosting.normalized_title.ilike(needle), Company.normalized_name.ilike(needle))
        )
    if filters.hide_dismissed:
        query = query.where(
            or_(
                latest_feedback.c.action.is_(None),
                latest_feedback.c.action.not_in(_DISMISSING_FEEDBACK),
            )
        )

    return [
        MatchRow(match=match, job=job, company=company, source=source, last_feedback=action)
        for match, job, company, source, action in db.execute(query).all()
    ]


def get_match_for_job(
    db: Session, candidate_profile_id: int, job_id: int
) -> MatchRow | None:
    latest_feedback = _latest_feedback_subquery().subquery()
    row = db.execute(
        select(JobMatch, JobPosting, Company, CareerSource, latest_feedback.c.action)
        .join(JobPosting, JobMatch.job_id == JobPosting.id)
        .join(Company, JobPosting.company_id == Company.id)
        .join(CareerSource, JobPosting.career_source_id == CareerSource.id)
        .outerjoin(latest_feedback, latest_feedback.c.job_id == JobPosting.id)
        .where(JobMatch.candidate_profile_id == candidate_profile_id, JobMatch.job_id == job_id)
        .order_by(JobMatch.final_score.desc())
        .limit(1)
    ).first()
    if row is None:
        return None
    match, job, company, source, action = row
    return MatchRow(match=match, job=job, company=company, source=source, last_feedback=action)

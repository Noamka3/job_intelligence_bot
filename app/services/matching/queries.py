"""Read-side queries over JobMatch that both the JSON API and the web
dashboard use, so the two can't drift on what "top matches" means
(active jobs only, Israel-only by default, one row per job)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Literal
from zoneinfo import ZoneInfo

from sqlalchemy import Select, case, func, or_, select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.timezone import utc_now
from app.models.career_source import CareerSource
from app.models.company import Company
from app.models.enums import JobFeedbackAction, JobStatus, SeniorityLevel
from app.models.job_feedback import JobFeedback
from app.models.job_match import JobMatch
from app.models.job_posting import JobPosting
from app.models.target_role import TargetRole
from app.services.jobs.israel_filter import israel_only_clause

# What a posting's stored seniority read (JobPosting.seniority and
# experience_min_years, set at ingest) means for *this* target role:
#   experienced - a senior-level title, or stated years above the role's
#                 max_expected_years
#   fit         - stated years within it, or else an entry-level title
#   unknown     - the posting says nothing readable about experience
# Stated years outrank the title's level: "2-3 years mandatory" under a
# junior-looking title is a requirement, and a candidate with none
# doesn't meet it.
SeniorityFit = Literal["fit", "unknown", "experienced"]
SeniorityFilter = Literal["all", "fit", "not_experienced"]

_ENTRY_LEVELS = (SeniorityLevel.JUNIOR, SeniorityLevel.INTERN)
_SENIOR_LEVELS = (
    SeniorityLevel.SENIOR,
    SeniorityLevel.STAFF,
    SeniorityLevel.PRINCIPAL,
    SeniorityLevel.LEAD,
    SeniorityLevel.MANAGER,
    SeniorityLevel.DIRECTOR,
)
_DEFAULT_MAX_EXPECTED_YEARS = 2


def seniority_fit_expression() -> Any:
    """SQL for SeniorityFit; needs JobPosting and TargetRole in the FROM."""
    ceiling = func.coalesce(TargetRole.max_expected_years, _DEFAULT_MAX_EXPECTED_YEARS)
    return case(
        (JobPosting.seniority.in_(_SENIOR_LEVELS), "experienced"),
        (JobPosting.experience_min_years > ceiling, "experienced"),
        (JobPosting.experience_min_years.is_not(None), "fit"),
        (JobPosting.seniority.in_(_ENTRY_LEVELS), "fit"),
        else_="unknown",
    )


@dataclass(frozen=True)
class MatchRow:
    match: JobMatch
    job: JobPosting
    company: Company
    source: CareerSource
    last_feedback: JobFeedbackAction | None
    seniority_fit: SeniorityFit


MatchSort = Literal["recent", "score"]

# Cosine *distance* (1 - similarity) above which a job is simply not about
# the query, for the local multilingual MiniLM model: related postings sit
# around 0.3-0.6, unrelated ones 0.8+.
SEMANTIC_MAX_DISTANCE = 0.75


def window_start(days_back: int) -> datetime:
    """Local midnight `days_back` days ago (0 = today). What people mean
    by "3 days": a job the card labels "found 3 days ago" is inside it,
    which a plain 72-hour window missed by a few hours."""
    local_now = utc_now().astimezone(ZoneInfo(get_settings().default_timezone))
    midnight = local_now.replace(hour=0, minute=0, second=0, microsecond=0)
    return (midnight - timedelta(days=days_back)).astimezone(UTC)


@dataclass(frozen=True)
class MatchFilters:
    min_score: float = 0.0
    target_role_id: int | None = None
    israel_only: bool = True
    discovered_within_days: int | None = None
    query: str | None = None
    # When set alongside `query`, the search is hybrid: text hits on
    # title/company first, then jobs whose embedding is close to this one
    # (ranked by cosine distance). Takes precedence over `sort`.
    query_embedding: list[float] | None = None
    # A JobPosting.region key (see app/services/jobs/location.py REGIONS).
    region: str | None = None
    hide_dismissed: bool = True
    # "fit": only postings that read as entry-level; "not_experienced":
    # also the ones that say nothing about experience.
    seniority: SeniorityFilter = "all"
    sort: MatchSort = "recent"
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
    query = (
        _top_matches_query(candidate_profile_id, filters)
        .limit(filters.limit)
        .offset(filters.offset)
    )
    return [
        MatchRow(
            match=match,
            job=job,
            company=company,
            source=source,
            last_feedback=action,
            seniority_fit=fit_value,
        )
        for match, job, company, source, action, fit_value in db.execute(query).all()
    ]


def count_top_matches(db: Session, candidate_profile_id: int, filters: MatchFilters) -> int:
    """How many matches the same filters select, unpaginated."""
    ids = _top_matches_query(candidate_profile_id, filters).with_only_columns(JobMatch.id)
    return db.scalar(select(func.count()).select_from(ids.order_by(None).subquery())) or 0


def _top_matches_query(candidate_profile_id: int, filters: MatchFilters) -> Select[Any]:
    latest_feedback = _latest_feedback_subquery().subquery()

    # "Newest first" means the posting date when the source reports one,
    # else when the bot found it - a bootstrap crawl finds hundreds of
    # jobs in one hour, and their real ages differ by months.
    posted_at = func.coalesce(JobPosting.source_published_at, JobPosting.first_seen_at)
    text_hit = None
    if filters.query:
        needle = f"%{filters.query.strip().lower()}%"
        text_hit = or_(
            JobPosting.normalized_title.ilike(needle), Company.normalized_name.ilike(needle)
        )
    ordering: tuple[Any, ...]
    if filters.query_embedding is not None and text_hit is not None:
        # Hybrid: an exact title/company hit outranks a merely similar
        # job, then closeness of meaning, then the match score.
        distance = JobPosting.embedding.cosine_distance(filters.query_embedding)
        ordering = (case((text_hit, 0), else_=1), distance, JobMatch.final_score.desc())
    elif filters.sort == "score":
        ordering = (JobMatch.final_score.desc(), posted_at.desc())
    else:
        ordering = (posted_at.desc(), JobMatch.final_score.desc())

    fit = seniority_fit_expression()
    query = (
        select(JobMatch, JobPosting, Company, CareerSource, latest_feedback.c.action, fit)
        .join(JobPosting, JobMatch.job_id == JobPosting.id)
        .join(Company, JobPosting.company_id == Company.id)
        .join(CareerSource, JobPosting.career_source_id == CareerSource.id)
        .join(TargetRole, JobMatch.target_role_id == TargetRole.id)
        .outerjoin(latest_feedback, latest_feedback.c.job_id == JobPosting.id)
        .where(
            JobMatch.candidate_profile_id == candidate_profile_id,
            JobMatch.final_score >= filters.min_score,
            # JobMatch rows are kept when a job closes (history); a great
            # match for a job that's gone is not something to apply to.
            JobPosting.status == JobStatus.ACTIVE,
        )
        .order_by(*ordering)
    )
    if filters.israel_only:
        query = query.where(israel_only_clause())
    if filters.target_role_id is not None:
        query = query.where(JobMatch.target_role_id == filters.target_role_id)
    if filters.discovered_within_days is not None:
        since = window_start(filters.discovered_within_days)
        # Recent means the bot found it recently *or* the source says it
        # was (re)published recently - the card shows the publish date when
        # there is one, so a job "published yesterday" must not vanish from
        # the 3-day view because the bot first saw it a week ago.
        query = query.where(
            (JobPosting.first_seen_at >= since) | (JobPosting.source_published_at >= since)
        )
    if filters.region is not None:
        query = query.where(JobPosting.region == filters.region)
    if filters.seniority == "fit":
        query = query.where(fit == "fit")
    elif filters.seniority == "not_experienced":
        query = query.where(fit != "experienced")
    if filters.query_embedding is not None and text_hit is not None:
        near = JobPosting.embedding.is_not(None) & (
            JobPosting.embedding.cosine_distance(filters.query_embedding) <= SEMANTIC_MAX_DISTANCE
        )
        query = query.where(or_(text_hit, near))
    elif text_hit is not None:
        query = query.where(text_hit)
    if filters.hide_dismissed:
        query = query.where(
            or_(
                latest_feedback.c.action.is_(None),
                latest_feedback.c.action.not_in(_DISMISSING_FEEDBACK),
            )
        )
    return query


def get_match_for_job(db: Session, candidate_profile_id: int, job_id: int) -> MatchRow | None:
    latest_feedback = _latest_feedback_subquery().subquery()
    row = db.execute(
        select(
            JobMatch,
            JobPosting,
            Company,
            CareerSource,
            latest_feedback.c.action,
            seniority_fit_expression(),
        )
        .join(JobPosting, JobMatch.job_id == JobPosting.id)
        .join(Company, JobPosting.company_id == Company.id)
        .join(CareerSource, JobPosting.career_source_id == CareerSource.id)
        .join(TargetRole, JobMatch.target_role_id == TargetRole.id)
        .outerjoin(latest_feedback, latest_feedback.c.job_id == JobPosting.id)
        .where(JobMatch.candidate_profile_id == candidate_profile_id, JobMatch.job_id == job_id)
        .order_by(JobMatch.final_score.desc())
        .limit(1)
    ).first()
    if row is None:
        return None
    match, job, company, source, action, fit_value = row
    return MatchRow(
        match=match,
        job=job,
        company=company,
        source=source,
        last_feedback=action,
        seniority_fit=fit_value,
    )

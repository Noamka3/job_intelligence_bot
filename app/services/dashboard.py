"""Aggregates for the dashboard's status page - what the scheduler is
doing and how much data there is, from the tables that already record it
(CrawlRun, CareerSource, JobPosting, JobMatch)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.timezone import utc_now
from app.models.career_source import CareerSource
from app.models.company import Company
from app.models.crawl_run import CrawlRun
from app.models.enums import CareerSourceType, CrawlRunStatus, JobStatus
from app.models.job_match import JobMatch
from app.models.job_posting import JobPosting
from app.services.candidate.profile_service import get_active_profile
from app.services.jobs.israel_filter import israel_only_clause
from app.services.matching.queries import MatchFilters, count_top_matches, window_start
from app.services.scheduler_state import crawl_queue_depth, last_dispatch_at, next_dispatch_at

# What the matches page's live line calls "over 60% today": today's
# Israeli (or unplaced) postings that don't demand experience, scored
# 60 or more - the same query the page's own 60%+ view runs.
_TODAYS_DEFAULT_VIEW = MatchFilters(
    min_score=60, seniority="not_experienced", discovered_within_days=0
)


@dataclass(frozen=True)
class SourceTypeStat:
    source_type: CareerSourceType
    sources: int
    active_jobs: int
    active_israel_jobs: int  # country classified as Israel - not "unknown"


@dataclass(frozen=True)
class RecentRun:
    company_name: str
    source_type: CareerSourceType
    status: CrawlRunStatus
    started_at: datetime
    jobs_seen: int
    jobs_created: int
    jobs_updated: int
    jobs_closed: int
    jobs_failed: int
    error_type: str | None


@dataclass(frozen=True)
class FailingSource:
    company_name: str
    source_type: CareerSourceType
    source_url: str
    consecutive_failures: int
    last_error: str | None


@dataclass(frozen=True)
class DashboardStats:
    companies_enabled: int
    sources_enabled: int
    active_jobs: int
    # Israel-only as the matches list applies it (classified Israel or
    # location unknown), then the two halves of it separately - the
    # "unknown" share is the honest measure of how much the generic
    # adapter still can't place.
    active_israel_or_unknown_jobs: int
    active_israel_jobs: int
    active_unknown_location_jobs: int
    matches: int
    jobs_discovered_24h: int
    # Since local midnight: everything the crawler stored, and how much of
    # it the matches page's default view shows - the two numbers the live
    # line on that page reports, so "nothing new" and "nothing relevant"
    # stay distinguishable.
    jobs_found_today: int
    relevant_today: int
    last_crawl_at: datetime | None
    # The scheduler's own heartbeat (Redis), separate from crawl rows: a
    # tick with nothing due still proves Beat is alive.
    last_dispatch_at: datetime | None
    next_dispatch_at: datetime | None
    poll_interval_minutes: int
    crawl_queue_depth: int | None  # crawls waiting for a worker; None if Redis is unreachable
    runs_last_hour: int
    failed_runs_last_hour: int
    by_source_type: list[SourceTypeStat]
    recent_runs: list[RecentRun]
    failing_sources: list[FailingSource]


def load_dashboard_stats(db: Session) -> DashboardStats:
    now = utc_now()
    active = JobPosting.status == JobStatus.ACTIVE

    companies_enabled = db.scalar(select(func.count()).where(Company.enabled.is_(True))) or 0
    sources_enabled = db.scalar(select(func.count()).where(CareerSource.enabled.is_(True))) or 0
    israel = JobPosting.country == "Israel"
    active_jobs = db.scalar(select(func.count()).where(active)) or 0
    active_israel_or_unknown = (
        db.scalar(select(func.count()).where(active, israel_only_clause())) or 0
    )
    active_israel = db.scalar(select(func.count()).where(active, israel)) or 0
    active_unknown = (
        db.scalar(
            select(func.count()).where(
                active, JobPosting.country.is_(None), JobPosting.location_text.is_(None)
            )
        )
        or 0
    )
    matches = db.scalar(select(func.count()).select_from(JobMatch)) or 0
    discovered_24h = (
        db.scalar(select(func.count()).where(JobPosting.first_seen_at >= now - timedelta(hours=24)))
        or 0
    )
    today = window_start(0)
    found_today = db.scalar(select(func.count()).where(JobPosting.first_seen_at >= today)) or 0
    profile = get_active_profile(db)
    relevant_today = count_top_matches(db, profile.id, _TODAYS_DEFAULT_VIEW) if profile else 0
    last_crawl_at = db.scalar(select(func.max(CrawlRun.started_at)))
    hour_ago = now - timedelta(hours=1)
    runs_last_hour = db.scalar(select(func.count()).where(CrawlRun.started_at >= hour_ago)) or 0
    failed_last_hour = (
        db.scalar(
            select(func.count()).where(
                CrawlRun.started_at >= hour_ago, CrawlRun.status == CrawlRunStatus.FAILED
            )
        )
        or 0
    )

    by_type_rows = db.execute(
        select(
            CareerSource.source_type,
            func.count(func.distinct(CareerSource.id)),
            func.count(JobPosting.id).filter(active),
            func.count(JobPosting.id).filter(active, israel),
        )
        .outerjoin(JobPosting, JobPosting.career_source_id == CareerSource.id)
        .where(CareerSource.enabled.is_(True))
        .group_by(CareerSource.source_type)
        .order_by(func.count(JobPosting.id).filter(active).desc())
    ).all()

    recent_rows = db.execute(
        select(CrawlRun, CareerSource, Company)
        .join(CareerSource, CrawlRun.career_source_id == CareerSource.id)
        .join(Company, CareerSource.company_id == Company.id)
        .order_by(CrawlRun.started_at.desc())
        .limit(25)
    ).all()

    latest_error = (
        select(CrawlRun.career_source_id, func.max(CrawlRun.id).label("run_id"))
        .where(CrawlRun.status == CrawlRunStatus.FAILED)
        .group_by(CrawlRun.career_source_id)
        .subquery()
    )
    failing_rows = db.execute(
        select(CareerSource, Company, CrawlRun.error_type)
        .join(Company, CareerSource.company_id == Company.id)
        .outerjoin(latest_error, latest_error.c.career_source_id == CareerSource.id)
        .outerjoin(CrawlRun, CrawlRun.id == latest_error.c.run_id)
        .where(CareerSource.enabled.is_(True), CareerSource.consecutive_failures >= 2)
        .order_by(CareerSource.consecutive_failures.desc(), Company.name)
        .limit(30)
    ).all()

    return DashboardStats(
        companies_enabled=companies_enabled,
        sources_enabled=sources_enabled,
        active_jobs=active_jobs,
        active_israel_or_unknown_jobs=active_israel_or_unknown,
        active_israel_jobs=active_israel,
        active_unknown_location_jobs=active_unknown,
        matches=matches,
        jobs_discovered_24h=discovered_24h,
        jobs_found_today=found_today,
        relevant_today=relevant_today,
        last_crawl_at=last_crawl_at,
        last_dispatch_at=last_dispatch_at(),
        next_dispatch_at=next_dispatch_at(),
        poll_interval_minutes=get_settings().default_poll_minutes,
        crawl_queue_depth=crawl_queue_depth(),
        runs_last_hour=runs_last_hour,
        failed_runs_last_hour=failed_last_hour,
        by_source_type=[
            SourceTypeStat(source_type=t, sources=s, active_jobs=a, active_israel_jobs=i)
            for t, s, a, i in by_type_rows
        ],
        recent_runs=[
            RecentRun(
                company_name=company.name,
                source_type=source.source_type,
                status=run.status,
                started_at=run.started_at,
                jobs_seen=run.jobs_seen,
                jobs_created=run.jobs_created,
                jobs_updated=run.jobs_updated,
                jobs_closed=run.jobs_closed,
                jobs_failed=run.jobs_failed,
                error_type=run.error_type,
            )
            for run, source, company in recent_rows
        ],
        failing_sources=[
            FailingSource(
                company_name=company.name,
                source_type=source.source_type,
                source_url=source.source_url,
                consecutive_failures=source.consecutive_failures,
                last_error=error_type,
            )
            for source, company, error_type in failing_rows
        ],
    )

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict

from app.models.enums import CareerSourceType, CrawlRunStatus


class SourceTypeStatRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    source_type: CareerSourceType
    sources: int
    active_jobs: int
    active_israel_jobs: int


class RecentRunRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

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


class FailingSourceRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    company_name: str
    source_type: CareerSourceType
    source_url: str
    consecutive_failures: int
    last_error: str | None


class DashboardStatsRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    companies_enabled: int
    sources_enabled: int
    active_jobs: int
    active_israel_or_unknown_jobs: int
    active_israel_jobs: int
    active_unknown_location_jobs: int
    matches: int
    jobs_discovered_24h: int
    jobs_found_today: int
    relevant_today: int
    last_crawl_at: datetime | None
    last_dispatch_at: datetime | None
    next_dispatch_at: datetime | None
    poll_interval_minutes: int
    crawl_queue_depth: int | None
    runs_last_hour: int
    failed_runs_last_hour: int
    by_source_type: list[SourceTypeStatRead]
    recent_runs: list[RecentRunRead]
    failing_sources: list[FailingSourceRead]

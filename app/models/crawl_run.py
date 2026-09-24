from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, Enum, ForeignKey, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.models.enums import CrawlRunStatus


class CrawlRun(Base):
    """One observability record per source-check attempt.

    Every crawl of a CareerSource writes one of these, success or failure,
    so the whole system is auditable without grepping application logs.
    """

    __tablename__ = "crawl_runs"

    id: Mapped[int] = mapped_column(primary_key=True)
    career_source_id: Mapped[int] = mapped_column(
        ForeignKey("career_sources.id", ondelete="CASCADE"), nullable=False, index=True
    )
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    status: Mapped[CrawlRunStatus] = mapped_column(
        Enum(CrawlRunStatus, name="crawl_run_status"),
        nullable=False,
        default=CrawlRunStatus.RUNNING,
        index=True,
    )
    http_status: Mapped[int | None] = mapped_column(Integer)

    jobs_seen: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    jobs_created: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    jobs_updated: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    jobs_closed: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    # Jobs listed by the source whose detail fetch/embed failed and were
    # skipped for this run (the run itself still succeeds unless all did).
    jobs_failed: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")

    error_type: Mapped[str | None] = mapped_column(String(255))
    error_message: Mapped[str | None] = mapped_column(Text)

    def __repr__(self) -> str:
        return (
            f"CrawlRun(id={self.id!r}, source_id={self.career_source_id!r}, status={self.status!r})"
        )

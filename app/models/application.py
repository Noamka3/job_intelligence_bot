from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, Enum, ForeignKey, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.models.enums import ApplicationStatus
from app.models.mixins import TimestampMixin


class Application(Base, TimestampMixin):
    """A job actually applied to, and where the process stands.

    Distinct from JobFeedback, which is a stream of quick reactions
    ("interested", "too senior"): an application is one row per job that
    kept updated as the process moves. Created automatically
    the first time a job gets "applied" feedback; never deleted - a
    rejection or withdrawal is a status, so the history stays.
    """

    __tablename__ = "applications"

    id: Mapped[int] = mapped_column(primary_key=True)
    job_id: Mapped[int] = mapped_column(
        ForeignKey("job_postings.id", ondelete="CASCADE"), nullable=False, unique=True
    )
    status: Mapped[ApplicationStatus] = mapped_column(
        Enum(ApplicationStatus, name="application_status"),
        nullable=False,
        default=ApplicationStatus.APPLIED,
        index=True,
    )
    applied_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    notes: Mapped[str | None] = mapped_column(Text)

    def __repr__(self) -> str:
        return f"Application(job_id={self.job_id!r}, status={self.status!r})"

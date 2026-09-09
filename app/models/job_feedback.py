from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, Enum, ForeignKey, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.models.enums import JobFeedbackAction


class JobFeedback(Base):
    """A user decision about a job (interested / applied / not relevant /
    ...). Collected as-is for now; a later phase may use this to tune
    matching weights, but no learning happens here yet - see spec §29.
    """

    __tablename__ = "job_feedback"

    id: Mapped[int] = mapped_column(primary_key=True)
    job_id: Mapped[int] = mapped_column(
        ForeignKey("job_postings.id", ondelete="CASCADE"), nullable=False, index=True
    )
    action: Mapped[JobFeedbackAction] = mapped_column(
        Enum(JobFeedbackAction, name="job_feedback_action"), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    def __repr__(self) -> str:
        return f"JobFeedback(job_id={self.job_id!r}, action={self.action!r})"

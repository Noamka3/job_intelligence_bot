from __future__ import annotations

from typing import Any

from sqlalchemy import Float, ForeignKey, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.models.mixins import TimestampMixin


class JobMatch(Base, TimestampMixin):
    """The hybrid-scoring result for one (candidate profile, target role,
    job) triple. Recomputed in place (not appended) when any input changes,
    enforced by the unique constraint below.
    """

    __tablename__ = "job_matches"
    __table_args__ = (
        UniqueConstraint(
            "candidate_profile_id",
            "target_role_id",
            "job_id",
            name="uq_job_matches_profile_role_job",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    candidate_profile_id: Mapped[int] = mapped_column(
        ForeignKey("candidate_profiles.id", ondelete="CASCADE"), nullable=False, index=True
    )
    target_role_id: Mapped[int] = mapped_column(
        ForeignKey("target_roles.id", ondelete="CASCADE"), nullable=False, index=True
    )
    job_id: Mapped[int] = mapped_column(
        ForeignKey("job_postings.id", ondelete="CASCADE"), nullable=False, index=True
    )

    candidate_semantic_score: Mapped[float] = mapped_column(Float, nullable=False)
    intent_semantic_score: Mapped[float] = mapped_column(Float, nullable=False)
    skill_score: Mapped[float] = mapped_column(Float, nullable=False)
    role_score: Mapped[float] = mapped_column(Float, nullable=False)
    seniority_score: Mapped[float] = mapped_column(Float, nullable=False)
    location_score: Mapped[float] = mapped_column(Float, nullable=False)
    recency_score: Mapped[float] = mapped_column(Float, nullable=False)

    final_score: Mapped[float] = mapped_column(Float, nullable=False, index=True)

    reasons: Mapped[list[Any]] = mapped_column(JSONB, nullable=False, default=list)
    concerns: Mapped[list[Any]] = mapped_column(JSONB, nullable=False, default=list)
    # Jev's judgement of the CV for this posting (app/services/jev/fit.py),
    # with the posting's content hash; None below the role gate or with
    # Jev off. Shown, not yet ranked on.
    jev_fit: Mapped[dict[str, Any] | None] = mapped_column(JSONB)

    def __repr__(self) -> str:
        return f"JobMatch(job_id={self.job_id!r}, final_score={self.final_score!r})"

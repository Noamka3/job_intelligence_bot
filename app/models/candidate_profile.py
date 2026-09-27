from __future__ import annotations

from datetime import datetime
from typing import Any

from pgvector.sqlalchemy import Vector
from sqlalchemy import Boolean, DateTime, Index, Integer, String, Text, func, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.models.constants import EMBEDDING_DIM


class CandidateProfile(Base):
    """A versioned snapshot of the CV being matched against.

    Only one row may have is_active=True at a time (enforced by a partial
    unique index, not just application logic) - previous versions are kept
    for history rather than deleted, per the "remember until explicitly
    replaced" requirement.
    """

    __tablename__ = "candidate_profiles"
    __table_args__ = (
        Index(
            "uq_candidate_profiles_single_active",
            "is_active",
            unique=True,
            postgresql_where=text("is_active"),
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    filename: Mapped[str] = mapped_column(String(255), nullable=False)
    file_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)

    raw_text: Mapped[str] = mapped_column(Text, nullable=False)
    normalized_text: Mapped[str] = mapped_column(Text, nullable=False)
    structured_profile: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)

    embedding: Mapped[list[float] | None] = mapped_column(Vector(EMBEDDING_DIM))

    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    # Structured extraction (a local LLM, up to minutes) and rescoring
    # every job happen *after* the upload has responded; this is how the
    # dashboard knows when they're done: "pending" -> "done" | "failed".
    processing_status: Mapped[str] = mapped_column(
        String(16), nullable=False, default="done", server_default="done"
    )
    processing_note: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    activated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    def __repr__(self) -> str:
        return (
            f"CandidateProfile(id={self.id!r}, version={self.version!r}, active={self.is_active!r})"
        )

from __future__ import annotations

from pgvector.sqlalchemy import Vector
from sqlalchemy import ARRAY, Boolean, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.models.constants import EMBEDDING_DIM
from app.models.mixins import TimestampMixin


class TargetRole(Base, TimestampMixin):
    """A configurable role the user wants matched against, e.g. "Junior
    Software Engineer". Multiple roles can be enabled at once; matching
    scores a job against each enabled role independently (see JobMatch).
    """

    __tablename__ = "target_roles"

    id: Mapped[int] = mapped_column(primary_key=True)
    canonical_name: Mapped[str] = mapped_column(String(255), nullable=False, unique=True)
    aliases: Mapped[list[str]] = mapped_column(ARRAY(String), nullable=False, default=list)
    description: Mapped[str | None] = mapped_column(Text)

    positive_keywords: Mapped[list[str]] = mapped_column(
        ARRAY(String), nullable=False, default=list
    )
    negative_keywords: Mapped[list[str]] = mapped_column(
        ARRAY(String), nullable=False, default=list
    )
    preferred_skills: Mapped[list[str]] = mapped_column(ARRAY(String), nullable=False, default=list)
    max_expected_years: Mapped[int | None] = mapped_column(Integer)

    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    embedding: Mapped[list[float] | None] = mapped_column(Vector(EMBEDDING_DIM))

    def __repr__(self) -> str:
        return f"TargetRole(id={self.id!r}, canonical_name={self.canonical_name!r})"

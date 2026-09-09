from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import Boolean, DateTime, Enum, ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.models.enums import CareerSourceType
from app.models.mixins import TimestampMixin

if TYPE_CHECKING:
    from app.models.company import Company


class CareerSource(Base, TimestampMixin):
    """A recruiting source for a company (ATS board, career page, ...).

    A company may have more than one source over time; source_type drives
    which JobSourceAdapter handles it. UNSUPPORTED covers LinkedIn-only
    entries and rows the resolver could not classify (missing/broken URL) -
    these are recorded, not dropped, so the sheet sync stays auditable.
    """

    __tablename__ = "career_sources"
    __table_args__ = (
        UniqueConstraint("company_id", "source_url", name="uq_career_sources_company_url"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    company_id: Mapped[int] = mapped_column(
        ForeignKey("companies.id", ondelete="CASCADE"), nullable=False, index=True
    )
    source_type: Mapped[CareerSourceType] = mapped_column(
        Enum(CareerSourceType, name="career_source_type", native_enum=True), nullable=False
    )
    source_url: Mapped[str] = mapped_column(String(2048), nullable=False)
    external_identifier: Mapped[str | None] = mapped_column(String(255))
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    poll_interval_minutes: Mapped[int] = mapped_column(Integer, nullable=False, default=5)

    last_successful_check_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_attempt_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    next_check_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    last_http_status: Mapped[int | None] = mapped_column(Integer)
    consecutive_failures: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    unsupported_reason: Mapped[str | None] = mapped_column(String(500))

    company: Mapped[Company] = relationship(back_populates="career_sources")

    def __repr__(self) -> str:
        return f"CareerSource(id={self.id!r}, type={self.source_type!r}, url={self.source_url!r})"

from __future__ import annotations

from datetime import datetime

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    ARRAY,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.models.constants import EMBEDDING_DIM
from app.models.enums import EmploymentType, JobStatus, RemoteType, SeniorityLevel
from app.models.mixins import TimestampMixin


class JobPosting(Base, TimestampMixin):
    """A single job listing discovered from a CareerSource.

    Publication-time fields are kept deliberately distinct:
    source_published_at / source_updated_at are only set when the ATS
    actually reports them; first_seen_at / last_seen_at are always ours.
    The API must never present first_seen_at as if it were the real
    publish date - see docs/matching.md and spec section 22.
    """

    __tablename__ = "job_postings"
    __table_args__ = (
        UniqueConstraint(
            "career_source_id", "external_job_id", name="uq_job_postings_source_external_id"
        ),
        Index("ix_job_postings_apply_url", "apply_url"),
        Index(
            "ix_job_postings_fingerprint", "company_id", "normalized_title", "normalized_location"
        ),
        Index(
            "ix_job_postings_embedding_hnsw",
            "embedding",
            postgresql_using="hnsw",
            postgresql_with={"m": 16, "ef_construction": 64},
            postgresql_ops={"embedding": "vector_cosine_ops"},
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    company_id: Mapped[int] = mapped_column(
        ForeignKey("companies.id", ondelete="CASCADE"), nullable=False, index=True
    )
    career_source_id: Mapped[int] = mapped_column(
        ForeignKey("career_sources.id", ondelete="CASCADE"), nullable=False, index=True
    )
    external_job_id: Mapped[str] = mapped_column(String(255), nullable=False)

    title: Mapped[str] = mapped_column(String(500), nullable=False)
    normalized_title: Mapped[str] = mapped_column(String(500), nullable=False, index=True)

    department: Mapped[str | None] = mapped_column(String(255))
    team: Mapped[str | None] = mapped_column(String(255))

    location_text: Mapped[str | None] = mapped_column(String(500))
    normalized_location: Mapped[str | None] = mapped_column(String(255))
    country: Mapped[str | None] = mapped_column(String(100))
    # A key from app.services.jobs.location.REGIONS, set at ingest.
    region: Mapped[str | None] = mapped_column(String(32), index=True)
    city: Mapped[str | None] = mapped_column(String(255))
    remote_type: Mapped[RemoteType] = mapped_column(
        Enum(RemoteType, name="remote_type"), nullable=False, default=RemoteType.UNKNOWN
    )

    employment_type: Mapped[EmploymentType] = mapped_column(
        Enum(EmploymentType, name="employment_type"), nullable=False, default=EmploymentType.UNKNOWN
    )

    description: Mapped[str | None] = mapped_column(Text)
    normalized_description: Mapped[str | None] = mapped_column(Text)
    responsibilities: Mapped[str | None] = mapped_column(Text)
    qualifications: Mapped[str | None] = mapped_column(Text)
    required_skills: Mapped[list[str]] = mapped_column(ARRAY(String), nullable=False, default=list)
    preferred_skills: Mapped[list[str]] = mapped_column(ARRAY(String), nullable=False, default=list)

    experience_min_years: Mapped[int | None] = mapped_column(Integer)
    experience_max_years: Mapped[int | None] = mapped_column(Integer)
    seniority: Mapped[SeniorityLevel] = mapped_column(
        Enum(SeniorityLevel, name="seniority_level"), nullable=False, default=SeniorityLevel.UNKNOWN
    )

    source_url: Mapped[str] = mapped_column(String(2048), nullable=False)
    apply_url: Mapped[str | None] = mapped_column(String(2048))

    source_published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    source_updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    first_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    last_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    # When the job's own page was last downloaded. A listed job whose page
    # is younger than JOB_DETAILS_REFRESH_HOURS is not fetched again (see
    # ingestion._is_definitely_unchanged).
    details_fetched_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    status: Mapped[JobStatus] = mapped_column(
        Enum(JobStatus, name="job_status"), nullable=False, default=JobStatus.ACTIVE, index=True
    )
    missing_streak: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    embedding: Mapped[list[float] | None] = mapped_column(Vector(EMBEDDING_DIM))

    def __repr__(self) -> str:
        return f"JobPosting(id={self.id!r}, title={self.title!r}, status={self.status!r})"

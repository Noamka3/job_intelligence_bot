from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict

from app.models.enums import EmploymentType, JobStatus, RemoteType


class JobRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    company_id: int
    career_source_id: int
    external_job_id: str
    title: str
    department: str | None
    location_text: str | None
    remote_type: RemoteType
    employment_type: EmploymentType
    source_url: str
    apply_url: str | None
    source_published_at: datetime | None
    source_updated_at: datetime | None
    first_seen_at: datetime
    last_seen_at: datetime
    status: JobStatus


class JevReading(BaseModel):
    model: str
    role_family: str
    role_family_confidence: float
    seniority: Literal["student", "junior", "mid", "senior", "lead"]
    seniority_confidence: float
    students_only: float
    experience_required: float


class JobDetailRead(JobRead):
    description: str | None
    responsibilities: str | None
    qualifications: str | None
    required_skills: list[str]
    preferred_skills: list[str]
    # Jev's reading of the posting, when it has been asked (docs/jev.md).
    jev_reading: JevReading | None

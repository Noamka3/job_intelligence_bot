from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class StructuredCandidateProfile(BaseModel):
    """AI-extracted structured view of a CV.

    Matching must never depend on this alone (per spec §6) - raw_text and
    normalized_text are always kept and used alongside it.
    """

    skills: list[str] = Field(default_factory=list)
    programming_languages: list[str] = Field(default_factory=list)
    frameworks: list[str] = Field(default_factory=list)
    databases: list[str] = Field(default_factory=list)
    cloud: list[str] = Field(default_factory=list)
    devops: list[str] = Field(default_factory=list)
    education: list[str] = Field(default_factory=list)
    projects: list[str] = Field(default_factory=list)
    years_of_experience: float | None = None
    seniority: str | None = None
    domains: list[str] = Field(default_factory=list)
    keywords: list[str] = Field(default_factory=list)


class CandidateProfileRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    version: int
    filename: str
    file_hash: str
    is_active: bool
    structured_profile: dict[str, object]
    created_at: datetime
    activated_at: datetime | None

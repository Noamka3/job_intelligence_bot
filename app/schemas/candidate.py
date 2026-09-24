from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator

_LIST_FIELDS = (
    "skills",
    "programming_languages",
    "frameworks",
    "databases",
    "cloud",
    "devops",
    "education",
    "projects",
    "domains",
    "keywords",
)


def _coerce_string_list(value: object) -> list[str]:
    """A small local model sometimes returns an object per entry
    ({"degree": "B.Sc.", "years": "2022-2026"}) or a bare string where a
    list of strings was asked for. Keep the information as one line each
    instead of failing the whole extraction over it."""
    if value is None:
        return []
    if isinstance(value, str):
        return [value.strip()] if value.strip() else []
    if isinstance(value, dict):
        value = [value]
    if not isinstance(value, (list, tuple, set)):
        return [str(value)]
    items: list[str] = []
    for item in value:
        if isinstance(item, dict):
            text = ", ".join(str(part) for part in item.values() if part not in (None, "", [], {}))
        else:
            text = str(item)
        if text.strip():
            items.append(text.strip())
    return items


class CandidateProfileLists(BaseModel):
    """The parts of a CV a language model copies out as lists - the schema
    the local model is asked to fill (see structured_profile.py)."""

    skills: list[str] = Field(default_factory=list)
    programming_languages: list[str] = Field(default_factory=list)
    frameworks: list[str] = Field(default_factory=list)
    databases: list[str] = Field(default_factory=list)
    cloud: list[str] = Field(default_factory=list)
    devops: list[str] = Field(default_factory=list)
    education: list[str] = Field(default_factory=list)
    projects: list[str] = Field(default_factory=list)
    domains: list[str] = Field(default_factory=list)
    keywords: list[str] = Field(default_factory=list)

    @field_validator(*_LIST_FIELDS, mode="before")
    @classmethod
    def _as_string_list(cls, value: object) -> list[str]:
        return _coerce_string_list(value)


class StructuredCandidateProfile(CandidateProfileLists):
    """AI-extracted structured view of a CV.

    Matching must never depend on this alone - raw_text and
    normalized_text are always kept and used alongside it.
    """

    years_of_experience: float | None = None
    seniority: str | None = None


class CandidateProfileText(BaseModel):
    id: int
    raw_text: str
    normalized_text: str


class CandidateProfileRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    version: int
    filename: str
    file_hash: str
    is_active: bool
    structured_profile: dict[str, object]
    processing_status: str
    processing_note: str | None
    created_at: datetime
    activated_at: datetime | None

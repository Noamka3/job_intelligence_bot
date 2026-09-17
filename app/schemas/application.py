from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field, field_validator

from app.models.enums import ApplicationStatus


class ApplicationRead(BaseModel):
    id: int
    job_id: int
    status: ApplicationStatus
    applied_at: datetime
    updated_at: datetime
    notes: str | None

    job_title: str
    company_name: str
    location_text: str | None
    apply_url: str | None
    source_url: str
    job_status: str


class ApplicationUpdate(BaseModel):
    """PATCH: only provided fields change. `notes: null` clears the notes."""

    status: ApplicationStatus | None = None
    notes: str | None = Field(None, max_length=4000)

    @field_validator("status", mode="before")
    @classmethod
    def _status_not_null(cls, value: object) -> object:
        if value is None:
            raise ValueError("status cannot be null - omit it to leave it unchanged")
        return value

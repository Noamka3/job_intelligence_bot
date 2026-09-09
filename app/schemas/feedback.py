from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict

from app.models.enums import JobFeedbackAction


class JobFeedbackCreate(BaseModel):
    action: JobFeedbackAction


class JobFeedbackRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    job_id: int
    action: JobFeedbackAction
    created_at: datetime

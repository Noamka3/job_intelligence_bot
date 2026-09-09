from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict

from app.models.enums import CareerSourceType


class CareerSourceRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    company_id: int
    source_type: CareerSourceType
    source_url: str
    external_identifier: str | None
    enabled: bool
    poll_interval_minutes: int
    unsupported_reason: str | None
    consecutive_failures: int
    last_successful_check_at: datetime | None
    next_check_at: datetime | None

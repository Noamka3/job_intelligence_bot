from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel


class MatchRead(BaseModel):
    id: int
    job_id: int
    target_role_id: int

    final_score: float
    candidate_semantic_score: float
    intent_semantic_score: float
    skill_score: float
    role_score: float
    seniority_score: float
    location_score: float
    recency_score: float

    reasons: list[str]
    concerns: list[str]
    created_at: datetime

    job_title: str
    company_name: str
    location_text: str | None
    apply_url: str | None

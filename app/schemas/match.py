from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel

from app.models.enums import CareerSourceType, JobFeedbackAction, SeniorityLevel
from app.services.matching.queries import SeniorityFit


class JevFit(BaseModel):
    model: str
    would_be_considered: float
    skills_coverage: float
    experience_level: Literal["below", "matches", "above"]
    experience_level_confidence: float


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
    company_id: int
    company_name: str
    location_text: str | None
    country: str | None
    region: str | None
    source_type: CareerSourceType
    source_url: str
    apply_url: str | None
    source_published_at: datetime | None
    first_seen_at: datetime
    last_feedback: JobFeedbackAction | None
    # What the posting itself says about experience, relative to the
    # target role: the dashboard's tag and "fits a junior" filter.
    seniority_fit: SeniorityFit
    job_seniority: SeniorityLevel
    experience_min_years: int | None
    # Jev's judgement of the CV for this posting, when it was asked
    # (above the role gate, key set) - see docs/jev.md.
    jev_fit: JevFit | None

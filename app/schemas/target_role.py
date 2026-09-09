from __future__ import annotations

from pydantic import BaseModel, ConfigDict


class TargetRoleCreate(BaseModel):
    canonical_name: str
    aliases: list[str] = []
    description: str | None = None
    positive_keywords: list[str] = []
    negative_keywords: list[str] = []
    preferred_skills: list[str] = []
    max_expected_years: int | None = None
    enabled: bool = True


class TargetRoleUpdate(BaseModel):
    """All fields optional: PATCH semantics - only provided fields change."""

    canonical_name: str | None = None
    aliases: list[str] | None = None
    description: str | None = None
    positive_keywords: list[str] | None = None
    negative_keywords: list[str] | None = None
    preferred_skills: list[str] | None = None
    max_expected_years: int | None = None
    enabled: bool | None = None


class TargetRoleRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    canonical_name: str
    aliases: list[str]
    description: str | None
    positive_keywords: list[str]
    negative_keywords: list[str]
    preferred_skills: list[str]
    max_expected_years: int | None
    enabled: bool

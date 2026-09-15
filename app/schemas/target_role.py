from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field, field_validator


class TargetRoleCreate(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    canonical_name: str = Field(min_length=1)
    aliases: list[str] = []
    description: str | None = None
    positive_keywords: list[str] = []
    negative_keywords: list[str] = []
    preferred_skills: list[str] = []
    max_expected_years: int | None = Field(None, ge=0)
    enabled: bool = True


class TargetRoleUpdate(BaseModel):
    """PATCH semantics - only provided fields change. Omitting a field and
    sending it as null are different things: null is only accepted for
    the two fields that are nullable in the database."""

    model_config = ConfigDict(str_strip_whitespace=True)

    canonical_name: str | None = Field(None, min_length=1)
    aliases: list[str] | None = None
    description: str | None = None
    positive_keywords: list[str] | None = None
    negative_keywords: list[str] | None = None
    preferred_skills: list[str] | None = None
    max_expected_years: int | None = Field(None, ge=0)
    enabled: bool | None = None

    @field_validator(
        "canonical_name",
        "aliases",
        "positive_keywords",
        "negative_keywords",
        "preferred_skills",
        "enabled",
        mode="before",
    )
    @classmethod
    def _not_null(cls, value: object) -> object:
        if value is None:
            raise ValueError("this field cannot be null - omit it to leave it unchanged")
        return value


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

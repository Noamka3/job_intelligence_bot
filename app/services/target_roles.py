"""TargetRole CRUD + its own embedding (spec §7: role *intent* is embedded
separately from the candidate's CV, since a CV can span several domains
even when the current search is narrowly scoped).
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models.target_role import TargetRole
from app.schemas.target_role import TargetRoleCreate, TargetRoleUpdate
from app.services.embeddings.base import EmbeddingProvider

_EMBEDDING_RELEVANT_FIELDS = frozenset(
    {"canonical_name", "aliases", "description", "positive_keywords", "preferred_skills"}
)


class TargetRoleNotFoundError(LookupError):
    def __init__(self, role_id: int) -> None:
        super().__init__(f"TargetRole {role_id} not found")


class TargetRoleAlreadyExistsError(ValueError):
    def __init__(self, canonical_name: str) -> None:
        super().__init__(f"A target role named {canonical_name!r} already exists")


def _commit_or_conflict(db: Session, canonical_name: str) -> None:
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise TargetRoleAlreadyExistsError(canonical_name) from exc


def _build_embedding_text(role: TargetRole) -> str:
    parts = [role.canonical_name, *role.aliases]
    if role.description:
        parts.append(role.description)
    if role.positive_keywords:
        parts.append("Keywords: " + ", ".join(role.positive_keywords))
    if role.preferred_skills:
        parts.append("Preferred skills: " + ", ".join(role.preferred_skills))
    return "\n".join(parts)


def create_target_role(
    db: Session, data: TargetRoleCreate, embedding_provider: EmbeddingProvider
) -> TargetRole:
    role = TargetRole(**data.model_dump())
    role.embedding = embedding_provider.embed_one(_build_embedding_text(role))
    db.add(role)
    _commit_or_conflict(db, role.canonical_name)
    db.refresh(role)
    return role


def update_target_role(
    db: Session, role_id: int, data: TargetRoleUpdate, embedding_provider: EmbeddingProvider
) -> TargetRole:
    role = db.get(TargetRole, role_id)
    if role is None:
        raise TargetRoleNotFoundError(role_id)

    updates = data.model_dump(exclude_unset=True)
    for field, value in updates.items():
        setattr(role, field, value)

    if _EMBEDDING_RELEVANT_FIELDS & updates.keys():
        role.embedding = embedding_provider.embed_one(_build_embedding_text(role))

    _commit_or_conflict(db, role.canonical_name)
    db.refresh(role)
    return role


def list_target_roles(db: Session) -> list[TargetRole]:
    return list(db.execute(select(TargetRole).order_by(TargetRole.canonical_name)).scalars())


def get_target_role(db: Session, role_id: int) -> TargetRole | None:
    return db.get(TargetRole, role_id)

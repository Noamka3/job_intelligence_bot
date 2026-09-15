from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.schemas.target_role import TargetRoleCreate, TargetRoleRead, TargetRoleUpdate
from app.services.embeddings import get_embedding_provider
from app.services.embeddings.base import EmbeddingProvider
from app.services.matching.runner import score_all_active_jobs
from app.services.target_roles import (
    TargetRoleAlreadyExistsError,
    TargetRoleNotFoundError,
    create_target_role,
    list_target_roles,
    update_target_role,
)

router = APIRouter(prefix="/target-roles", tags=["target-roles"])


@router.get("", response_model=list[TargetRoleRead])
def list_roles(db: Session = Depends(get_db)) -> list[TargetRoleRead]:
    return [TargetRoleRead.model_validate(role) for role in list_target_roles(db)]


@router.post("", response_model=TargetRoleRead, status_code=status.HTTP_201_CREATED)
def create_role(
    data: TargetRoleCreate,
    db: Session = Depends(get_db),
    embedding_provider: EmbeddingProvider = Depends(get_embedding_provider),
) -> TargetRoleRead:
    try:
        role = create_target_role(db, data, embedding_provider)
    except TargetRoleAlreadyExistsError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
    # Jobs already in the database get scored against the new role right
    # away instead of waiting for a crawl to touch each of them.
    score_all_active_jobs(db)
    return TargetRoleRead.model_validate(role)


@router.patch("/{role_id}", response_model=TargetRoleRead)
def patch_role(
    role_id: int,
    data: TargetRoleUpdate,
    db: Session = Depends(get_db),
    embedding_provider: EmbeddingProvider = Depends(get_embedding_provider),
) -> TargetRoleRead:
    try:
        role = update_target_role(db, role_id, data, embedding_provider)
    except TargetRoleNotFoundError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc
    except TargetRoleAlreadyExistsError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
    score_all_active_jobs(db)
    return TargetRoleRead.model_validate(role)

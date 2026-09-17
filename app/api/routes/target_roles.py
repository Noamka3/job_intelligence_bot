from __future__ import annotations

import logging
from collections.abc import Callable
from contextlib import AbstractContextManager

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.db.session import get_background_session_factory, get_db
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
logger = logging.getLogger(__name__)

SessionFactory = Callable[[], AbstractContextManager[Session]]


def _rescore_later(session_factory: SessionFactory) -> None:
    # Jobs already in the database get scored against the changed role
    # set right after the response - minutes of CPU that must not sit
    # inside the request.
    with session_factory() as db:
        try:
            score_all_active_jobs(db)
        except Exception:  # noqa: BLE001 - background: log, don't crash the worker thread
            logger.exception("background rescoring after a target-role change failed")


@router.get("", response_model=list[TargetRoleRead])
def list_roles(db: Session = Depends(get_db)) -> list[TargetRoleRead]:
    return [TargetRoleRead.model_validate(role) for role in list_target_roles(db)]


@router.post("", response_model=TargetRoleRead, status_code=status.HTTP_201_CREATED)
def create_role(
    data: TargetRoleCreate,
    background: BackgroundTasks,
    db: Session = Depends(get_db),
    embedding_provider: EmbeddingProvider = Depends(get_embedding_provider),
    session_factory: SessionFactory = Depends(get_background_session_factory),
) -> TargetRoleRead:
    try:
        role = create_target_role(db, data, embedding_provider)
    except TargetRoleAlreadyExistsError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
    background.add_task(_rescore_later, session_factory)
    return TargetRoleRead.model_validate(role)


@router.patch("/{role_id}", response_model=TargetRoleRead)
def patch_role(
    role_id: int,
    data: TargetRoleUpdate,
    background: BackgroundTasks,
    db: Session = Depends(get_db),
    embedding_provider: EmbeddingProvider = Depends(get_embedding_provider),
    session_factory: SessionFactory = Depends(get_background_session_factory),
) -> TargetRoleRead:
    try:
        role = update_target_role(db, role_id, data, embedding_provider)
    except TargetRoleNotFoundError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc
    except TargetRoleAlreadyExistsError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
    background.add_task(_rescore_later, session_factory)
    return TargetRoleRead.model_validate(role)

from __future__ import annotations

import logging
from collections.abc import Callable
from contextlib import AbstractContextManager
from pathlib import PurePosixPath, PureWindowsPath

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, UploadFile, status
from fastapi.concurrency import run_in_threadpool
from sqlalchemy.orm import Session

from app.db.session import get_background_session_factory, get_db
from app.models.candidate_profile import CandidateProfile
from app.schemas.candidate import CandidateProfileRead, CandidateProfileText
from app.services.candidate.extraction import UnreadableResumeError, UnsupportedResumeFormatError
from app.services.candidate.profile_service import (
    EmptyResumeTextError,
    ProfileNotFoundError,
    activate_profile,
    finish_profile_processing,
    get_active_profile,
    ingest_resume,
    list_profiles,
)
from app.services.embeddings import get_embedding_provider
from app.services.embeddings.base import EmbeddingProvider

router = APIRouter(prefix="/candidate", tags=["candidate"])
logger = logging.getLogger(__name__)

_MAX_RESUME_BYTES = 10 * 1024 * 1024  # 10 MB
_READ_CHUNK_BYTES = 1024 * 1024
_MAX_FILENAME_CHARS = 255  # CandidateProfile.filename column width

SessionFactory = Callable[[], AbstractContextManager[Session]]


async def _read_bounded(file: UploadFile) -> bytes:
    """Reads at most the size limit (+1 byte to detect overflow) instead of
    buffering an arbitrarily large body before checking it."""
    chunks: list[bytes] = []
    total = 0
    while chunk := await file.read(_READ_CHUNK_BYTES):
        total += len(chunk)
        if total > _MAX_RESUME_BYTES:
            raise HTTPException(
                status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, "Resume file too large (max 10MB)"
            )
        chunks.append(chunk)
    return b"".join(chunks)


def _safe_filename(raw: str) -> str:
    # Browsers/clients may send a full path; only the last component is
    # meaningful, and it must fit the column it's stored in.
    name = PurePosixPath(PureWindowsPath(raw).as_posix()).name
    if not name:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Missing filename")
    if len(name) > _MAX_FILENAME_CHARS:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST, f"Filename too long (max {_MAX_FILENAME_CHARS} chars)"
        )
    return name


def _process_profile_later(session_factory: SessionFactory, profile_id: int) -> None:
    """Runs after the response: LLM extraction (minutes on this hardware)
    and rescoring ~3,000 jobs must never sit inside the upload request."""
    with session_factory() as db:
        finish_profile_processing(db, profile_id)


@router.post("/resume", response_model=CandidateProfileRead, status_code=status.HTTP_201_CREATED)
async def upload_resume(
    file: UploadFile,
    background: BackgroundTasks,
    db: Session = Depends(get_db),
    embedding_provider: EmbeddingProvider = Depends(get_embedding_provider),
    session_factory: SessionFactory = Depends(get_background_session_factory),
) -> CandidateProfileRead:
    if not file.filename:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Missing filename")
    filename = _safe_filename(file.filename)
    content = await _read_bounded(file)

    try:
        # Parsing + embedding take seconds; still off the event loop
        # thread so /health and the dashboard stay responsive meanwhile.
        profile = await run_in_threadpool(
            ingest_resume,
            db,
            filename=filename,
            content=content,
            embedding_provider=embedding_provider,
        )
    except (UnsupportedResumeFormatError, UnreadableResumeError, EmptyResumeTextError) as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc

    background.add_task(_process_profile_later, session_factory, profile.id)
    return CandidateProfileRead.model_validate(profile)


@router.get("/active", response_model=CandidateProfileRead)
def get_active(db: Session = Depends(get_db)) -> CandidateProfileRead:
    profile = get_active_profile(db)
    if profile is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No active candidate profile")
    return CandidateProfileRead.model_validate(profile)


@router.get("/profiles", response_model=list[CandidateProfileRead])
def get_profiles(db: Session = Depends(get_db)) -> list[CandidateProfileRead]:
    return [CandidateProfileRead.model_validate(profile) for profile in list_profiles(db)]


@router.get("/profiles/{profile_id}/text", response_model=CandidateProfileText)
def get_profile_text(profile_id: int, db: Session = Depends(get_db)) -> CandidateProfileText:
    """What was actually read out of the uploaded file - so the user can
    check by eye that nothing was lost in parsing."""
    profile = db.get(CandidateProfile, profile_id)
    if profile is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Profile not found")
    return CandidateProfileText(
        id=profile.id, raw_text=profile.raw_text, normalized_text=profile.normalized_text
    )


@router.post("/profiles/{profile_id}/activate", response_model=CandidateProfileRead)
def activate(
    profile_id: int,
    background: BackgroundTasks,
    db: Session = Depends(get_db),
    session_factory: SessionFactory = Depends(get_background_session_factory),
) -> CandidateProfileRead:
    try:
        profile = activate_profile(db, profile_id)
    except ProfileNotFoundError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc
    background.add_task(_process_profile_later, session_factory, profile.id)
    return CandidateProfileRead.model_validate(profile)

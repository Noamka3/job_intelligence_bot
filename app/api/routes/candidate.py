from __future__ import annotations

from pathlib import PurePosixPath, PureWindowsPath

from fastapi import APIRouter, Depends, HTTPException, UploadFile, status
from fastapi.concurrency import run_in_threadpool
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.schemas.candidate import CandidateProfileRead
from app.services.candidate.extraction import UnreadableResumeError, UnsupportedResumeFormatError
from app.services.candidate.profile_service import (
    EmptyResumeTextError,
    ProfileNotFoundError,
    activate_profile,
    get_active_profile,
    ingest_resume,
    list_profiles,
)
from app.services.embeddings import get_embedding_provider
from app.services.embeddings.base import EmbeddingProvider
from app.services.matching.runner import score_all_active_jobs

router = APIRouter(prefix="/candidate", tags=["candidate"])

_MAX_RESUME_BYTES = 10 * 1024 * 1024  # 10 MB
_READ_CHUNK_BYTES = 1024 * 1024
_MAX_FILENAME_CHARS = 255  # CandidateProfile.filename column width


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


@router.post("/resume", response_model=CandidateProfileRead, status_code=status.HTTP_201_CREATED)
async def upload_resume(
    file: UploadFile,
    db: Session = Depends(get_db),
    embedding_provider: EmbeddingProvider = Depends(get_embedding_provider),
) -> CandidateProfileRead:
    if not file.filename:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Missing filename")
    filename = _safe_filename(file.filename)
    content = await _read_bounded(file)

    try:
        # ingest_resume is synchronous and can be slow (structured
        # extraction may call a local LLM - potentially minutes on
        # CPU-only hardware, though that step degrades gracefully rather
        # than blocking the resume from being saved, see profile_service).
        # Still run it off the event loop thread regardless, so a single
        # upload can never freeze every other request on this server,
        # including /health, while it's in flight.
        profile = await run_in_threadpool(
            ingest_resume,
            db,
            filename=filename,
            content=content,
            embedding_provider=embedding_provider,
        )
    except (UnsupportedResumeFormatError, UnreadableResumeError, EmptyResumeTextError) as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc

    # A new active profile means every existing match is against the old
    # CV; rescore now so /matches/top reflects it immediately.
    await run_in_threadpool(score_all_active_jobs, db)
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


@router.post("/profiles/{profile_id}/activate", response_model=CandidateProfileRead)
def activate(profile_id: int, db: Session = Depends(get_db)) -> CandidateProfileRead:
    try:
        profile = activate_profile(db, profile_id)
    except ProfileNotFoundError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc
    score_all_active_jobs(db)
    return CandidateProfileRead.model_validate(profile)

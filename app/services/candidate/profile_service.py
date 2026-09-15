"""CandidateProfile lifecycle: ingest a resume, keep exactly one active
profile, keep prior versions for history. See spec §6.
"""

from __future__ import annotations

import hashlib
import logging
from pathlib import Path

from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from app.core.paths import RESUME_STORAGE_DIR
from app.core.timezone import utc_now
from app.models.candidate_profile import CandidateProfile
from app.schemas.candidate import StructuredCandidateProfile
from app.services.candidate.extraction import extract_text
from app.services.candidate.normalization import normalize_text
from app.services.candidate.structured_profile import (
    OllamaTimeoutError,
    OllamaUnavailableError,
    OpenAIExtractionError,
    OpenAINotConfiguredError,
    extract_structured_profile,
)
from app.services.embeddings.base import EmbeddingProvider

logger = logging.getLogger(__name__)


class ProfileNotFoundError(LookupError):
    def __init__(self, profile_id: int) -> None:
        super().__init__(f"CandidateProfile {profile_id} not found")


class EmptyResumeTextError(ValueError):
    def __init__(self, filename: str) -> None:
        super().__init__(
            f"No text could be extracted from {filename!r} - a scanned/image-only PDF? "
            "Upload a text-based PDF or a .docx."
        )


def ingest_resume(
    db: Session,
    *,
    filename: str,
    content: bytes,
    embedding_provider: EmbeddingProvider,
) -> CandidateProfile:
    """Parse, embed, and activate a newly uploaded resume.

    Re-uploading a file whose content already exists (same SHA-256) just
    re-activates that existing version instead of creating a duplicate.
    """
    file_hash = hashlib.sha256(content).hexdigest()

    existing = db.execute(
        select(CandidateProfile).where(CandidateProfile.file_hash == file_hash)
    ).scalar_one_or_none()
    if existing is not None:
        logger.info("resume already ingested, re-activating", extra={"file_hash": file_hash})
        return activate_profile(db, existing.id)

    raw_text = extract_text(filename, content)
    normalized_text = normalize_text(raw_text)
    if not normalized_text.strip():
        # An image-only PDF parses "successfully" to nothing. Activating a
        # profile embedded from an empty string would silently make every
        # match meaningless and deactivate the working one.
        raise EmptyResumeTextError(filename)
    structured = _extract_structured_profile_best_effort(normalized_text)
    embedding = embedding_provider.embed_one(normalized_text)

    next_version = (
        db.execute(select(func.coalesce(func.max(CandidateProfile.version), 0))).scalar_one() + 1
    )

    db.execute(update(CandidateProfile).where(CandidateProfile.is_active.is_(True)).values(is_active=False))
    db.flush()

    profile = CandidateProfile(
        version=next_version,
        filename=filename,
        file_hash=file_hash,
        raw_text=raw_text,
        normalized_text=normalized_text,
        structured_profile=structured.model_dump(),
        embedding=embedding,
        is_active=True,
        activated_at=utc_now(),
    )
    db.add(profile)
    db.commit()
    db.refresh(profile)

    _store_resume_file(file_hash, filename, content)
    return profile


def activate_profile(db: Session, profile_id: int) -> CandidateProfile:
    profile = db.get(CandidateProfile, profile_id)
    if profile is None:
        raise ProfileNotFoundError(profile_id)

    db.execute(update(CandidateProfile).where(CandidateProfile.is_active.is_(True)).values(is_active=False))
    db.flush()

    profile.is_active = True
    profile.activated_at = utc_now()
    db.commit()
    db.refresh(profile)
    return profile


def get_active_profile(db: Session) -> CandidateProfile | None:
    return db.execute(
        select(CandidateProfile).where(CandidateProfile.is_active.is_(True))
    ).scalar_one_or_none()


def list_profiles(db: Session) -> list[CandidateProfile]:
    rows = db.execute(select(CandidateProfile).order_by(CandidateProfile.version.desc())).scalars()
    return list(rows)


def _extract_structured_profile_best_effort(normalized_text: str) -> StructuredCandidateProfile:
    """Structured extraction is a secondary signal - spec §6 explicitly
    says matching must never depend on it alone, raw_text/embedding are
    what actually matter. So a slow or unavailable LLM (observed on this
    dev machine: local CPU-only inference can take minutes to hours) must
    never block the resume itself from being saved; it degrades to an
    empty profile and logs clearly instead. Re-run via `rebuild-profile`
    once the LLM is fast/available again.
    """
    try:
        return extract_structured_profile(normalized_text)
    except (
        OpenAINotConfiguredError,
        OpenAIExtractionError,
        OllamaUnavailableError,
        OllamaTimeoutError,
    ) as exc:
        logger.warning(
            "structured CV extraction failed, saving resume without it",
            extra={"error_type": type(exc).__name__, "error": str(exc)},
        )
        return StructuredCandidateProfile()


def _store_resume_file(file_hash: str, filename: str, content: bytes) -> None:
    RESUME_STORAGE_DIR.mkdir(parents=True, exist_ok=True)
    suffix = Path(filename).suffix
    destination = RESUME_STORAGE_DIR / f"{file_hash}{suffix}"
    if not destination.exists():
        destination.write_bytes(content)

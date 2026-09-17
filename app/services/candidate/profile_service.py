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
    apply_deterministic_rules,
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


PROCESSING_PENDING = "pending"
PROCESSING_DONE = "done"
PROCESSING_FAILED = "failed"


def ingest_resume(
    db: Session,
    *,
    filename: str,
    content: bytes,
    embedding_provider: EmbeddingProvider,
) -> CandidateProfile:
    """Parse, embed and activate a newly uploaded resume - the fast part,
    seconds. The slow part (structured extraction by a local LLM, then
    rescoring every job) is left for finish_profile_processing(), run in
    the background after the request has responded; until it's done the
    profile's processing_status is "pending".

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
    embedding = embedding_provider.embed_one(normalized_text)

    next_version = (
        db.execute(select(func.coalesce(func.max(CandidateProfile.version), 0))).scalar_one() + 1
    )

    _deactivate_all(db)
    profile = CandidateProfile(
        version=next_version,
        filename=filename,
        file_hash=file_hash,
        raw_text=raw_text,
        normalized_text=normalized_text,
        structured_profile={},
        embedding=embedding,
        is_active=True,
        activated_at=utc_now(),
        processing_status=PROCESSING_PENDING,
    )
    db.add(profile)
    db.commit()
    db.refresh(profile)

    _store_resume_file(file_hash, filename, content)
    return profile


def activate_profile(db: Session, profile_id: int) -> CandidateProfile:
    """Makes an existing version the active one. Matches are computed
    against the active profile, so the caller must rescore afterwards -
    finish_profile_processing() does, and marks the profile pending
    until then."""
    profile = db.get(CandidateProfile, profile_id)
    if profile is None:
        raise ProfileNotFoundError(profile_id)

    _deactivate_all(db)
    profile.is_active = True
    profile.activated_at = utc_now()
    profile.processing_status = PROCESSING_PENDING
    profile.processing_note = None
    db.commit()
    db.refresh(profile)
    return profile


def finish_profile_processing(db: Session, profile_id: int) -> None:
    """The slow half of an upload/activation: structured extraction (only
    if the profile has none yet - it's stable per file) and a full
    rescore. Never raises: the outcome is written to processing_status/
    processing_note for the dashboard to show."""
    from app.services.matching.runner import score_all_active_jobs

    profile = db.get(CandidateProfile, profile_id)
    if profile is None:
        return
    try:
        note: str | None = None
        # Retried on every activation until the model yields something, so
        # a CV uploaded while Ollama was down gets its lists the next time.
        if not _has_model_data(profile):
            normalized_text = profile.normalized_text
            # Don't sit "idle in transaction" for the minutes the local
            # model takes - the read above opened one; end it first.
            db.commit()
            structured, note = _extract_structured_profile_best_effort(normalized_text)
            profile.structured_profile = structured.model_dump()
            db.commit()
        if profile.is_active:
            score_all_active_jobs(db)
        profile.processing_status = PROCESSING_DONE
        profile.processing_note = note
    except Exception as exc:  # noqa: BLE001 - reported to the user, not swallowed silently
        logger.exception("profile processing failed", extra={"profile_id": profile_id})
        db.rollback()
        profile = db.get(CandidateProfile, profile_id)
        if profile is None:
            return
        profile.processing_status = PROCESSING_FAILED
        profile.processing_note = f"{type(exc).__name__}: {exc}"[:500]
    db.commit()


# What only the model fills in; level and skills also come from rules, so
# their presence says nothing about whether the model answered.
_MODEL_FIELDS = (
    "programming_languages",
    "frameworks",
    "databases",
    "cloud",
    "devops",
    "education",
    "projects",
    "domains",
    "keywords",
)


def _has_model_data(profile: CandidateProfile) -> bool:
    """False for a fresh upload ({}) and for a failed or empty extraction
    alike - both are worth retrying on the next activation."""
    structured = profile.structured_profile or {}
    return any(structured.get(key) for key in _MODEL_FIELDS)


def _deactivate_all(db: Session) -> None:
    db.execute(
        update(CandidateProfile).where(CandidateProfile.is_active.is_(True)).values(is_active=False)
    )
    db.flush()


def get_active_profile(db: Session) -> CandidateProfile | None:
    return db.execute(
        select(CandidateProfile).where(CandidateProfile.is_active.is_(True))
    ).scalar_one_or_none()


def list_profiles(db: Session) -> list[CandidateProfile]:
    rows = db.execute(select(CandidateProfile).order_by(CandidateProfile.version.desc())).scalars()
    return list(rows)


def _extract_structured_profile_best_effort(
    normalized_text: str,
) -> tuple[StructuredCandidateProfile, str | None]:
    """(profile, note for the dashboard - None when the model answered).

    Structured extraction is a secondary signal - spec §6 explicitly
    says matching must never depend on it alone, raw_text/embedding are
    what actually matter. So a slow or unavailable LLM (observed on this
    dev machine: local CPU-only inference can take minutes to hours, and
    the model times out whenever the 8GB of RAM are tight) must never
    block the resume itself from being saved. The level and the skills
    the vocabulary finds in the text are still filled in by rule, so the
    profile page never shows nothing; the model's lists are retried on
    the next activation (or `rebuild-profile`).
    """
    try:
        profile = extract_structured_profile(normalized_text)
    except (
        OpenAINotConfiguredError,
        OpenAIExtractionError,
        OllamaUnavailableError,
        OllamaTimeoutError,
    ) as exc:
        logger.warning(
            "structured CV extraction failed, saving resume without the model's part",
            extra={"error_type": type(exc).__name__, "error": str(exc)},
        )
        return apply_deterministic_rules(StructuredCandidateProfile(), normalized_text), (
            f"the local model did not answer ({type(exc).__name__}) - the level and skills "
            "were derived from the text; re-activate this CV to retry"
        )
    if not any(getattr(profile, key) for key in _MODEL_FIELDS):
        return profile, (
            "the model returned nothing - the level and skills were derived from the text; "
            "re-activate this CV to retry"
        )
    return profile, None


def _store_resume_file(file_hash: str, filename: str, content: bytes) -> None:
    RESUME_STORAGE_DIR.mkdir(parents=True, exist_ok=True)
    suffix = Path(filename).suffix
    destination = RESUME_STORAGE_DIR / f"{file_hash}{suffix}"
    if not destination.exists():
        destination.write_bytes(content)

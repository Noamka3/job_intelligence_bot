from __future__ import annotations

import io
from pathlib import Path

import pytest
from docx import Document
from sqlalchemy.orm import Session

from app.schemas.candidate import StructuredCandidateProfile
from app.services.candidate import profile_service
from app.services.candidate.structured_profile import (
    OllamaTimeoutError,
    OllamaUnavailableError,
    OpenAINotConfiguredError,
)
from tests.conftest import FakeEmbeddingProvider


@pytest.fixture(autouse=True)
def _no_real_llm_extraction(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Every test in this module ingests resumes; none should hit OpenAI or
    write into the real data/resumes directory.
    """
    monkeypatch.setattr(
        profile_service,
        "extract_structured_profile",
        lambda text: StructuredCandidateProfile(
            seniority="junior", programming_languages=["Python"]
        ),
    )
    monkeypatch.setattr(profile_service, "RESUME_STORAGE_DIR", tmp_path)


def _docx_bytes(text: str) -> bytes:
    document = Document()
    document.add_paragraph(text)
    buffer = io.BytesIO()
    document.save(buffer)
    return buffer.getvalue()


def test_ingest_resume_creates_active_profile(
    db_session: Session, fake_embedding_provider: FakeEmbeddingProvider
) -> None:
    content = _docx_bytes("Jane Doe - Junior Software Engineer")

    profile = profile_service.ingest_resume(
        db_session, filename="cv.docx", content=content, embedding_provider=fake_embedding_provider
    )

    # The fast half: parsed, embedded, active - but not yet extracted/scored.
    assert profile.is_active is True
    assert profile.version == 1
    assert profile.processing_status == profile_service.PROCESSING_PENDING
    assert profile.structured_profile == {}
    assert "Jane Doe" in profile.raw_text
    assert profile.embedding is not None

    profile_service.finish_profile_processing(db_session, profile.id)

    db_session.refresh(profile)
    assert profile.processing_status == profile_service.PROCESSING_DONE
    assert profile.processing_note is None
    assert profile.structured_profile["seniority"] == "junior"
    assert profile.structured_profile["programming_languages"] == ["Python"]


def test_ingest_resume_deactivates_previous_and_bumps_version(
    db_session: Session, fake_embedding_provider: FakeEmbeddingProvider
) -> None:
    profile_service.ingest_resume(
        db_session,
        filename="cv_v1.docx",
        content=_docx_bytes("Version one"),
        embedding_provider=fake_embedding_provider,
    )
    second = profile_service.ingest_resume(
        db_session,
        filename="cv_v2.docx",
        content=_docx_bytes("Version two"),
        embedding_provider=fake_embedding_provider,
    )

    active = profile_service.get_active_profile(db_session)
    assert active is not None
    assert active.id == second.id
    assert second.version == 2

    all_profiles = profile_service.list_profiles(db_session)
    assert len(all_profiles) == 2
    assert sum(p.is_active for p in all_profiles) == 1


def test_reuploading_identical_content_reactivates_instead_of_duplicating(
    db_session: Session, fake_embedding_provider: FakeEmbeddingProvider
) -> None:
    content = _docx_bytes("Same content")
    first = profile_service.ingest_resume(
        db_session, filename="cv.docx", content=content, embedding_provider=fake_embedding_provider
    )
    profile_service.ingest_resume(
        db_session,
        filename="cv_v2.docx",
        content=_docx_bytes("Different"),
        embedding_provider=fake_embedding_provider,
    )

    reactivated = profile_service.ingest_resume(
        db_session, filename="cv.docx", content=content, embedding_provider=fake_embedding_provider
    )

    assert reactivated.id == first.id
    assert reactivated.is_active is True
    assert len(profile_service.list_profiles(db_session)) == 2


def test_activate_profile_switches_active_flag(
    db_session: Session, fake_embedding_provider: FakeEmbeddingProvider
) -> None:
    first = profile_service.ingest_resume(
        db_session,
        filename="cv_v1.docx",
        content=_docx_bytes("One"),
        embedding_provider=fake_embedding_provider,
    )
    profile_service.ingest_resume(
        db_session,
        filename="cv_v2.docx",
        content=_docx_bytes("Two"),
        embedding_provider=fake_embedding_provider,
    )

    reactivated = profile_service.activate_profile(db_session, first.id)

    assert reactivated.is_active is True
    active = profile_service.get_active_profile(db_session)
    assert active is not None
    assert active.id == first.id


def test_activate_profile_raises_for_missing_id(db_session: Session) -> None:
    with pytest.raises(profile_service.ProfileNotFoundError):
        profile_service.activate_profile(db_session, 999_999)


@pytest.mark.parametrize(
    "error",
    [
        OllamaTimeoutError(timeout_seconds=120),
        OllamaUnavailableError("http://localhost:11434", RuntimeError("refused")),
        OpenAINotConfiguredError(),
    ],
)
def test_ingest_resume_saves_successfully_when_structured_extraction_fails(
    db_session: Session,
    fake_embedding_provider: FakeEmbeddingProvider,
    monkeypatch: pytest.MonkeyPatch,
    error: Exception,
) -> None:
    """The resume itself (raw text + embedding) must never be lost just
    because the LLM step is slow/unavailable - matching must not depend
    entirely on structured data. Observed for real on this dev
    machine: CPU-only Ollama inference can take minutes to hours.
    """

    def _raise(text: str) -> StructuredCandidateProfile:
        raise error

    monkeypatch.setattr(profile_service, "extract_structured_profile", _raise)

    profile = profile_service.ingest_resume(
        db_session,
        filename="cv.docx",
        content=_docx_bytes("Jane Doe - Junior Software Engineer"),
        embedding_provider=fake_embedding_provider,
    )
    profile_service.finish_profile_processing(db_session, profile.id)

    db_session.refresh(profile)
    assert profile.is_active is True
    assert profile.processing_status == profile_service.PROCESSING_DONE
    assert profile.processing_note is not None
    assert type(error).__name__ in profile.processing_note
    # The model's lists are empty, but the rules still decided the level.
    assert profile.structured_profile["programming_languages"] == []
    assert profile.structured_profile["seniority"] == "junior"
    assert profile.structured_profile["years_of_experience"] == 0.0
    assert "Jane Doe" in profile.raw_text
    assert profile.embedding is not None

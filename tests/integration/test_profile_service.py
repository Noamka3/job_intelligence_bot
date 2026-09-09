from __future__ import annotations

import io
from pathlib import Path

import pytest
from docx import Document
from sqlalchemy.orm import Session

from app.schemas.candidate import StructuredCandidateProfile
from app.services.candidate import profile_service
from tests.conftest import FakeEmbeddingProvider


@pytest.fixture(autouse=True)
def _no_real_llm_extraction(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Every test in this module ingests resumes; none should hit OpenAI or
    write into the real data/resumes directory.
    """
    monkeypatch.setattr(
        profile_service,
        "extract_structured_profile",
        lambda text: StructuredCandidateProfile(seniority="junior"),
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

    assert profile.is_active is True
    assert profile.version == 1
    assert profile.structured_profile["seniority"] == "junior"
    assert "Jane Doe" in profile.raw_text


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

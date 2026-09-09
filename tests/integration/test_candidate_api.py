from __future__ import annotations

import io
from pathlib import Path

import pytest
from docx import Document
from fastapi.testclient import TestClient

from app.schemas.candidate import StructuredCandidateProfile
from app.services.candidate import profile_service


@pytest.fixture(autouse=True)
def _no_real_llm_extraction(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
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


def test_upload_resume_then_fetch_active(api_client: TestClient) -> None:
    content = _docx_bytes("Jane Doe - Junior Software Engineer")

    upload_response = api_client.post(
        "/candidate/resume",
        files={
            "file": (
                "cv.docx",
                content,
                "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            )
        },
    )
    assert upload_response.status_code == 201
    body = upload_response.json()
    assert body["is_active"] is True

    active_response = api_client.get("/candidate/active")
    assert active_response.status_code == 200
    assert active_response.json()["id"] == body["id"]


def test_upload_resume_rejects_unsupported_extension(api_client: TestClient) -> None:
    response = api_client.post(
        "/candidate/resume", files={"file": ("cv.txt", b"plain text", "text/plain")}
    )
    assert response.status_code == 400


def test_active_profile_returns_404_when_none_exists(api_client: TestClient) -> None:
    response = api_client.get("/candidate/active")
    assert response.status_code == 404

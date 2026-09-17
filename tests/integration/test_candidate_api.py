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
    # The response itself is sent before extraction/rescoring...
    assert body["processing_status"] == "pending"

    active_response = api_client.get("/candidate/active")
    assert active_response.status_code == 200
    assert active_response.json()["id"] == body["id"]
    # ...and the TestClient runs the background task before returning,
    # so by now the profile has been fully processed.
    assert active_response.json()["processing_status"] == "done"
    assert active_response.json()["structured_profile"]["seniority"] == "junior"


def test_upload_resume_rejects_unsupported_extension(api_client: TestClient) -> None:
    response = api_client.post(
        "/candidate/resume", files={"file": ("cv.txt", b"plain text", "text/plain")}
    )
    assert response.status_code == 400


def test_upload_rejects_a_corrupt_pdf_with_400_not_500(api_client: TestClient) -> None:
    response = api_client.post(
        "/candidate/resume",
        files={"file": ("cv.pdf", b"this is not a pdf at all", "application/pdf")},
    )
    assert response.status_code == 400
    assert "Could not read" in response.json()["detail"]


def test_upload_rejects_a_pdf_with_no_extractable_text(api_client: TestClient) -> None:
    """An image-only/blank PDF used to be accepted and *activated*,
    silently replacing the working profile with one embedded from ""."""
    from pypdf import PdfWriter

    writer = PdfWriter()
    writer.add_blank_page(width=200, height=200)
    buffer = io.BytesIO()
    writer.write(buffer)

    response = api_client.post(
        "/candidate/resume", files={"file": ("scan.pdf", buffer.getvalue(), "application/pdf")}
    )
    assert response.status_code == 400
    assert "No text could be extracted" in response.json()["detail"]
    assert api_client.get("/candidate/active").status_code == 404


def test_upload_uses_only_the_basename_of_a_client_supplied_path(api_client: TestClient) -> None:
    content = _docx_bytes("Jane Doe - Junior Software Engineer")
    client_path = "C:\\Users\\jane\\Documents\\cv.docx"
    response = api_client.post(
        "/candidate/resume", files={"file": (client_path, content, "application/octet-stream")}
    )
    assert response.status_code == 201
    assert response.json()["filename"] == "cv.docx"


def test_profile_text_exposes_what_was_read_from_the_file(api_client: TestClient) -> None:
    content = _docx_bytes("Jane Doe - Junior Software Engineer\nPython, Docker")
    profile_id = api_client.post(
        "/candidate/resume", files={"file": ("cv.docx", content, "application/octet-stream")}
    ).json()["id"]

    response = api_client.get(f"/candidate/profiles/{profile_id}/text")

    assert response.status_code == 200
    assert "Python, Docker" in response.json()["raw_text"]
    assert response.json()["normalized_text"]
    assert api_client.get("/candidate/profiles/999999/text").status_code == 404


def test_active_profile_returns_404_when_none_exists(api_client: TestClient) -> None:
    response = api_client.get("/candidate/active")
    assert response.status_code == 404

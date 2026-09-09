"""Raw text extraction from an uploaded resume file (PDF or DOCX)."""

from __future__ import annotations

import io

from docx import Document
from pypdf import PdfReader


class UnsupportedResumeFormatError(ValueError):
    def __init__(self, filename: str) -> None:
        super().__init__(f"Unsupported resume format: {filename!r} (expected .pdf or .docx)")


def extract_text(filename: str, content: bytes) -> str:
    suffix = filename.rsplit(".", maxsplit=1)[-1].lower() if "." in filename else ""
    if suffix == "pdf":
        return _extract_pdf_text(content)
    if suffix == "docx":
        return _extract_docx_text(content)
    raise UnsupportedResumeFormatError(filename)


def _extract_pdf_text(content: bytes) -> str:
    reader = PdfReader(io.BytesIO(content))
    pages = [page.extract_text() or "" for page in reader.pages]
    return "\n".join(pages)


def _extract_docx_text(content: bytes) -> str:
    document = Document(io.BytesIO(content))
    parts = [paragraph.text for paragraph in document.paragraphs]
    for table in document.tables:
        for row in table.rows:
            parts.extend(cell.text for cell in row.cells)
    return "\n".join(parts)

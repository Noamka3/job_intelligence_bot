"""Generic whitespace/control-character text normalization, shared by
resume text (app/services/candidate) and job description text
(app/services/jobs). Deterministic - no AI involved.
"""

from __future__ import annotations

import re

_WHITESPACE_RUN = re.compile(r"[ \t ]+")
_BLANK_LINE_RUN = re.compile(r"\n{3,}")
_CONTROL_CHARS = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


def normalize_whitespace(raw_text: str) -> str:
    text = _CONTROL_CHARS.sub("", raw_text)
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = _WHITESPACE_RUN.sub(" ", text)
    lines = [line.strip() for line in text.split("\n")]
    text = "\n".join(lines)
    text = _BLANK_LINE_RUN.sub("\n\n", text)
    return text.strip()

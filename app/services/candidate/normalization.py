"""Resume-text normalization. Thin re-export: the actual whitespace/
control-character cleanup is generic (also used for job descriptions) and
lives in app.services.text_normalization.
"""

from __future__ import annotations

from app.services.text_normalization import normalize_whitespace as normalize_text

__all__ = ["normalize_text"]

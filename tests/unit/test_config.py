from __future__ import annotations

import pytest

from app.core.config import Settings


def test_settings_defaults_are_sane() -> None:
    settings = Settings(_env_file=None)
    assert settings.embedding_provider == "local"
    assert settings.embedding_dimensions == 384
    assert settings.embedding_model == "text-embedding-3-large"
    assert settings.default_timezone == "Asia/Jerusalem"
    # The sheet is the owner's own, so its id has no default and must
    # come from .env - see config.py.
    assert settings.google_sheet_id == ""
    assert settings.google_sheet_gid == 0
    assert 0 <= settings.notification_score_threshold <= 100


def test_settings_reads_env_override(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("NOTIFICATION_SCORE_THRESHOLD", "90")
    settings = Settings(_env_file=None)
    assert settings.notification_score_threshold == 90

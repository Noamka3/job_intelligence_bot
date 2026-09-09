from __future__ import annotations

from app.core.config import Settings


def test_settings_defaults_are_sane() -> None:
    settings = Settings(_env_file=None)  # type: ignore[call-arg]
    assert settings.embedding_dimensions == 1024
    assert settings.embedding_model == "text-embedding-3-large"
    assert settings.default_timezone == "Asia/Jerusalem"
    assert settings.google_sheet_gid == 168609393
    assert 0 <= settings.notification_score_threshold <= 100


def test_settings_reads_env_override(monkeypatch) -> None:
    monkeypatch.setenv("NOTIFICATION_SCORE_THRESHOLD", "90")
    settings = Settings(_env_file=None)  # type: ignore[call-arg]
    assert settings.notification_score_threshold == 90

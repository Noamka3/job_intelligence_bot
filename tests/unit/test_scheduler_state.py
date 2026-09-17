from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from app.core.config import get_settings
from app.services import scheduler_state


def test_next_dispatch_is_one_poll_interval_after_the_last(monkeypatch: pytest.MonkeyPatch) -> None:
    last = datetime(2026, 9, 16, 12, 0, tzinfo=UTC)
    monkeypatch.setattr(scheduler_state, "last_dispatch_at", lambda: last)
    monkeypatch.setattr(get_settings(), "default_poll_minutes", 5)

    assert scheduler_state.next_dispatch_at() == last + timedelta(minutes=5)


def test_next_dispatch_is_none_before_the_first_tick(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(scheduler_state, "last_dispatch_at", lambda: None)
    assert scheduler_state.next_dispatch_at() is None


def test_unreachable_redis_degrades_to_none(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(get_settings(), "redis_url", "redis://127.0.0.1:1/0")
    assert scheduler_state.last_dispatch_at() is None
    assert scheduler_state.crawl_queue_depth() is None
    scheduler_state.record_dispatch_tick()  # logs, never raises

from __future__ import annotations

from datetime import UTC, datetime

from app.ingestion.adapters._util import parse_timestamp


def test_parse_timestamp_always_returns_aware_datetimes() -> None:
    for value in ("2024-01-10", "2024-01-10T10:00:00", "2024-01-10T10:00:00Z", 1700000000):
        parsed = parse_timestamp(value)
        assert parsed is not None, value
        assert parsed.tzinfo is not None, value


def test_parse_timestamp_offsetless_values_are_read_as_utc() -> None:
    assert parse_timestamp("2024-01-10T10:00:00") == datetime(2024, 1, 10, 10, 0, tzinfo=UTC)


def test_parse_timestamp_keeps_an_explicit_offset() -> None:
    parsed = parse_timestamp("2024-01-10T10:00:00+02:00")
    assert parsed is not None
    assert parsed.utcoffset() is not None
    assert parsed.utcoffset().total_seconds() == 2 * 3600  # type: ignore[union-attr]


def test_parse_timestamp_garbage_is_none_not_an_exception() -> None:
    assert parse_timestamp("not a date") is None
    assert parse_timestamp("") is None
    assert parse_timestamp(None) is None

from __future__ import annotations

from typing import Any

import pytest

from app.services.sheets import reader
from app.services.sheets.reader import SheetTabNotFoundError, read_company_rows, resolve_sheet_title


class _FakeExecutable:
    def __init__(self, result: dict[str, Any]) -> None:
        self._result = result

    def execute(self) -> dict[str, Any]:
        return self._result


class _FakeValues:
    def __init__(self, rows: list[list[str]]) -> None:
        self._rows = rows

    def get(self, spreadsheetId: str, range: str) -> _FakeExecutable:
        return _FakeExecutable({"values": self._rows})


class _FakeSpreadsheets:
    def __init__(self, metadata: dict[str, Any], rows: list[list[str]]) -> None:
        self._metadata = metadata
        self._values = _FakeValues(rows)

    def get(self, spreadsheetId: str, fields: str) -> _FakeExecutable:
        return _FakeExecutable(self._metadata)

    def values(self) -> _FakeValues:
        return self._values


class _FakeService:
    def __init__(self, metadata: dict[str, Any], rows: list[list[str]]) -> None:
        self._spreadsheets = _FakeSpreadsheets(metadata, rows)

    def spreadsheets(self) -> _FakeSpreadsheets:
        return self._spreadsheets


_METADATA = {
    "sheets": [
        {"properties": {"sheetId": 111, "title": "Other Tab"}},
        {"properties": {"sheetId": 168609393, "title": "Hiring_Partners_Elevation_links"}},
    ]
}


def test_resolve_sheet_title_finds_tab_by_gid(monkeypatch: pytest.MonkeyPatch) -> None:
    fake_service = _FakeService(_METADATA, rows=[])
    monkeypatch.setattr(reader, "get_sheets_service", lambda: fake_service)

    title = resolve_sheet_title("sheet-id", 168609393)

    assert title == "Hiring_Partners_Elevation_links"


def test_resolve_sheet_title_raises_when_gid_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    fake_service = _FakeService(_METADATA, rows=[])
    monkeypatch.setattr(reader, "get_sheets_service", lambda: fake_service)

    with pytest.raises(SheetTabNotFoundError):
        resolve_sheet_title("sheet-id", 999)


def test_read_company_rows_skips_blank_and_header_rows(monkeypatch: pytest.MonkeyPatch) -> None:
    rows = [
        ["Company Name", "Link"],
        ["Ness", "https://www.ness-tech.co.il/careers/"],
        ["", ""],
        ["Meta"],
        ["Cambium", "https://cambium.co.il/careers"],
    ]
    fake_service = _FakeService(_METADATA, rows)
    monkeypatch.setattr(reader, "get_sheets_service", lambda: fake_service)

    result = read_company_rows()

    assert [row.name for row in result] == ["Ness", "Meta", "Cambium"]
    assert result[1].url is None
    assert result[0].url == "https://www.ness-tech.co.il/careers/"

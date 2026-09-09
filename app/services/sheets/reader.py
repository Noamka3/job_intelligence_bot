"""Reads company rows from the configured Google Sheet.

The sheet tab is resolved by gid (its stable sheetId), never by an
assumed tab name/title - per spec §2/§38, the title can change without
warning.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from app.core.config import get_settings
from app.services.sheets.client import get_sheets_service

logger = logging.getLogger(__name__)

# Defensive only: this tab currently has no header row, but if one is ever
# added, skip it rather than ingesting it as a fake "company".
_HEADER_LABELS = {
    "company name",
    "company",
    "companies",
    "name",
    "link",
    "url",
    "שם חברה",
    "שם",
    "קישור",
}


@dataclass(frozen=True)
class CompanySheetRow:
    name: str
    url: str | None


class SheetTabNotFoundError(LookupError):
    def __init__(self, spreadsheet_id: str, gid: int) -> None:
        super().__init__(f"No sheet tab with gid={gid} found in spreadsheet {spreadsheet_id}")


def resolve_sheet_title(spreadsheet_id: str, gid: int) -> str:
    service = get_sheets_service()
    metadata = (
        service.spreadsheets()
        .get(spreadsheetId=spreadsheet_id, fields="sheets.properties")
        .execute()
    )
    for sheet in metadata.get("sheets", []):
        properties = sheet.get("properties", {})
        if properties.get("sheetId") == gid:
            title: str = properties["title"]
            return title
    raise SheetTabNotFoundError(spreadsheet_id, gid)


def read_company_rows() -> list[CompanySheetRow]:
    settings = get_settings()
    service = get_sheets_service()
    title = resolve_sheet_title(settings.google_sheet_id, settings.google_sheet_gid)

    result = (
        service.spreadsheets()
        .values()
        .get(spreadsheetId=settings.google_sheet_id, range=f"'{title}'!A:B")
        .execute()
    )
    raw_rows: list[list[str]] = result.get("values", [])

    rows: list[CompanySheetRow] = []
    for raw_row in raw_rows:
        name = raw_row[0].strip() if len(raw_row) > 0 else ""
        url = raw_row[1].strip() if len(raw_row) > 1 else ""

        if not name and not url:
            continue
        if name.lower() in _HEADER_LABELS:
            continue

        rows.append(CompanySheetRow(name=name, url=url or None))

    logger.info(
        "read company rows from sheet", extra={"row_count": len(rows), "sheet_title": title}
    )
    return rows

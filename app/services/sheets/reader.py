"""Reads company rows from the configured Google Sheet.

The sheet tab is resolved by gid (its stable sheetId), never by an
assumed tab name/title - the title can change without
warning.
"""

from __future__ import annotations

import logging

from app.core.config import get_settings
from app.services.sheets.client import get_sheets_service
from app.services.sheets.row_parsing import CompanySheetRow, parse_company_rows

logger = logging.getLogger(__name__)

__all__ = ["CompanySheetRow", "SheetTabNotFoundError", "read_company_rows", "resolve_sheet_title"]


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

    # A1 notation quotes the tab title; a literal ' inside it is escaped
    # by doubling, as in SQL.
    quoted_title = "'" + title.replace("'", "''") + "'"
    result = (
        service.spreadsheets()
        .values()
        .get(spreadsheetId=settings.google_sheet_id, range=f"{quoted_title}!A:B")
        .execute()
    )
    raw_rows: list[list[str]] = result.get("values", [])
    rows = parse_company_rows(raw_rows)

    logger.info(
        "read company rows from sheet", extra={"row_count": len(rows), "sheet_title": title}
    )
    return rows

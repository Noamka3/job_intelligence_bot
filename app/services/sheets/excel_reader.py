"""Reads company rows from a local .xlsx export of the company sheet -
an alternative to the live Google Sheets API sync (reader.py) for when a
Google service account isn't set up, or as a one-off import.
"""

from __future__ import annotations

import logging
from pathlib import Path

import openpyxl

from app.services.sheets.row_parsing import CompanySheetRow, parse_company_rows

logger = logging.getLogger(__name__)


def read_company_rows_from_excel(file_path: str | Path) -> list[CompanySheetRow]:
    workbook = openpyxl.load_workbook(file_path, read_only=True, data_only=True)
    try:
        sheet = workbook.active
        if sheet is None:
            raise ValueError(f"{file_path} has no active worksheet")
        raw_rows = list(sheet.iter_rows(values_only=True))
    finally:
        workbook.close()

    rows = parse_company_rows(raw_rows)
    logger.info(
        "read company rows from excel file",
        extra={"row_count": len(rows), "file_path": str(file_path)},
    )
    return rows

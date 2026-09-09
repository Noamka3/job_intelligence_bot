from __future__ import annotations

from pathlib import Path
from typing import Any

import openpyxl

from app.services.sheets.excel_reader import read_company_rows_from_excel


def _write_workbook(path: Path, rows: list[tuple[Any, ...]]) -> None:
    workbook = openpyxl.Workbook()
    sheet = workbook.active
    assert sheet is not None
    for row in rows:
        sheet.append(row)
    workbook.save(path)


def test_reads_real_file_shape_skipping_title_and_header_rows(tmp_path: Path) -> None:
    path = tmp_path / "companies.xlsx"
    _write_workbook(
        path,
        [
            ("Hiring Partners Elevation", None),
            ("Full Name", "Link", "Location"),
            ("Ness", "https://www.ness-tech.co.il/careers/"),
            ("Cambium", "https://cambium.co.il/careers", "Negev"),
            (None, None, None),
            ("Meta", None),
        ],
    )

    rows = read_company_rows_from_excel(path)

    assert [r.name for r in rows] == ["Ness", "Cambium", "Meta"]
    assert rows[0].url == "https://www.ness-tech.co.il/careers/"
    assert rows[2].url is None


def test_accepts_string_path(tmp_path: Path) -> None:
    path = tmp_path / "companies.xlsx"
    _write_workbook(path, [("Acme", "https://acme.example/careers")])

    rows = read_company_rows_from_excel(str(path))

    assert rows == [read_company_rows_from_excel(path)[0]]

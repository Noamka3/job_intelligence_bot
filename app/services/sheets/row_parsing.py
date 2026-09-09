"""Shared logic for turning raw (name, url, ...) rows - from the Google
Sheets API or a local spreadsheet file export - into CompanySheetRow
objects. Same filtering rules regardless of where the rows came from.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

# Known non-company rows, taken from the real sheet/export seen during
# development: a title row ("Hiring Partners Elevation") and a header row
# ("Full Name" / "Link" / "Location"). Kept as an explicit label set
# (rather than skipping the first N rows positionally) so this keeps
# working even if row order ever changes.
_SKIP_LABELS = {
    "company name",
    "company",
    "companies",
    "full name",
    "name",
    "link",
    "url",
    "hiring partners elevation",
    "שם חברה",
    "שם",
    "קישור",
}


@dataclass(frozen=True)
class CompanySheetRow:
    name: str
    url: str | None


def parse_company_rows(raw_rows: Sequence[Sequence[object]]) -> list[CompanySheetRow]:
    rows: list[CompanySheetRow] = []
    for raw_row in raw_rows:
        name = str(raw_row[0]).strip() if len(raw_row) > 0 and raw_row[0] is not None else ""
        url = str(raw_row[1]).strip() if len(raw_row) > 1 and raw_row[1] is not None else ""

        if not name and not url:
            continue
        if name.lower() in _SKIP_LABELS:
            continue

        rows.append(CompanySheetRow(name=name, url=url or None))
    return rows

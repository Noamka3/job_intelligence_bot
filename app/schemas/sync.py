from __future__ import annotations

from pydantic import BaseModel


class SheetSyncResult(BaseModel):
    companies_seen: int
    companies_created: int
    companies_updated: int
    companies_disabled: int
    sources_created: int

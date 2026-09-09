from __future__ import annotations

from pydantic import BaseModel


class CrawlNowResult(BaseModel):
    sources_attempted: int
    succeeded: int
    failed: int

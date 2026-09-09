from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.schemas.sync import SheetSyncResult
from app.services.sheets.company_sync import sync_companies_from_sheet

router = APIRouter(prefix="/sync", tags=["sync"])
logger = logging.getLogger(__name__)


@router.post("/google-sheet", response_model=SheetSyncResult)
def sync_google_sheet(db: Session = Depends(get_db)) -> SheetSyncResult:
    try:
        return sync_companies_from_sheet(db)
    except Exception as exc:  # noqa: BLE001 - any Sheets API/auth failure becomes a clean 502
        logger.warning("google sheet sync failed", extra={"error_type": type(exc).__name__})
        raise HTTPException(
            status.HTTP_502_BAD_GATEWAY, f"Google Sheet sync failed: {exc}"
        ) from exc

from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.schemas.dashboard import DashboardStatsRead
from app.services.dashboard import load_dashboard_stats

router = APIRouter(prefix="/dashboard", tags=["dashboard"])


@router.get("/stats", response_model=DashboardStatsRead)
def dashboard_stats(db: Session = Depends(get_db)) -> DashboardStatsRead:
    return DashboardStatsRead.model_validate(load_dashboard_stats(db))

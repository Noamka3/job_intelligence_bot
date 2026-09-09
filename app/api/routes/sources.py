from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.models.career_source import CareerSource
from app.models.enums import CareerSourceType
from app.schemas.career_source import CareerSourceRead

router = APIRouter(prefix="/sources", tags=["sources"])


@router.get("", response_model=list[CareerSourceRead])
def list_sources(
    source_type: CareerSourceType | None = None,
    enabled: bool | None = None,
    company_id: int | None = None,
    db: Session = Depends(get_db),
) -> list[CareerSourceRead]:
    query = select(CareerSource).order_by(CareerSource.id)
    if source_type is not None:
        query = query.where(CareerSource.source_type == source_type)
    if enabled is not None:
        query = query.where(CareerSource.enabled == enabled)
    if company_id is not None:
        query = query.where(CareerSource.company_id == company_id)
    sources = db.execute(query).scalars()
    return [CareerSourceRead.model_validate(source) for source in sources]

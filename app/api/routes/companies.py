from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.models.company import Company
from app.schemas.company import CompanyRead

router = APIRouter(prefix="/companies", tags=["companies"])


@router.get("", response_model=list[CompanyRead])
def list_companies(enabled: bool | None = None, db: Session = Depends(get_db)) -> list[CompanyRead]:
    query = select(Company).order_by(Company.name)
    if enabled is not None:
        query = query.where(Company.enabled == enabled)
    companies = db.execute(query).scalars()
    return [CompanyRead.model_validate(company) for company in companies]

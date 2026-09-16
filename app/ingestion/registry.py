from __future__ import annotations

from app.ingestion.adapters.ashby import AshbyAdapter
from app.ingestion.adapters.base import JobSourceAdapter
from app.ingestion.adapters.comeet import ComeetAdapter
from app.ingestion.adapters.generic_html import GenericHtmlAdapter
from app.ingestion.adapters.greenhouse import GreenhouseAdapter
from app.ingestion.adapters.jsonld import JsonLdAdapter
from app.ingestion.adapters.lever import LeverAdapter
from app.ingestion.adapters.smartrecruiters import SmartRecruitersAdapter
from app.ingestion.adapters.taleo import TaleoAdapter
from app.ingestion.adapters.workable import WorkableAdapter
from app.ingestion.adapters.workday import WorkdayAdapter
from app.models.enums import CareerSourceType

# PLAYWRIGHT is deliberately absent: no browser-based adapter exists yet,
# and get_due_sources skips source types that aren't registered here
# (see app/services/jobs/ingestion.py).
_ADAPTERS: dict[CareerSourceType, JobSourceAdapter] = {
    CareerSourceType.GREENHOUSE: GreenhouseAdapter(),
    CareerSourceType.LEVER: LeverAdapter(),
    CareerSourceType.ASHBY: AshbyAdapter(),
    CareerSourceType.SMARTRECRUITERS: SmartRecruitersAdapter(),
    CareerSourceType.WORKABLE: WorkableAdapter(),
    CareerSourceType.COMEET: ComeetAdapter(),
    CareerSourceType.WORKDAY: WorkdayAdapter(),
    CareerSourceType.TALEO: TaleoAdapter(),
    CareerSourceType.JSONLD: JsonLdAdapter(),
    CareerSourceType.GENERIC_HTML: GenericHtmlAdapter(),
}


def get_adapter(source_type: CareerSourceType) -> JobSourceAdapter | None:
    return _ADAPTERS.get(source_type)


def supported_source_types() -> list[CareerSourceType]:
    return list(_ADAPTERS)

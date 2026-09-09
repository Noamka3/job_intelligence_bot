from __future__ import annotations

from app.ingestion.adapters.ashby import AshbyAdapter
from app.ingestion.adapters.base import JobSourceAdapter
from app.ingestion.adapters.comeet import ComeetAdapter
from app.ingestion.adapters.greenhouse import GreenhouseAdapter
from app.ingestion.adapters.jsonld import JsonLdAdapter
from app.ingestion.adapters.lever import LeverAdapter
from app.models.enums import CareerSourceType

_ADAPTERS: dict[CareerSourceType, JobSourceAdapter] = {
    CareerSourceType.GREENHOUSE: GreenhouseAdapter(),
    CareerSourceType.LEVER: LeverAdapter(),
    CareerSourceType.ASHBY: AshbyAdapter(),
    CareerSourceType.COMEET: ComeetAdapter(),
    CareerSourceType.JSONLD: JsonLdAdapter(),
}


def get_adapter(source_type: CareerSourceType) -> JobSourceAdapter | None:
    return _ADAPTERS.get(source_type)

"""Location component score (spec §8/§28). Reuses the same Israel
classifier job ingestion already writes into JobPosting.country, so the
"why" behind a location score is always explainable from data the job
actually has.
"""

from __future__ import annotations

from app.models.enums import RemoteType
from app.services.jobs.location import is_confidently_non_israeli


def score_location(
    country: str | None, location_text: str | None, remote_type: RemoteType
) -> tuple[float, str]:
    if remote_type == RemoteType.REMOTE:
        return 1.0, "remote position"
    if country == "Israel":
        return 1.0, "located in Israel"
    if is_confidently_non_israeli(location_text):
        return 0.1, f"located outside Israel ({location_text})"
    return 0.5, "location unclear from the posting"

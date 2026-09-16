"""Common adapter interface + DTOs. The ingestion pipeline
(app/services/jobs/ingestion.py) only ever talks to this shape - it never
knows whether a job came from Greenhouse, Lever, Ashby, Comeet, or a
JSON-LD page.

Deliberately smaller than the spec's illustrative sketch in two ways:
- No `can_handle`: dispatch is a straight CareerSourceType -> adapter
  lookup in the registry (app/ingestion/registry.py), so there's nothing
  for an adapter to introspect.
- No `normalize` on the adapter: turning JobDetails into normalized text
  + a content hash is identical regardless of which ATS produced the
  JobDetails, so it lives once in app/services/jobs/normalization.py
  instead of being duplicated per adapter.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Protocol

from pydantic import BaseModel, ConfigDict

from app.models.career_source import CareerSource
from app.models.enums import EmploymentType, RemoteType


class JobStub(BaseModel):
    """Cheap, list-call-only representation of a job - just enough to
    detect new / changed / missing jobs without fetching full details for
    everything on every crawl (spec §20).
    """

    model_config = ConfigDict(arbitrary_types_allowed=True)

    external_job_id: str
    title: str
    location_text: str | None = None
    source_url: str
    apply_url: str | None = None
    source_updated_at: datetime | None = None
    # Set when the adapter's list call already returned full detail
    # (Lever/Ashby/Comeet all do) so fetch_job can reuse it instead of a
    # second, redundant network round-trip.
    raw: dict[str, Any] | None = None


class JobDetails(BaseModel):
    """Full, ATS-agnostic representation of one job."""

    external_job_id: str
    title: str
    department: str | None = None
    team: str | None = None
    location_text: str | None = None
    remote_type: RemoteType = RemoteType.UNKNOWN
    employment_type: EmploymentType = EmploymentType.UNKNOWN
    description: str | None = None
    responsibilities: str | None = None
    qualifications: str | None = None
    required_skills: list[str] = []
    preferred_skills: list[str] = []
    source_url: str
    apply_url: str | None = None
    source_published_at: datetime | None = None
    source_updated_at: datetime | None = None


class JobUnavailableError(LookupError):
    """Raised by fetch_job when the source itself says the stub is not an
    open job: the page is gone (404/410) or turns out to be a listing/
    category page rather than a posting. Distinct from a transient failure
    - the crawler closes an existing row on it instead of retrying, and
    skips a new one without counting it as an error.
    """

    def __init__(self, url: str, reason: str) -> None:
        super().__init__(f"{reason}: {url}")
        self.reason = reason


class JobSourceAdapter(Protocol):
    def list_jobs(self, source: CareerSource) -> list[JobStub]: ...

    def fetch_job(self, source: CareerSource, stub: JobStub) -> JobDetails: ...

"""Common adapter interface + DTOs. The ingestion pipeline
(app/services/jobs/ingestion.py) only ever talks to this shape - it never
knows whether a job came from Greenhouse, Lever, Ashby, Comeet, or a
JSON-LD page.

Deliberately smaller than the usual adapter interface in two ways:
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
from typing import Annotated, Any, Protocol
from urllib.parse import urlparse

from pydantic import BaseModel, BeforeValidator, ConfigDict

from app.models.career_source import CareerSource
from app.models.enums import EmploymentType, RemoteType


def _drop_unsafe_scheme(value: Any) -> Any:
    """Apply links are read off third-party pages and feeds, and the
    dashboard renders them as real links. A "javascript:" or "data:" one
    would run in the dashboard's own origin, so it is dropped here rather
    than stored. Anything that isn't an absolute http(s) URL (a relative
    path, a blank) is left alone: harmless, and the UI decides whether it
    can be linked (frontend/src/lib/format.ts externalHref)."""
    if isinstance(value, str) and ":" in value:
        scheme = urlparse(value).scheme.lower()
        if scheme and scheme not in ("http", "https"):
            return None
    return value


# An optional URL that will end up in an href.
LinkUrl = Annotated[str | None, BeforeValidator(_drop_unsafe_scheme)]


class JobStub(BaseModel):
    """Cheap, list-call-only representation of a job - just enough to
    detect new / changed / missing jobs without fetching full details for
    everything on every crawl.
    """

    model_config = ConfigDict(arbitrary_types_allowed=True)

    external_job_id: str
    title: str
    location_text: str | None = None
    source_url: str
    apply_url: LinkUrl = None
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
    apply_url: LinkUrl = None
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


class BoardBehindPage(Exception):
    """Raised by list_jobs when the page turns out to be a front for a
    known ATS board (the browser saw the page call Comeet's or
    Greenhouse's API). The crawler re-points the source at that board,
    whose adapter reads it properly, instead of scraping a rendering."""

    def __init__(self, board: Any) -> None:  # a resolver.ResolvedSource
        super().__init__(f"page is a front for {board.source_type.value}")
        self.board = board


class JobSourceAdapter(Protocol):
    def list_jobs(self, source: CareerSource) -> list[JobStub]: ...

    def fetch_job(self, source: CareerSource, stub: JobStub) -> JobDetails: ...

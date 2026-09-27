"""Ten days after a posting appeared it is outside the dashboard's widest
window, and from then on its text and vector only take space. This trims
the database to what the windows can show, while keeping what the
crawler needs to know that a link is not new:

- An ACTIVE posting past the window is *archived*: its text, embedding,
  Jev reading and matches go; its identity (source, external id, title,
  dates, content hash) stays, so the next crawl recognises the link
  instead of importing it again as "found today". A listing that later
  reports the posting updated brings the text back (ingestion.py).
- A CLOSED posting past the window is deleted outright.
- A posting with feedback or an application against it is never
  touched, however old it is.

Runs nightly (app/tasks/retention.py) and on demand (`prune` in the CLI).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy import delete, func, select, update
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.timezone import utc_now
from app.models.application import Application
from app.models.enums import JobStatus
from app.models.job_feedback import JobFeedback
from app.models.job_match import JobMatch
from app.models.job_posting import JobPosting


@dataclass(frozen=True)
class PruneResult:
    archived: int
    deleted: int


def prune_postings(db: Session, *, now: datetime | None = None) -> PruneResult:
    now = now or utc_now()
    cutoff = now - timedelta(days=get_settings().job_retention_days)
    # Out of every window - neither found nor published inside it, since
    # the dashboard shows a posting when either date is recent - and
    # carrying no feedback or application.
    prunable = (
        JobPosting.first_seen_at < cutoff,
        func.coalesce(JobPosting.source_published_at, JobPosting.first_seen_at) < cutoff,
        JobPosting.id.not_in(select(JobFeedback.job_id)),
        JobPosting.id.not_in(select(Application.job_id)),
    )

    to_archive = db.scalars(
        select(JobPosting.id).where(
            JobPosting.status == JobStatus.ACTIVE, JobPosting.archived_at.is_(None), *prunable
        )
    ).all()
    db.execute(delete(JobMatch).where(JobMatch.job_id.in_(to_archive)))
    db.execute(
        update(JobPosting)
        .where(JobPosting.id.in_(to_archive))
        .values(
            description=None,
            normalized_description=None,
            responsibilities=None,
            qualifications=None,
            embedding=None,
            jev_reading=None,
            archived_at=now,
        )
    )

    to_delete = db.scalars(
        select(JobPosting.id).where(JobPosting.status == JobStatus.CLOSED, *prunable)
    ).all()
    db.execute(delete(JobPosting).where(JobPosting.id.in_(to_delete)))
    db.commit()
    return PruneResult(archived=len(to_archive), deleted=len(to_delete))

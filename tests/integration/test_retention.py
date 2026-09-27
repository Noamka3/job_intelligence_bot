from __future__ import annotations

from datetime import timedelta

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.timezone import utc_now
from app.ingestion.adapters.base import JobStub
from app.models.candidate_profile import CandidateProfile
from app.models.constants import EMBEDDING_DIM
from app.models.enums import JobFeedbackAction, JobStatus
from app.models.job_feedback import JobFeedback
from app.models.job_match import JobMatch
from app.models.job_posting import JobPosting
from app.models.target_role import TargetRole
from app.services.jobs import ingestion
from app.services.jobs.retention import prune_postings
from tests.conftest import FakeEmbeddingProvider
from tests.integration.test_ingestion import _details, _FakeAdapter, _make_source


def _stored(
    db: Session,
    source_id: int,
    company_id: int,
    external_id: str,
    *,
    days_ago: int,
    status: JobStatus,
) -> JobPosting:
    job = JobPosting(
        company_id=company_id,
        career_source_id=source_id,
        external_job_id=external_id,
        title="Junior Engineer",
        normalized_title="junior engineer",
        description="Build things.",
        normalized_description="build things.",
        source_url=f"https://job-boards.greenhouse.io/acme/jobs/{external_id}",
        content_hash="a" * 64,
        status=status,
        first_seen_at=utc_now() - timedelta(days=days_ago),
        details_fetched_at=utc_now() - timedelta(days=days_ago),
        embedding=[0.1] * EMBEDDING_DIM,
    )
    db.add(job)
    db.flush()
    return job


def test_prune_archives_old_active_postings_and_deletes_old_closed_ones(
    db_session: Session,
) -> None:
    """The dashboard shows nothing older than ten days, so nothing older
    needs its text. The row of a still-listed posting stays: it is what
    tells the next crawl the link is not new."""
    source = _make_source(db_session)

    def make(external_id: str, days_ago: int, status: JobStatus = JobStatus.ACTIVE) -> JobPosting:
        return _stored(
            db_session, source.id, source.company_id, external_id, days_ago=days_ago, status=status
        )

    old_active = make("old-active", 11)
    old_closed = make("old-closed", 11, JobStatus.CLOSED)
    with_feedback = make("old-with-feedback", 11)
    recent = make("recent", 3)
    db_session.add(JobFeedback(job_id=with_feedback.id, action=JobFeedbackAction.INTERESTED))
    candidate = CandidateProfile(
        version=1,
        filename="cv.pdf",
        file_hash="b" * 64,
        raw_text="x",
        normalized_text="x",
        structured_profile={},
        is_active=True,
    )
    role = TargetRole(canonical_name="Junior Software Engineer")
    db_session.add_all([candidate, role])
    db_session.flush()
    db_session.add(
        JobMatch(
            candidate_profile_id=candidate.id,
            target_role_id=role.id,
            job_id=old_active.id,
            candidate_semantic_score=0.5,
            intent_semantic_score=0.5,
            skill_score=0.5,
            role_score=1.0,
            seniority_score=1.0,
            location_score=1.0,
            recency_score=1.0,
            final_score=80.0,
            reasons=[],
            concerns=[],
        )
    )
    db_session.commit()

    result = prune_postings(db_session)

    # The shared development database has old postings of its own, so
    # the counts are floors; the seeded rows tell the story.
    assert result.archived >= 1 and result.deleted >= 1
    db_session.expire_all()
    assert old_active.archived_at is not None
    assert old_active.description is None and old_active.embedding is None
    assert old_active.status == JobStatus.ACTIVE and old_active.title == "Junior Engineer"
    assert db_session.scalar(select(JobMatch).where(JobMatch.job_id == old_active.id)) is None
    assert db_session.get(JobPosting, old_closed.id) is None
    assert with_feedback.archived_at is None and with_feedback.description == "Build things."
    assert recent.archived_at is None and recent.embedding is not None


def test_archived_posting_is_downloaded_again_only_when_the_listing_reports_an_update(
    db_session: Session,
    fake_embedding_provider: FakeEmbeddingProvider,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = _make_source(db_session)
    job = _stored(
        db_session, source.id, source.company_id, "j1", days_ago=11, status=JobStatus.ACTIVE
    )
    db_session.commit()
    prune_postings(db_session)

    # Still listed, no timestamp: a stored page this old would normally be
    # refreshed, but an archived one is left alone.
    stub = JobStub(external_job_id="j1", title="Junior Engineer", source_url=source.source_url)
    adapter = _FakeAdapter([stub], {"j1": _details("j1")})
    monkeypatch.setattr(ingestion, "get_adapter", lambda _: adapter)
    ingestion.crawl_source(db_session, source, fake_embedding_provider)
    db_session.expire_all()
    assert adapter.fetch_calls == []
    assert job.archived_at is not None

    # The listing now says the posting changed: it is read again in full.
    updated = utc_now()
    stub = JobStub(
        external_job_id="j1",
        title="Junior Engineer",
        source_url=source.source_url,
        source_updated_at=updated,
    )
    adapter = _FakeAdapter([stub], {"j1": _details("j1", updated_at=updated)})
    monkeypatch.setattr(ingestion, "get_adapter", lambda _: adapter)
    ingestion.crawl_source(db_session, source, fake_embedding_provider)
    db_session.expire_all()
    assert adapter.fetch_calls == ["j1"]
    assert job.archived_at is None
    assert job.embedding is not None

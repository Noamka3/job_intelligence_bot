from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.timezone import utc_now
from app.ingestion.adapters.base import JobDetails, JobStub, JobUnavailableError
from app.models.career_source import CareerSource
from app.models.company import Company
from app.models.enums import CareerSourceType, CrawlRunStatus, JobStatus
from app.models.job_posting import JobPosting
from app.services.jobs import ingestion
from tests.conftest import FakeEmbeddingProvider


class _FakeAdapter:
    def __init__(
        self,
        stubs: list[JobStub],
        details_by_id: dict[str, JobDetails] | None = None,
        raise_on_list: Exception | None = None,
    ) -> None:
        self.stubs = stubs
        self.details_by_id = details_by_id or {}
        self.raise_on_list = raise_on_list
        self.fetch_calls: list[str] = []

    def list_jobs(self, source: CareerSource) -> list[JobStub]:
        if self.raise_on_list is not None:
            raise self.raise_on_list
        return self.stubs

    def fetch_job(self, source: CareerSource, stub: JobStub) -> JobDetails:
        self.fetch_calls.append(stub.external_job_id)
        return self.details_by_id[stub.external_job_id]


def _make_source(db: Session) -> CareerSource:
    company = Company(name="Acme", normalized_name="acme")
    db.add(company)
    db.flush()
    source = CareerSource(
        company_id=company.id,
        source_type=CareerSourceType.GREENHOUSE,
        source_url="https://job-boards.greenhouse.io/acme",
        external_identifier="acme",
        poll_interval_minutes=5,
    )
    db.add(source)
    db.flush()
    return source


def _details(
    job_id: str, title: str = "Junior Engineer", updated_at: datetime | None = None
) -> JobDetails:
    # Every real adapter's fetch_job populates source_updated_at from its
    # own detail response (see e.g. greenhouse.py's _to_details) - a fake
    # missing it would make _is_definitely_unchanged never trust the cheap
    # stub comparison, which isn't representative of real adapter output.
    return JobDetails(
        external_job_id=job_id,
        title=title,
        source_url=f"https://job-boards.greenhouse.io/acme/jobs/{job_id}",
        source_updated_at=updated_at,
    )


def test_crawl_source_creates_new_jobs(
    db_session: Session,
    fake_embedding_provider: FakeEmbeddingProvider,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = _make_source(db_session)
    stub = JobStub(external_job_id="1", title="Junior Engineer", source_url=source.source_url)
    adapter = _FakeAdapter([stub], {"1": _details("1")})
    monkeypatch.setattr(ingestion, "get_adapter", lambda _: adapter)

    run = ingestion.crawl_source(db_session, source, fake_embedding_provider)

    assert run.status == CrawlRunStatus.SUCCESS
    assert run.jobs_created == 1
    assert run.jobs_seen == 1

    jobs = list(
        db_session.execute(
            select(JobPosting).where(JobPosting.career_source_id == source.id)
        ).scalars()
    )
    assert len(jobs) == 1
    assert jobs[0].external_job_id == "1"
    assert jobs[0].status == JobStatus.ACTIVE
    assert jobs[0].embedding is not None


def test_crawl_source_skips_unchanged_job_without_refetching(
    db_session: Session,
    fake_embedding_provider: FakeEmbeddingProvider,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = _make_source(db_session)
    updated_at = datetime(2024, 1, 1, tzinfo=UTC)
    stub = JobStub(
        external_job_id="1",
        title="Junior Engineer",
        source_url=source.source_url,
        source_updated_at=updated_at,
    )
    adapter = _FakeAdapter([stub], {"1": _details("1", updated_at=updated_at)})
    monkeypatch.setattr(ingestion, "get_adapter", lambda _: adapter)

    ingestion.crawl_source(db_session, source, fake_embedding_provider)
    assert adapter.fetch_calls == ["1"]

    run2 = ingestion.crawl_source(db_session, source, fake_embedding_provider)

    assert run2.jobs_created == 0
    assert run2.jobs_updated == 0
    assert adapter.fetch_calls == ["1"]  # no second fetch_job call


def test_crawl_source_updates_changed_job_and_reembeds(
    db_session: Session,
    fake_embedding_provider: FakeEmbeddingProvider,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = _make_source(db_session)
    stub = JobStub(external_job_id="1", title="Junior Engineer", source_url=source.source_url)
    adapter = _FakeAdapter([stub], {"1": _details("1")})
    monkeypatch.setattr(ingestion, "get_adapter", lambda _: adapter)
    ingestion.crawl_source(db_session, source, fake_embedding_provider)
    calls_after_first = len(fake_embedding_provider.calls)

    adapter.details_by_id["1"] = _details("1", title="Junior Engineer II")
    run2 = ingestion.crawl_source(db_session, source, fake_embedding_provider)

    assert run2.jobs_updated == 1
    assert len(fake_embedding_provider.calls) == calls_after_first + 1
    job = db_session.execute(
        select(JobPosting).where(JobPosting.career_source_id == source.id)
    ).scalar_one()
    assert job.title == "Junior Engineer II"


def test_crawl_source_closes_job_after_missing_threshold(
    db_session: Session,
    fake_embedding_provider: FakeEmbeddingProvider,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = _make_source(db_session)
    stub = JobStub(external_job_id="1", title="Junior Engineer", source_url=source.source_url)
    adapter = _FakeAdapter([stub], {"1": _details("1")})
    monkeypatch.setattr(ingestion, "get_adapter", lambda _: adapter)
    ingestion.crawl_source(db_session, source, fake_embedding_provider)

    adapter.stubs = []  # job disappears from the listing from now on
    threshold = get_settings().job_missing_threshold

    job_query = select(JobPosting).where(JobPosting.career_source_id == source.id)
    for iteration in range(threshold - 1):
        ingestion.crawl_source(db_session, source, fake_embedding_provider)
        job = db_session.execute(job_query).scalar_one()
        assert job.status == JobStatus.ACTIVE, f"closed too early on iteration {iteration}"

    ingestion.crawl_source(db_session, source, fake_embedding_provider)
    job = db_session.execute(job_query).scalar_one()
    assert job.status == JobStatus.CLOSED


def test_crawl_source_isolates_adapter_failure(
    db_session: Session,
    fake_embedding_provider: FakeEmbeddingProvider,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = _make_source(db_session)
    adapter = _FakeAdapter([], raise_on_list=RuntimeError("board unreachable"))
    monkeypatch.setattr(ingestion, "get_adapter", lambda _: adapter)

    run = ingestion.crawl_source(db_session, source, fake_embedding_provider)

    assert run.status == CrawlRunStatus.FAILED
    assert run.error_type == "RuntimeError"
    assert source.consecutive_failures == 1
    assert source.next_check_at is not None


def test_crawl_source_with_no_registered_adapter_fails_cleanly(
    db_session: Session,
    fake_embedding_provider: FakeEmbeddingProvider,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = _make_source(db_session)
    monkeypatch.setattr(ingestion, "get_adapter", lambda _: None)

    run = ingestion.crawl_source(db_session, source, fake_embedding_provider)

    assert run.status == CrawlRunStatus.FAILED
    assert run.error_type == "NoAdapter"


class _FailingFetchAdapter(_FakeAdapter):
    def __init__(
        self, stubs: list[JobStub], details_by_id: dict[str, JobDetails], failing: set[str]
    ) -> None:
        super().__init__(stubs, details_by_id)
        self.failing = failing

    def fetch_job(self, source: CareerSource, stub: JobStub) -> JobDetails:
        if stub.external_job_id in self.failing:
            raise RuntimeError("404 - posting removed between list and fetch")
        return super().fetch_job(source, stub)


def test_one_job_failing_to_fetch_does_not_sink_the_rest(
    db_session: Session,
    fake_embedding_provider: FakeEmbeddingProvider,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Before: the exception escaped crawl_source - the whole run rolled
    back (every other job lost), no FAILED CrawlRun, no backoff, and Celery
    retried the same crash on every tick."""
    source = _make_source(db_session)
    stubs = [
        JobStub(external_job_id=i, title=f"Job {i}", source_url=source.source_url)
        for i in ("1", "2", "3")
    ]
    adapter = _FailingFetchAdapter(stubs, {"1": _details("1"), "3": _details("3")}, failing={"2"})
    monkeypatch.setattr(ingestion, "get_adapter", lambda _: adapter)

    run = ingestion.crawl_source(db_session, source, fake_embedding_provider)

    assert run.status == CrawlRunStatus.SUCCESS
    assert run.jobs_seen == 3
    assert run.jobs_created == 2
    assert run.jobs_failed == 1
    stored = {
        job.external_job_id
        for job in db_session.execute(
            select(JobPosting).where(JobPosting.career_source_id == source.id)
        ).scalars()
    }
    assert stored == {"1", "3"}


def test_every_fetch_failing_fails_the_run_and_backs_off(
    db_session: Session,
    fake_embedding_provider: FakeEmbeddingProvider,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = _make_source(db_session)
    stubs = [JobStub(external_job_id="1", title="Job", source_url=source.source_url)]
    monkeypatch.setattr(
        ingestion, "get_adapter", lambda _: _FailingFetchAdapter(stubs, {}, failing={"1"})
    )

    run = ingestion.crawl_source(db_session, source, fake_embedding_provider)

    assert run.status == CrawlRunStatus.FAILED
    assert run.error_type == "AllJobsFailed"
    assert source.consecutive_failures == 1


def test_failed_refresh_leaves_the_existing_job_untouched(
    db_session: Session,
    fake_embedding_provider: FakeEmbeddingProvider,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = _make_source(db_session)
    stub = JobStub(external_job_id="1", title="Old Title", source_url=source.source_url)
    monkeypatch.setattr(
        ingestion,
        "get_adapter",
        lambda _: _FakeAdapter([stub], {"1": _details("1", title="Old Title")}),
    )
    ingestion.crawl_source(db_session, source, fake_embedding_provider)

    changed_stub = JobStub(external_job_id="1", title="New Title", source_url=source.source_url)
    monkeypatch.setattr(
        ingestion,
        "get_adapter",
        lambda _: _FailingFetchAdapter([changed_stub], {}, failing={"1"}),
    )
    run = ingestion.crawl_source(db_session, source, fake_embedding_provider)

    job = db_session.execute(
        select(JobPosting).where(JobPosting.career_source_id == source.id)
    ).scalar_one()
    assert job.title == "Old Title"
    assert job.status == JobStatus.ACTIVE  # it was listed, so it's not "missing"
    assert run.jobs_failed == 1


class _UnavailableFetchAdapter(_FakeAdapter):
    def __init__(self, stubs: list[JobStub], details_by_id: dict[str, JobDetails]) -> None:
        super().__init__(stubs, details_by_id)

    def fetch_job(self, source: CareerSource, stub: JobStub) -> JobDetails:
        if stub.external_job_id not in self.details_by_id:
            raise JobUnavailableError(stub.source_url, "page is a job listing, not a job")
        return super().fetch_job(source, stub)


def test_unavailable_job_is_closed_if_stored_and_skipped_if_new_without_failing_the_run(
    db_session: Session,
    fake_embedding_provider: FakeEmbeddingProvider,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = _make_source(db_session)
    stubs = [
        JobStub(external_job_id=i, title=f"Job {i}", source_url=source.source_url)
        for i in ("real", "category")
    ]
    monkeypatch.setattr(
        ingestion,
        "get_adapter",
        lambda _: _FakeAdapter(stubs, {"real": _details("real"), "category": _details("category")}),
    )
    ingestion.crawl_source(db_session, source, fake_embedding_provider)

    # Next crawl: the source now says "category" is not a job page.
    later = _UnavailableFetchAdapter(stubs, {"real": _details("real")})
    monkeypatch.setattr(ingestion, "get_adapter", lambda _: later)
    run = ingestion.crawl_source(db_session, source, fake_embedding_provider)

    assert run.status == CrawlRunStatus.SUCCESS
    assert run.jobs_failed == 0
    assert run.jobs_closed == 1
    jobs = {
        job.external_job_id: job
        for job in db_session.execute(
            select(JobPosting).where(JobPosting.career_source_id == source.id)
        ).scalars()
    }
    assert jobs["category"].status == JobStatus.CLOSED
    assert jobs["real"].status == JobStatus.ACTIVE
    assert source.consecutive_failures == 0


def test_get_due_sources_skips_types_without_an_adapter_and_disabled_companies(
    db_session: Session,
) -> None:
    company = Company(name="Acme", normalized_name="acme")
    disabled_company = Company(name="Gone", normalized_name="gone", enabled=False)
    db_session.add_all([company, disabled_company])
    db_session.flush()

    no_adapter_yet = CareerSource(
        company_id=company.id,
        source_type=CareerSourceType.PLAYWRIGHT,  # the one type still without an adapter
        source_url="https://a",
        enabled=True,
    )
    of_disabled_company = CareerSource(
        company_id=disabled_company.id,
        source_type=CareerSourceType.GREENHOUSE,
        source_url="https://b",
        enabled=True,
    )
    due = CareerSource(
        company_id=company.id,
        source_type=CareerSourceType.GREENHOUSE,
        source_url="https://c",
        enabled=True,
    )
    db_session.add_all([no_adapter_yet, of_disabled_company, due])
    db_session.commit()

    result = ingestion.get_due_sources(db_session)

    assert due in result
    assert no_adapter_yet not in result
    assert of_disabled_company not in result
    # Untouched, so it's picked up on the first tick once its adapter lands.
    assert no_adapter_yet.next_check_at is None


def test_get_due_sources_only_returns_enabled_and_due(db_session: Session) -> None:
    company = Company(name="Acme", normalized_name="acme")
    db_session.add(company)
    db_session.flush()

    due = CareerSource(
        company_id=company.id,
        source_type=CareerSourceType.GREENHOUSE,
        source_url="https://a",
        enabled=True,
        next_check_at=None,
    )
    not_due = CareerSource(
        company_id=company.id,
        source_type=CareerSourceType.GREENHOUSE,
        source_url="https://b",
        enabled=True,
        next_check_at=utc_now() + timedelta(hours=1),
    )
    disabled = CareerSource(
        company_id=company.id,
        source_type=CareerSourceType.GREENHOUSE,
        source_url="https://c",
        enabled=False,
        next_check_at=None,
    )
    db_session.add_all([due, not_due, disabled])
    db_session.commit()

    result = ingestion.get_due_sources(db_session)

    assert due in result
    assert not_due not in result
    assert disabled not in result


def test_get_due_sources_serves_api_backed_sources_before_plain_career_sites(
    db_session: Session,
) -> None:
    """Measured: 164 plain career sites take 85% of the crawl time. A job
    at an ATS-backed company must not wait behind them in the queue."""
    company = Company(name="Acme", normalized_name="acme")
    db_session.add(company)
    db_session.flush()
    older_generic = CareerSource(
        company_id=company.id,
        source_type=CareerSourceType.GENERIC_HTML,
        source_url="https://acme.example/careers",
        enabled=True,
        next_check_at=utc_now() - timedelta(hours=2),
    )
    newer_api = CareerSource(
        company_id=company.id,
        source_type=CareerSourceType.GREENHOUSE,
        source_url="https://job-boards.greenhouse.io/acme-api",
        enabled=True,
        next_check_at=utc_now() - timedelta(minutes=1),
    )
    db_session.add_all([older_generic, newer_api])
    db_session.flush()

    ordered = [s.id for s in ingestion.get_due_sources(db_session)]

    assert ordered.index(newer_api.id) < ordered.index(older_generic.id)

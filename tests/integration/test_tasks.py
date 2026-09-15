"""Tests for the Celery task wrappers in app/tasks/crawlers.py. The
broker (.delay()) is mocked out so these don't need a live Redis
connection - the underlying crawl_source/get_due_sources logic is
already covered by tests/integration/test_ingestion.py; these just check
the Celery-facing wiring around it.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import timedelta

import pytest
from sqlalchemy.orm import Session

from app.core.timezone import utc_now
from app.ingestion.adapters.base import JobDetails, JobStub
from app.models.career_source import CareerSource
from app.models.company import Company
from app.models.enums import CareerSourceType
from app.services.jobs import ingestion
from app.tasks import crawlers
from tests.conftest import FakeEmbeddingProvider


class _FakeAdapter:
    def list_jobs(self, source: CareerSource) -> list[JobStub]:
        return []

    def fetch_job(self, source: CareerSource, stub: JobStub) -> JobDetails:
        raise AssertionError("not called - list_jobs returns no stubs")


def _make_source(db: Session) -> CareerSource:
    company = Company(name="Acme", normalized_name="acme")
    db.add(company)
    db.flush()
    source = CareerSource(
        company_id=company.id,
        source_type=CareerSourceType.GREENHOUSE,
        source_url="https://job-boards.greenhouse.io/acme",
        enabled=True,
    )
    db.add(source)
    db.flush()
    return source


class _NoCloseSessionWrapper:
    """crawl_one_source/dispatch_due_sources use `with session_factory()
    as db:`, which calls db.close() on exit - fine in production (a fresh
    Session per call), but it would detach every object from the shared
    transactional db_session these tests reuse. This wrapper honors the
    context-manager protocol without actually closing the real session;
    the test fixture owns db_session's lifecycle.
    """

    def __init__(self, db: Session) -> None:
        self._db = db

    def __enter__(self) -> Session:
        return self._db

    def __exit__(self, *exc_info: object) -> None:
        pass


def _session_factory_returning(db: Session) -> Callable[[], _NoCloseSessionWrapper]:
    def factory() -> _NoCloseSessionWrapper:
        return _NoCloseSessionWrapper(db)

    return factory


def test_dispatch_due_sources_enqueues_one_task_per_due_source(
    db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = _make_source(db_session)

    enqueued: list[int] = []
    monkeypatch.setattr(
        crawlers.crawl_one_source, "delay", lambda source_id: enqueued.append(source_id)
    )
    monkeypatch.setattr(
        crawlers, "get_session_factory", lambda: _session_factory_returning(db_session)
    )

    count = crawlers.dispatch_due_sources()

    # >= 1 rather than == 1: the dev DB has real career_sources from
    # manual testing that are also genuinely due by wall-clock time -
    # dispatch_due_sources is meant to process the whole queue, so this
    # only asserts on the source *this* test created.
    assert count >= 1
    assert source.id in enqueued


def test_dispatch_leases_each_source_so_the_next_tick_cannot_redispatch_it(
    db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """With a long queue, Beat's next tick used to re-enqueue every source
    still waiting; two workers then crawled the same board at once and
    collided on the (career_source_id, external_job_id) unique key."""
    source = _make_source(db_session)
    monkeypatch.setattr(crawlers.crawl_one_source, "delay", lambda source_id: None)
    monkeypatch.setattr(
        crawlers, "get_session_factory", lambda: _session_factory_returning(db_session)
    )

    crawlers.dispatch_due_sources()
    db_session.refresh(source)
    assert source.next_check_at is not None
    assert source.next_check_at > utc_now()

    enqueued: list[int] = []
    monkeypatch.setattr(
        crawlers.crawl_one_source, "delay", lambda source_id: enqueued.append(source_id)
    )
    crawlers.dispatch_due_sources()
    assert source.id not in enqueued


def test_dispatch_due_sources_skips_sources_not_yet_due(
    db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = _make_source(db_session)
    source.next_check_at = utc_now() + timedelta(hours=1)
    db_session.flush()  # session autoflush=False - the query below needs this visible

    enqueued: list[int] = []
    monkeypatch.setattr(
        crawlers.crawl_one_source, "delay", lambda source_id: enqueued.append(source_id)
    )
    monkeypatch.setattr(
        crawlers, "get_session_factory", lambda: _session_factory_returning(db_session)
    )

    crawlers.dispatch_due_sources()

    assert source.id not in enqueued


def test_crawl_one_source_runs_the_real_crawl(
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
    fake_embedding_provider: FakeEmbeddingProvider,
) -> None:
    source = _make_source(db_session)

    monkeypatch.setattr(ingestion, "get_adapter", lambda _: _FakeAdapter())
    monkeypatch.setattr(
        crawlers, "get_session_factory", lambda: _session_factory_returning(db_session)
    )
    monkeypatch.setattr(crawlers, "get_embedding_provider", lambda: fake_embedding_provider)

    crawlers.crawl_one_source(source.id)

    db_session.refresh(source)
    assert source.last_successful_check_at is not None
    assert source.consecutive_failures == 0


def test_crawl_one_source_handles_missing_source_without_raising(
    db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        crawlers, "get_session_factory", lambda: _session_factory_returning(db_session)
    )

    crawlers.crawl_one_source(999_999)

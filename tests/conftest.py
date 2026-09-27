"""Shared pytest fixtures.

db_session wraps each test in an outer transaction + a restartable SAVEPOINT
so tests can call session.commit()/rollback() freely and nothing persists
between tests, without needing to reset the database between runs.
"""

from __future__ import annotations

from collections.abc import Generator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import event
from sqlalchemy.engine import Connection
from sqlalchemy.orm import Session, sessionmaker

from app.db.session import get_background_session_factory, get_db, get_engine
from app.main import app
from app.models.constants import EMBEDDING_DIM
from app.services.embeddings import get_embedding_provider


class NoCloseSession:
    """A context manager handing out an existing Session without closing
    it on exit - lets code written as `with session_factory() as db:`
    (Celery tasks, FastAPI background tasks) run inside a test's single
    rolled-back transaction instead of opening a real one of its own."""

    def __init__(self, db: Session) -> None:
        self._db = db

    def __enter__(self) -> Session:
        return self._db

    def __exit__(self, *exc_info: object) -> None:
        pass


class FakeEmbeddingProvider:
    """A deterministic, free EmbeddingProvider for tests - no network calls.

    Returns a fixed-length zero vector by default, or per-text overrides
    (matched by substring) when a test needs embeddings to differ.
    """

    dimensions = EMBEDDING_DIM

    def __init__(self, overrides: dict[str, list[float]] | None = None) -> None:
        self.calls: list[str] = []
        self._overrides = overrides or {}

    def embed_one(self, text: str) -> list[float]:
        self.calls.append(text)
        for needle, vector in self._overrides.items():
            if needle in text:
                return vector
        return [0.0] * self.dimensions

    def embed_many(self, texts: list[str]) -> list[list[float]]:
        return [self.embed_one(text) for text in texts]


@pytest.fixture
def fake_embedding_provider() -> FakeEmbeddingProvider:
    return FakeEmbeddingProvider()


@pytest.fixture
def api_client(
    db_session: Session, fake_embedding_provider: FakeEmbeddingProvider
) -> Generator[TestClient, None, None]:
    """A TestClient wired to the same transactional db_session (so test
    assertions and API-side writes see the same data) and a fake, free
    EmbeddingProvider - no real DB connections or OpenAI calls per test.
    """

    def _get_db_override() -> Generator[Session, None, None]:
        yield db_session

    app.dependency_overrides[get_db] = _get_db_override
    app.dependency_overrides[get_embedding_provider] = lambda: fake_embedding_provider
    # Background tasks (post-upload extraction/rescoring) run inside the
    # TestClient call, on this same transactional session.
    app.dependency_overrides[get_background_session_factory] = lambda: (
        lambda: NoCloseSession(db_session)
    )
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.pop(get_db, None)
        app.dependency_overrides.pop(get_embedding_provider, None)
        app.dependency_overrides.pop(get_background_session_factory, None)


@pytest.fixture
def db_session() -> Generator[Session, None, None]:
    engine = get_engine()
    connection = engine.connect()
    outer_transaction = connection.begin()

    session_factory = sessionmaker(bind=connection, autoflush=False, expire_on_commit=False)
    session = session_factory()

    nested = connection.begin_nested()

    @event.listens_for(session, "after_transaction_end")
    def _restart_savepoint(sess: Session, trans: object) -> None:
        nonlocal nested
        if not nested.is_active:
            nested = connection.begin_nested()

    try:
        yield session
    finally:
        session.close()
        outer_transaction.rollback()
        connection.close()


@pytest.fixture(autouse=True, scope="session")
def _fail_fast_if_db_unreachable() -> Generator[None, None, None]:
    """Give a clear error up front instead of many opaque failures.

    Integration tests need the docker-compose Postgres/Redis
    running (`docker compose up -d postgres redis`).
    """
    try:
        connection: Connection = get_engine().connect()
        connection.close()
    except Exception as exc:  # noqa: BLE001
        pytest.exit(
            "Cannot reach the database. Run `docker compose up -d postgres redis` "
            f"first. Original error: {exc}",
            returncode=1,
        )
    yield

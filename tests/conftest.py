"""Shared pytest fixtures.

db_session wraps each test in an outer transaction + a restartable SAVEPOINT
so tests can call session.commit()/rollback() freely and nothing persists
between tests, without needing to reset the database between runs.
"""

from __future__ import annotations

from collections.abc import Generator

import pytest
from sqlalchemy import event
from sqlalchemy.engine import Connection
from sqlalchemy.orm import Session, sessionmaker

from app.db.session import get_engine


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

    Phase 1 integration tests need the docker-compose Postgres/Redis
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

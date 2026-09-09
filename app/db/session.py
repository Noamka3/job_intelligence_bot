"""Synchronous SQLAlchemy engine/session.

This project uses sync SQLAlchemy sessions rather than the async engine.
Traffic is personal-scale (single user, a background crawler, no concurrent
request load), so async DB access would add real complexity (async
sessions, async Celery task bodies, async Alembic env) for no measurable
benefit here. FastAPI runs sync route dependencies in a threadpool, so this
does not block the event loop in practice.
"""

from __future__ import annotations

from collections.abc import Generator

from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import get_settings

_engine: Engine | None = None
_SessionLocal: sessionmaker[Session] | None = None


def get_engine() -> Engine:
    global _engine
    if _engine is None:
        _engine = create_engine(get_settings().database_url, pool_pre_ping=True)
    return _engine


def get_session_factory() -> sessionmaker[Session]:
    global _SessionLocal
    if _SessionLocal is None:
        _SessionLocal = sessionmaker(bind=get_engine(), autoflush=False, expire_on_commit=False)
    return _SessionLocal


def get_db() -> Generator[Session, None, None]:
    """FastAPI dependency yielding a request-scoped session."""
    session_factory = get_session_factory()
    db = session_factory()
    try:
        yield db
    finally:
        db.close()

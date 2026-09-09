from __future__ import annotations

import logging

import redis
from fastapi import APIRouter, Response
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.db.session import get_session_factory
from app.schemas.health import DependencyStatus, HealthResponse

router = APIRouter(tags=["health"])
logger = logging.getLogger(__name__)


def _check_database() -> DependencyStatus:
    session: Session = get_session_factory()()
    try:
        session.execute(text("SELECT 1"))
        return DependencyStatus(name="database", ok=True)
    except Exception as exc:  # noqa: BLE001 - report any failure as unhealthy, not a crash
        logger.warning(
            "health check: database unreachable", extra={"error_type": type(exc).__name__}
        )
        return DependencyStatus(name="database", ok=False, detail=type(exc).__name__)
    finally:
        session.close()


def _check_redis() -> DependencyStatus:
    client = redis.from_url(get_settings().redis_url, socket_connect_timeout=2)
    try:
        client.ping()
        return DependencyStatus(name="redis", ok=True)
    except Exception as exc:  # noqa: BLE001 - report any failure as unhealthy, not a crash
        logger.warning("health check: redis unreachable", extra={"error_type": type(exc).__name__})
        return DependencyStatus(name="redis", ok=False, detail=type(exc).__name__)
    finally:
        client.close()


@router.get("/health", response_model=HealthResponse)
def health(response: Response) -> HealthResponse:
    dependencies = [_check_database(), _check_redis()]
    all_ok = all(dependency.ok for dependency in dependencies)
    response.status_code = 200 if all_ok else 503
    return HealthResponse(status="ok" if all_ok else "degraded", dependencies=dependencies)

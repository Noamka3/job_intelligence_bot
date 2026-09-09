from __future__ import annotations

from fastapi import FastAPI

from app.api.routes.candidate import router as candidate_router
from app.api.routes.health import router as health_router
from app.api.routes.target_roles import router as target_roles_router
from app.core.logging import configure_logging

configure_logging()

app = FastAPI(
    title="Job Intelligence Bot",
    description="Monitors company career pages, matches jobs against a CV and target roles.",
    version="0.1.0",
)

app.include_router(health_router)
app.include_router(candidate_router)
app.include_router(target_roles_router)

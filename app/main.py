from __future__ import annotations

from fastapi import FastAPI

from app.api.routes.health import router as health_router
from app.core.logging import configure_logging

configure_logging()

app = FastAPI(
    title="Job Intelligence Bot",
    description="Monitors company career pages, matches jobs against a CV and target roles.",
    version="0.1.0",
)

app.include_router(health_router)

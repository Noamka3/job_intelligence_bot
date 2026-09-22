from __future__ import annotations

import logging
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

from app.api.routes.applications import router as applications_router
from app.api.routes.candidate import router as candidate_router
from app.api.routes.companies import router as companies_router
from app.api.routes.dashboard import router as dashboard_router
from app.api.routes.health import router as health_router
from app.api.routes.jobs import router as jobs_router
from app.api.routes.matches import router as matches_router
from app.api.routes.operations import router as operations_router
from app.api.routes.sources import router as sources_router
from app.api.routes.sync import router as sync_router
from app.api.routes.target_roles import router as target_roles_router
from app.core.logging import configure_logging
from app.core.security import AccessControlMiddleware

configure_logging()
logger = logging.getLogger(__name__)

app = FastAPI(
    title="Job Intelligence Bot",
    description="Monitors company career pages, matches jobs against a CV and target roles.",
    version="0.1.0",
)
# Adds the hardening headers to every response, and requires HTTP Basic
# credentials when DASHBOARD_PASSWORD is set (off by default - see
# app/core/security.py). No CORS middleware on purpose: the dashboard is
# served from this same origin, so no other site may call this API.
app.add_middleware(AccessControlMiddleware)

app.include_router(health_router)
app.include_router(candidate_router)
app.include_router(target_roles_router)
app.include_router(sync_router)
app.include_router(companies_router)
app.include_router(sources_router)
app.include_router(jobs_router)
app.include_router(matches_router)
app.include_router(operations_router)
app.include_router(dashboard_router)
app.include_router(applications_router)

# The React dashboard (frontend/, built with `npm run build`) is served
# from this same process under /app - one thing to run, no CORS. The JSON
# API keeps its root paths, which is why the SPA lives under a prefix:
# /app/jobs/5 is a page, /jobs/5 is JSON.
FRONTEND_DIST = Path(__file__).resolve().parent.parent / "frontend" / "dist"


@app.get("/", include_in_schema=False)
def root() -> RedirectResponse:
    return RedirectResponse("/app/")


if (FRONTEND_DIST / "index.html").is_file():
    app.mount("/app/assets", StaticFiles(directory=FRONTEND_DIST / "assets"), name="app-assets")

    @app.get("/app", include_in_schema=False)
    @app.get("/app/{path:path}", include_in_schema=False)
    def spa(path: str = "") -> FileResponse:
        # Real files at the dist root (favicon, manifest) are served as-is;
        # every other path is a client-side route and gets index.html.
        candidate = (FRONTEND_DIST / path).resolve()
        if path and candidate.is_file() and candidate.is_relative_to(FRONTEND_DIST):
            return FileResponse(candidate)
        return FileResponse(FRONTEND_DIST / "index.html")

else:
    logger.warning(
        "frontend/dist not found - the dashboard is not served; run `npm run build` in frontend/"
    )

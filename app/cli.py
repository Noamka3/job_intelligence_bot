"""Operational CLI. Commands are added as their phase is implemented -
see README.md for why this stays intentionally sparse for now.

Usage: python -m app.cli <command>
"""

from __future__ import annotations

import typer
from sqlalchemy import func, select

from app.db.session import get_session_factory
from app.models.career_source import CareerSource
from app.models.company import Company
from app.services.candidate.normalization import normalize_text
from app.services.candidate.profile_service import get_active_profile
from app.services.candidate.structured_profile import extract_structured_profile
from app.services.embeddings import get_embedding_provider
from app.services.jobs.ingestion import crawl_source, get_due_sources
from app.services.matching.runner import score_all_active_jobs
from app.services.sheets.company_sync import (
    normalize_company_name,
    resync_companies_from_stored_urls,
    sync_companies_from_excel,
    sync_companies_from_sheet,
)

app = typer.Typer(help="Job Intelligence Bot operational CLI.")


@app.command("sync-sheet")
def sync_sheet() -> None:
    """Sync companies + career sources from the configured Google Sheet
    (needs GOOGLE_APPLICATION_CREDENTIALS set up - see README). For a
    local .xlsx export instead, use import-excel.
    """
    session_factory = get_session_factory()
    with session_factory() as db:
        result = sync_companies_from_sheet(db)

    typer.echo(
        f"Seen: {result.companies_seen}, created: {result.companies_created}, "
        f"updated: {result.companies_updated}, disabled: {result.companies_disabled}, "
        f"sources created: {result.sources_created}"
    )


@app.command("import-excel")
def import_excel(file_path: str) -> None:
    """Sync companies + career sources from a local .xlsx export of the
    company sheet - same upsert/disable logic as sync-sheet, no Google
    credentials needed. Expects company name in column A, URL in column B.
    """
    session_factory = get_session_factory()
    with session_factory() as db:
        result = sync_companies_from_excel(db, file_path)

    typer.echo(
        f"Seen: {result.companies_seen}, created: {result.companies_created}, "
        f"updated: {result.companies_updated}, disabled: {result.companies_disabled}, "
        f"sources created: {result.sources_created}"
    )


@app.command("reresolve-sources")
def reresolve_sources() -> None:
    """Re-run career-source resolution for every enabled company from the
    sheet URL it was imported with (no sheet read) - picks up resolver
    improvements such as newly registered adapters or embedded-board
    detection on companies already in the database.
    """
    session_factory = get_session_factory()
    with session_factory() as db:
        result = resync_companies_from_stored_urls(db)
        by_type = db.execute(
            select(CareerSource.source_type, func.count())
            .where(CareerSource.enabled.is_(True))
            .group_by(CareerSource.source_type)
            .order_by(func.count().desc())
        ).all()

    typer.echo(
        f"Re-resolved {result.companies_seen} companies; sources created: {result.sources_created}"
    )
    for source_type, count in by_type:
        typer.echo(f"  {source_type.value:16} {count}")


@app.command("rebuild-profile")
def rebuild_profile() -> None:
    """Re-run structured extraction + embedding for the active CV in place,
    without requiring a re-upload. Useful after improving the extraction
    prompt or switching embedding models.
    """
    session_factory = get_session_factory()
    with session_factory() as db:
        profile = get_active_profile(db)
        if profile is None:
            typer.echo("No active candidate profile.", err=True)
            raise typer.Exit(code=1)

        normalized = normalize_text(profile.raw_text)
        structured = extract_structured_profile(normalized)
        embedding = get_embedding_provider().embed_one(normalized)

        profile.normalized_text = normalized
        profile.structured_profile = structured.model_dump()
        profile.embedding = embedding
        db.commit()
        # Existing JobMatch rows were computed against the old embedding/
        # skills - refresh them rather than leave stale scores in
        # /matches/top until the next crawl happens to touch each job.
        matches = score_all_active_jobs(db)

        typer.echo(
            f"Rebuilt profile version {profile.version} (id={profile.id}); "
            f"rescored active jobs ({matches} matches)."
        )


@app.command("crawl-now")
def crawl_now() -> None:
    """Crawl every enabled CareerSource whose next_check_at is due."""
    session_factory = get_session_factory()
    embedding_provider = get_embedding_provider()
    with session_factory() as db:
        sources = get_due_sources(db)
        if not sources:
            typer.echo("No sources due for crawling.")
            return
        for source in sources:
            run = crawl_source(db, source, embedding_provider)
            typer.echo(
                f"source #{source.id} ({source.source_type.value}): {run.status.value} "
                f"(seen={run.jobs_seen} created={run.jobs_created} "
                f"updated={run.jobs_updated} closed={run.jobs_closed})"
            )


@app.command("crawl-company")
def crawl_company(name: str) -> None:
    """Crawl every enabled CareerSource belonging to one company, by name."""
    session_factory = get_session_factory()
    embedding_provider = get_embedding_provider()
    with session_factory() as db:
        company = db.execute(
            select(Company).where(Company.normalized_name == normalize_company_name(name))
        ).scalar_one_or_none()
        if company is None:
            typer.echo(f"No company matching {name!r}.", err=True)
            raise typer.Exit(code=1)

        sources = db.execute(
            select(CareerSource).where(
                CareerSource.company_id == company.id, CareerSource.enabled.is_(True)
            )
        ).scalars()
        found = False
        for source in sources:
            found = True
            run = crawl_source(db, source, embedding_provider)
            typer.echo(
                f"{source.source_type.value}: {run.status.value} "
                f"(seen={run.jobs_seen} created={run.jobs_created} "
                f"updated={run.jobs_updated} closed={run.jobs_closed})"
            )
        if not found:
            typer.echo(f"{company.name} has no enabled career sources.")


@app.command("score-all")
def score_all() -> None:
    """Score every ACTIVE job against the active candidate profile and
    every enabled target role, writing/updating JobMatch rows.
    """
    session_factory = get_session_factory()
    with session_factory() as db:
        if get_active_profile(db) is None:
            typer.echo("No active candidate profile - upload a resume first.", err=True)
            raise typer.Exit(code=1)

        matches_written = score_all_active_jobs(db)
        typer.echo(f"Wrote/updated {matches_written} match(es).")


if __name__ == "__main__":
    app()

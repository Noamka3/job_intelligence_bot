"""Operational CLI. Commands are added as their phase is implemented -
see README.md for why this stays intentionally sparse for now.

Usage: python -m app.cli <command>
"""

from __future__ import annotations

import typer
from sqlalchemy import select

from app.db.session import get_session_factory
from app.models.career_source import CareerSource
from app.models.company import Company
from app.services.candidate.normalization import normalize_text
from app.services.candidate.profile_service import get_active_profile
from app.services.candidate.structured_profile import extract_structured_profile
from app.services.embeddings import get_embedding_provider
from app.services.jobs.ingestion import crawl_source, get_due_sources
from app.services.sheets.company_sync import normalize_company_name, sync_companies_from_sheet

app = typer.Typer(help="Job Intelligence Bot operational CLI.")


@app.command("sync-sheet")
def sync_sheet() -> None:
    """Sync companies + career sources from the configured Google Sheet."""
    session_factory = get_session_factory()
    with session_factory() as db:
        result = sync_companies_from_sheet(db)

    typer.echo(
        f"Seen: {result.companies_seen}, created: {result.companies_created}, "
        f"updated: {result.companies_updated}, disabled: {result.companies_disabled}, "
        f"sources created: {result.sources_created}"
    )


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

        typer.echo(f"Rebuilt profile version {profile.version} (id={profile.id}).")


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


if __name__ == "__main__":
    app()

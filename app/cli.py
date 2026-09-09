"""Operational CLI. Commands are added as their phase is implemented -
see README.md for why this stays intentionally sparse for now.

Usage: python -m app.cli <command>
"""

from __future__ import annotations

import typer

from app.db.session import get_session_factory
from app.services.candidate.normalization import normalize_text
from app.services.candidate.profile_service import get_active_profile
from app.services.candidate.structured_profile import extract_structured_profile
from app.services.embeddings import get_embedding_provider

app = typer.Typer(help="Job Intelligence Bot operational CLI.")


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


if __name__ == "__main__":
    app()

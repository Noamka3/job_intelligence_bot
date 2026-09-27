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
from app.models.enums import JobStatus
from app.services.candidate.normalization import normalize_text
from app.services.candidate.profile_service import get_active_profile
from app.services.candidate.structured_profile import extract_structured_profile
from app.services.embeddings import get_embedding_provider
from app.services.jev import (
    JUDGED_ABOVE_ROLE_FIT,
    is_enabled,
    judge_fit,
    needs_judgement,
    needs_reading,
    read_posting,
)
from app.services.jobs.ingestion import crawl_source, get_due_sources
from app.services.jobs.retention import prune_postings
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


@app.command("reclassify-locations")
def reclassify_locations() -> None:
    """Recompute country + region for every job from its stored location
    text - after the location vocabulary changes, so existing rows get
    the same classification a fresh crawl would give them.
    """
    from app.models.job_posting import JobPosting
    from app.services.jobs.location import classify_country, classify_region

    session_factory = get_session_factory()
    with session_factory() as db:
        changed = 0
        for job in db.execute(select(JobPosting)).scalars():
            country = classify_country(job.location_text)
            region = classify_region(job.location_text)
            if (job.country, job.region) != (country, region):
                job.country, job.region = country, region
                changed += 1
        db.commit()
    typer.echo(f"Reclassified {changed} job(s).")


@app.command("reassess-seniority")
def reassess_seniority() -> None:
    """Re-read every active job's seniority / years requirement from its
    stored text (after the parser learns new phrasings - e.g. Hebrew), then
    rescore, since the seniority component of every match depends on it.
    """
    from app.models.enums import JobStatus
    from app.models.job_posting import JobPosting
    from app.services.jobs.ingestion import assess_job_seniority

    session_factory = get_session_factory()
    with session_factory() as db:
        changed = 0
        jobs = db.execute(
            select(JobPosting).where(
                JobPosting.status == JobStatus.ACTIVE, JobPosting.archived_at.is_(None)
            )
        ).scalars()
        for job in jobs:
            assessment = assess_job_seniority(
                job.title, job.qualifications, job.normalized_description
            )
            if (job.seniority, job.experience_min_years) != (
                assessment.level,
                assessment.min_years_required,
            ):
                job.seniority = assessment.level
                job.experience_min_years = assessment.min_years_required
                changed += 1
        db.commit()
        typer.echo(f"Reassessed seniority: {changed} job(s) changed. Rescoring...")
        matches = score_all_active_jobs(db)
    typer.echo(f"Rescored {matches} match(es).")


@app.command("apply-poll-intervals")
def apply_poll_intervals() -> None:
    """Set every enabled source's poll interval to the default for its
    type (see company_sync.DEFAULT_POLL_MINUTES) - after the defaults
    change; the sheet sync only applies them to new or re-typed sources.
    """
    from app.models.career_source import CareerSource
    from app.services.sheets.company_sync import default_poll_minutes

    session_factory = get_session_factory()
    with session_factory() as db:
        changed = 0
        for source in db.execute(select(CareerSource)).scalars():
            minutes = default_poll_minutes(source.source_type)
            if source.poll_interval_minutes != minutes:
                source.poll_interval_minutes = minutes
                changed += 1
        db.commit()
    typer.echo(f"Updated the poll interval of {changed} source(s).")


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


@app.command("reembed")
def reembed() -> None:
    """Recompute every embedding (active CV, enabled target roles, all
    ACTIVE jobs) with the current provider and embedding-text rules, then
    rescore. Run after changing the embedding model, the chunking, or
    build_embedding_text - stored vectors don't update on their own
    because the jobs' content didn't change.
    """
    from app.ingestion.adapters.base import JobDetails
    from app.models.job_posting import JobPosting
    from app.models.target_role import TargetRole
    from app.services.jobs.normalization import build_embedding_text, content_hash_for
    from app.services.target_roles import _build_embedding_text as role_embedding_text

    provider = get_embedding_provider()
    session_factory = get_session_factory()
    with session_factory() as db:
        profile = get_active_profile(db)
        if profile is not None:
            profile.embedding = provider.embed_one(profile.normalized_text)
        for role in db.execute(select(TargetRole).where(TargetRole.enabled.is_(True))).scalars():
            role.embedding = provider.embed_one(role_embedding_text(role))
        db.commit()

        jobs = db.execute(
            select(JobPosting).where(
                JobPosting.status == JobStatus.ACTIVE, JobPosting.archived_at.is_(None)
            )
        ).scalars()
        done = 0
        for job in jobs:
            details = JobDetails(
                external_job_id=job.external_job_id,
                title=job.title,
                department=job.department,
                team=job.team,
                location_text=job.location_text,
                remote_type=job.remote_type,
                employment_type=job.employment_type,
                description=job.description,
                responsibilities=job.responsibilities,
                qualifications=job.qualifications,
                required_skills=job.required_skills,
                preferred_skills=job.preferred_skills,
                source_url=job.source_url,
                apply_url=job.apply_url,
            )
            text = build_embedding_text(details)
            job.embedding = provider.embed_one(text)
            job.content_hash = content_hash_for(text)
            done += 1
            if done % 100 == 0:
                db.commit()
                typer.echo(f"  re-embedded {done} jobs...")
        db.commit()
        typer.echo(f"Re-embedded {done} active jobs; rescoring...")
        matches = score_all_active_jobs(db)
    typer.echo(f"Done - {matches} matches recomputed.")


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


@app.command("prune")
def prune() -> None:
    """Archive postings past the retention window (text and vector go,
    the row stays so the crawler knows the link) and delete closed ones.
    Runs nightly by itself; this is the same thing on demand.
    """
    with get_session_factory()() as db:
        result = prune_postings(db)
    typer.echo(f"Archived {result.archived} posting(s), deleted {result.deleted} closed one(s).")


@app.command("jev-backfill")
def jev_backfill() -> None:
    """Ask Jev about every stored posting it has not read, and about
    every match above the role gate it has not judged. New postings get
    both as they are crawled; this covers what was there before the key.
    """
    from app.models.job_match import JobMatch
    from app.models.job_posting import JobPosting
    from app.models.target_role import TargetRole

    if not is_enabled():
        typer.echo("TYPESAFE_API_KEY is not set - nothing to do.", err=True)
        raise typer.Exit(code=1)
    session_factory = get_session_factory()
    with session_factory() as db:
        candidate = get_active_profile(db)
        if candidate is None:
            typer.echo("No active candidate profile - upload a resume first.", err=True)
            raise typer.Exit(code=1)

        jobs = db.execute(
            select(JobPosting).where(
                JobPosting.status == JobStatus.ACTIVE, JobPosting.archived_at.is_(None)
            )
        ).scalars()
        read = 0
        for index, job in enumerate((j for j in jobs if needs_reading(j)), start=1):
            job.jev_reading = read_posting(job)
            read += job.jev_reading is not None
            if index % 100 == 0:
                db.commit()
                typer.echo(f"  read {index} postings...")
        db.commit()

        rows = db.execute(
            select(JobMatch, JobPosting, TargetRole)
            .join(JobPosting, JobMatch.job_id == JobPosting.id)
            .join(TargetRole, JobMatch.target_role_id == TargetRole.id)
            .where(
                JobMatch.candidate_profile_id == candidate.id,
                JobMatch.role_score >= JUDGED_ABOVE_ROLE_FIT,
                JobPosting.status == JobStatus.ACTIVE,
            )
        ).all()
        judged = 0
        for index, (match, job, role) in enumerate(
            ((m, j, r) for m, j, r in rows if needs_judgement(m, j)), start=1
        ):
            match.jev_fit = judge_fit(candidate, role, job)
            judged += match.jev_fit is not None
            if index % 100 == 0:
                db.commit()
                typer.echo(f"  judged {index} matches...")
        db.commit()
    typer.echo(f"Read {read} posting(s), judged {judged} match(es).")


@app.command("jev-agreement")
def jev_agreement() -> None:
    """How well the rules and Jev each predict the feedback given in the
    dashboard - pairwise accuracy of positive over negative feedback.
    """
    from app.services.jev.agreement import measure

    with get_session_factory()() as db:
        result = measure(db)
    if result is None:
        typer.echo("No active candidate profile - upload a resume first.", err=True)
        raise typer.Exit(code=1)

    def show(value: float | None) -> str:
        return "n/a" if value is None else f"{value:.0%}"

    typer.echo(f"Feedback: {result.positives} positive, {result.negatives} negative")
    typer.echo(f"Rules rank positive over negative: {show(result.rules_pairwise)}")
    typer.echo(
        f"Jev   rank positive over negative: {show(result.jev_pairwise)}"
        f"  (judged: {result.judged_positives} positive, {result.judged_negatives} negative)"
    )


if __name__ == "__main__":
    app()

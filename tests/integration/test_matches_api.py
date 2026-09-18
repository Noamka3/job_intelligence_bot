from __future__ import annotations

import hashlib

from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.candidate_profile import CandidateProfile
from app.models.career_source import CareerSource
from app.models.company import Company
from app.models.constants import EMBEDDING_DIM
from app.models.enums import CareerSourceType, JobStatus
from app.models.job_match import JobMatch
from app.models.job_posting import JobPosting
from app.models.target_role import TargetRole
from app.services.jobs.location import classify_country
from tests.conftest import FakeEmbeddingProvider


def _seed(db_session: Session) -> tuple[JobPosting, JobPosting, JobPosting]:
    candidate = CandidateProfile(
        version=1,
        filename="cv.pdf",
        file_hash="a" * 64,
        raw_text="x",
        normalized_text="x",
        structured_profile={},
        is_active=True,
    )
    role = TargetRole(canonical_name="Junior Software Engineer")
    company = Company(name="Acme", normalized_name="acme")
    db_session.add_all([candidate, role, company])
    db_session.flush()
    source = CareerSource(
        company_id=company.id,
        source_type=CareerSourceType.GREENHOUSE,
        source_url="https://job-boards.greenhouse.io/acme",
    )
    db_session.add(source)
    db_session.flush()

    jobs = []
    for external_id, status, location in (
        ("open", JobStatus.ACTIVE, "Tel Aviv"),
        ("gone", JobStatus.CLOSED, "Tel Aviv"),
        ("abroad", JobStatus.ACTIVE, "Warsaw, Poland"),
    ):
        job = JobPosting(
            company_id=company.id,
            career_source_id=source.id,
            external_job_id=external_id,
            title="Junior Software Engineer",
            normalized_title="junior software engineer",
            location_text=location,
            country=classify_country(location),
            source_url=f"https://job-boards.greenhouse.io/acme/jobs/{external_id}",
            content_hash=hashlib.sha256(external_id.encode()).hexdigest(),
            status=status,
        )
        db_session.add(job)
        db_session.flush()
        db_session.add(
            JobMatch(
                candidate_profile_id=candidate.id,
                target_role_id=role.id,
                job_id=job.id,
                candidate_semantic_score=0.9,
                intent_semantic_score=0.9,
                skill_score=0.9,
                role_score=1.0,
                seniority_score=1.0,
                location_score=1.0,
                recency_score=1.0,
                final_score=95.0,
                reasons=[],
                concerns=[],
            )
        )
        jobs.append(job)
    db_session.commit()
    return jobs[0], jobs[1], jobs[2]


def test_top_matches_never_lists_closed_jobs(api_client: TestClient, db_session: Session) -> None:
    """JobMatch rows are kept when a job closes (history) - a 95-point
    match for a job that vanished from the ATS used to stay at #1 forever."""
    open_job, closed_job, _ = _seed(db_session)

    response = api_client.get("/matches/top")

    assert response.status_code == 200
    job_ids = {match["job_id"] for match in response.json()}
    assert open_job.id in job_ids
    assert closed_job.id not in job_ids


def test_top_matches_are_israel_only_by_default(
    api_client: TestClient, db_session: Session
) -> None:
    """location_score is a 3% tiebreaker, so a strong match abroad used to
    outrank every Israeli one in the list the user actually reads."""
    open_job, _, abroad_job = _seed(db_session)

    default_ids = {match["job_id"] for match in api_client.get("/matches/top").json()}
    assert open_job.id in default_ids
    assert abroad_job.id not in default_ids

    everything = api_client.get("/matches/top", params={"israel_only": "false"}).json()
    assert abroad_job.id in {match["job_id"] for match in everything}


def test_top_matches_search_dismissal_and_per_job_breakdown(
    api_client: TestClient, db_session: Session
) -> None:
    open_job, _, _ = _seed(db_session)

    by_query = api_client.get("/matches/top", params={"q": "acme"}).json()
    assert open_job.id in {m["job_id"] for m in by_query}
    assert api_client.get("/matches/top", params={"q": "zzz-no-such"}).json() == []
    assert by_query[0]["source_type"] == "greenhouse"
    assert by_query[0]["company_name"] == "Acme"
    assert by_query[0]["last_feedback"] is None

    # Dismissing feedback hides the job by default, but not with hide_dismissed=false.
    api_client.post(f"/jobs/{open_job.id}/feedback", json={"action": "too_senior"})
    assert open_job.id not in {m["job_id"] for m in api_client.get("/matches/top").json()}
    shown = api_client.get("/matches/top", params={"hide_dismissed": "false"}).json()
    assert {m["job_id"]: m["last_feedback"] for m in shown}[open_job.id] == "too_senior"

    breakdown = api_client.get(f"/matches/job/{open_job.id}")
    assert breakdown.status_code == 200
    assert breakdown.json()["seniority_score"] == 1.0
    assert api_client.get("/matches/job/999999").status_code == 404


def test_top_matches_sorts_newest_first_by_default_and_by_score_on_request(
    api_client: TestClient, db_session: Session
) -> None:
    from datetime import timedelta

    from app.core.timezone import utc_now

    open_job, _, _ = _seed(db_session)
    company = db_session.get(Company, open_job.company_id)
    assert company is not None
    older_better = JobPosting(
        company_id=company.id,
        career_source_id=open_job.career_source_id,
        external_job_id="older-better",
        title="Junior Backend Engineer",
        normalized_title="junior backend engineer",
        location_text="Tel Aviv",
        country="Israel",
        source_url="https://job-boards.greenhouse.io/acme/jobs/older-better",
        source_published_at=utc_now() - timedelta(days=40),
        content_hash="c" * 64,
        status=JobStatus.ACTIVE,
    )
    db_session.add(older_better)
    db_session.flush()
    candidate_id = db_session.execute(
        select(JobMatch.candidate_profile_id).where(JobMatch.job_id == open_job.id)
    ).scalar_one()
    role_id = db_session.execute(
        select(JobMatch.target_role_id).where(JobMatch.job_id == open_job.id)
    ).scalar_one()
    db_session.add(
        JobMatch(
            candidate_profile_id=candidate_id,
            target_role_id=role_id,
            job_id=older_better.id,
            candidate_semantic_score=0.9,
            intent_semantic_score=0.9,
            skill_score=1.0,
            role_score=1.0,
            seniority_score=1.0,
            location_score=1.0,
            recency_score=1.0,
            final_score=99.0,
            reasons=[],
            concerns=[],
        )
    )
    db_session.commit()

    # open_job has no publish date, so its discovery time (now) counts:
    # it's newer than the 40-day-old, higher-scoring one.
    newest_first = [m["job_id"] for m in api_client.get("/matches/top").json()]
    assert newest_first.index(open_job.id) < newest_first.index(older_better.id)

    scored = api_client.get("/matches/top", params={"sort": "score"}).json()
    by_score = [m["job_id"] for m in scored]
    assert by_score.index(older_better.id) < by_score.index(open_job.id)


def test_top_matches_semantic_search_ranks_by_embedding_distance(
    api_client: TestClient, db_session: Session, fake_embedding_provider: FakeEmbeddingProvider
) -> None:
    open_job, _, _ = _seed(db_session)
    # Give the stored job a direction, and make the fake provider embed a
    # query containing "backend" onto the same direction.
    vector = [0.0] * EMBEDDING_DIM
    vector[0] = 1.0
    open_job.embedding = vector
    db_session.commit()
    fake_embedding_provider._overrides["backend"] = vector  # noqa: SLF001

    hit = api_client.get("/matches/top", params={"q": "backend work", "semantic": "true"}).json()
    assert [m["job_id"] for m in hit] == [open_job.id]

    # A query the provider embeds as the zero vector has no cosine
    # relationship with anything: nothing is "about" it.
    miss = api_client.get("/matches/top", params={"q": "zzz", "semantic": "true"}).json()
    assert miss == []


def test_dashboard_stats_and_root_redirect(api_client: TestClient, db_session: Session) -> None:
    _seed(db_session)

    stats = api_client.get("/dashboard/stats")
    assert stats.status_code == 200
    body = stats.json()
    assert body["active_jobs"] >= 1
    assert body["matches"] >= 2
    assert any(row["source_type"] == "greenhouse" for row in body["by_source_type"])

    root = api_client.get("/", follow_redirects=False)
    assert root.status_code in (302, 307)
    assert root.headers["location"] == "/app/"


def test_top_matches_rejects_out_of_range_params(api_client: TestClient) -> None:
    assert api_client.get("/matches/top", params={"limit": 0}).status_code == 422
    assert api_client.get("/matches/top", params={"min_score": 101}).status_code == 422


def test_top_matches_tag_and_filter_by_what_the_posting_says_about_experience(
    api_client: TestClient, db_session: Session
) -> None:
    """Live: postings saying "לפחות 4 שנות ניסיון" reached a candidate with
    no experience at 61% because nothing read the requirement and nothing
    let the user filter on it."""
    from app.models.enums import SeniorityLevel

    open_job, _, abroad_job = _seed(db_session)
    open_job.seniority = SeniorityLevel.JUNIOR
    abroad_job.experience_min_years = 5  # "לפחות 5 שנות ניסיון", no title signal
    db_session.commit()

    everything = api_client.get("/matches/top", params={"israel_only": "false"}).json()
    fit_by_job = {m["job_id"]: m["seniority_fit"] for m in everything}
    assert fit_by_job[open_job.id] == "fit"
    assert fit_by_job[abroad_job.id] == "experienced"
    assert {m["job_id"]: m["experience_min_years"] for m in everything}[abroad_job.id] == 5

    only_fit = api_client.get(
        "/matches/top", params={"israel_only": "false", "seniority": "fit"}
    ).json()
    assert {m["job_id"] for m in only_fit} == {open_job.id}

    not_experienced = api_client.get(
        "/matches/top", params={"israel_only": "false", "seniority": "not_experienced"}
    ).json()
    assert abroad_job.id not in {m["job_id"] for m in not_experienced}

    assert api_client.get("/matches/top", params={"seniority": "bogus"}).status_code == 422

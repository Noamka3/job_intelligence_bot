from __future__ import annotations

import hashlib

import pytest
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models import CandidateProfile, CareerSource, Company, JobPosting
from app.models.enums import CareerSourceType


def test_company_roundtrip(db_session: Session) -> None:
    company = Company(name="Acme", normalized_name="acme")
    db_session.add(company)
    db_session.flush()

    fetched = db_session.get(Company, company.id)
    assert fetched is not None
    assert fetched.name == "Acme"
    assert fetched.enabled is True


def test_career_source_requires_existing_company(db_session: Session) -> None:
    source = CareerSource(
        company_id=999_999,
        source_type=CareerSourceType.GREENHOUSE,
        source_url="https://job-boards.greenhouse.io/acme",
    )
    db_session.add(source)
    with pytest.raises(IntegrityError):
        db_session.flush()


def test_job_posting_unique_external_id_per_source(db_session: Session) -> None:
    company = Company(name="Acme", normalized_name="acme")
    db_session.add(company)
    db_session.flush()

    source = CareerSource(
        company_id=company.id,
        source_type=CareerSourceType.GREENHOUSE,
        source_url="https://job-boards.greenhouse.io/acme",
    )
    db_session.add(source)
    db_session.flush()

    def make_job() -> JobPosting:
        return JobPosting(
            company_id=company.id,
            career_source_id=source.id,
            external_job_id="123",
            title="Software Engineer I",
            normalized_title="software engineer i",
            source_url="https://job-boards.greenhouse.io/acme/jobs/123",
            content_hash=hashlib.sha256(b"content").hexdigest(),
        )

    db_session.add(make_job())
    db_session.flush()

    db_session.add(make_job())
    with pytest.raises(IntegrityError):
        db_session.flush()


def test_candidate_profile_only_one_active_at_a_time(db_session: Session) -> None:
    first = CandidateProfile(
        version=1,
        filename="cv_v1.pdf",
        file_hash="a" * 64,
        raw_text="text",
        normalized_text="text",
        structured_profile={},
        is_active=True,
    )
    db_session.add(first)
    db_session.flush()

    second = CandidateProfile(
        version=2,
        filename="cv_v2.pdf",
        file_hash="b" * 64,
        raw_text="text",
        normalized_text="text",
        structured_profile={},
        is_active=True,
    )
    db_session.add(second)
    with pytest.raises(IntegrityError):
        db_session.flush()

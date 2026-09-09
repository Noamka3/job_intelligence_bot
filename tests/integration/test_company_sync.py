from __future__ import annotations

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.ingestion.resolver import ResolvedSource
from app.models.career_source import CareerSource
from app.models.company import Company
from app.models.enums import CareerSourceType
from app.services.sheets import company_sync
from app.services.sheets.reader import CompanySheetRow

_RESOLUTIONS = {
    "https://job-boards.greenhouse.io/acme": ResolvedSource(CareerSourceType.GREENHOUSE, "acme"),
    "https://www.linkedin.com/in/some-recruiter/": ResolvedSource(
        CareerSourceType.UNSUPPORTED, None, "linkedin_not_scraped"
    ),
    "https://careers.example.com/": ResolvedSource(CareerSourceType.GENERIC_HTML, None),
}


def _fake_resolver(url: str | None) -> ResolvedSource:
    if url is None:
        return ResolvedSource(CareerSourceType.UNSUPPORTED, None, "missing_url")
    return _RESOLUTIONS[url]


@pytest.fixture(autouse=True)
def _patch_resolver(monkeypatch: pytest.MonkeyPatch) -> None:
    """Never let the sync's concurrent resolution step make a real network
    call - resolve_career_source is exercised directly in test_resolver.py.
    """
    monkeypatch.setattr(company_sync, "resolve_career_source", _fake_resolver)


def _mock_rows(monkeypatch: pytest.MonkeyPatch, rows: list[CompanySheetRow]) -> None:
    monkeypatch.setattr(company_sync, "read_company_rows", lambda: rows)


def test_sync_creates_companies_and_sources(
    db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    _mock_rows(
        monkeypatch,
        [
            CompanySheetRow(name="Acme", url="https://job-boards.greenhouse.io/acme"),
            CompanySheetRow(
                name="Some Recruiter Co", url="https://www.linkedin.com/in/some-recruiter/"
            ),
        ],
    )

    result = company_sync.sync_companies_from_sheet(db_session)

    assert result.companies_created == 2
    assert result.sources_created == 2

    acme = db_session.execute(select(Company).where(Company.normalized_name == "acme")).scalar_one()
    assert acme.enabled is True

    acme_source = db_session.execute(
        select(CareerSource).where(CareerSource.company_id == acme.id)
    ).scalar_one()
    assert acme_source.source_type == CareerSourceType.GREENHOUSE
    assert acme_source.external_identifier == "acme"
    assert acme_source.enabled is True
    assert acme_source.poll_interval_minutes == 5

    linkedin_company = db_session.execute(
        select(Company).where(Company.normalized_name == "some recruiter co")
    ).scalar_one()
    linkedin_source = db_session.execute(
        select(CareerSource).where(CareerSource.company_id == linkedin_company.id)
    ).scalar_one()
    assert linkedin_source.source_type == CareerSourceType.UNSUPPORTED
    assert linkedin_source.unsupported_reason == "linkedin_not_scraped"
    assert linkedin_source.enabled is False


def test_resync_does_not_duplicate_companies_or_sources(
    db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    rows = [CompanySheetRow(name="Acme", url="https://job-boards.greenhouse.io/acme")]
    _mock_rows(monkeypatch, rows)

    first = company_sync.sync_companies_from_sheet(db_session)
    second = company_sync.sync_companies_from_sheet(db_session)

    assert first.companies_created == 1
    assert second.companies_created == 0
    assert second.sources_created == 0

    companies = list(
        db_session.execute(select(Company).where(Company.normalized_name == "acme")).scalars()
    )
    assert len(companies) == 1


def test_company_missing_from_new_sync_is_disabled_not_deleted(
    db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    _mock_rows(
        monkeypatch,
        [
            CompanySheetRow(name="Acme", url="https://job-boards.greenhouse.io/acme"),
            CompanySheetRow(
                name="Some Recruiter Co", url="https://www.linkedin.com/in/some-recruiter/"
            ),
        ],
    )
    company_sync.sync_companies_from_sheet(db_session)

    _mock_rows(
        monkeypatch, [CompanySheetRow(name="Acme", url="https://job-boards.greenhouse.io/acme")]
    )
    result = company_sync.sync_companies_from_sheet(db_session)

    assert result.companies_disabled == 1
    companies = list(
        db_session.execute(
            select(Company).where(Company.normalized_name.in_(["acme", "some recruiter co"]))
        ).scalars()
    )
    assert len(companies) == 2  # still present, just disabled
    disabled = db_session.execute(
        select(Company).where(Company.normalized_name == "some recruiter co")
    ).scalar_one()
    assert disabled.enabled is False


def test_company_reappearing_is_reenabled(
    db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    row = CompanySheetRow(name="Acme", url="https://job-boards.greenhouse.io/acme")
    _mock_rows(monkeypatch, [row])
    company_sync.sync_companies_from_sheet(db_session)

    _mock_rows(monkeypatch, [])
    company_sync.sync_companies_from_sheet(db_session)
    disabled = db_session.execute(
        select(Company).where(Company.normalized_name == "acme")
    ).scalar_one()
    assert disabled.enabled is False

    _mock_rows(monkeypatch, [row])
    company_sync.sync_companies_from_sheet(db_session)
    reenabled = db_session.execute(
        select(Company).where(Company.normalized_name == "acme")
    ).scalar_one()
    assert reenabled.enabled is True


def test_failed_sheet_read_touches_nothing(
    db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    existing = Company(name="Acme", normalized_name="acme", enabled=True)
    db_session.add(existing)
    db_session.commit()

    def _boom() -> list[CompanySheetRow]:
        raise RuntimeError("Sheets API unavailable")

    monkeypatch.setattr(company_sync, "read_company_rows", _boom)

    with pytest.raises(RuntimeError):
        company_sync.sync_companies_from_sheet(db_session)

    still_there = db_session.execute(
        select(Company).where(Company.normalized_name == "acme")
    ).scalar_one()
    assert still_there.enabled is True

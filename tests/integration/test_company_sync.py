from __future__ import annotations

from pathlib import Path

import openpyxl
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
    "https://job-boards.greenhouse.io/acme?offices%5B%5D=1": ResolvedSource(
        CareerSourceType.GREENHOUSE, "acme"
    ),
    "https://www.linkedin.com/in/some-recruiter/": ResolvedSource(
        CareerSourceType.UNSUPPORTED, None, "linkedin_not_scraped"
    ),
    "https://careers.example.com/": ResolvedSource(CareerSourceType.GENERIC_HTML, None),
    "https://careers.example.com/jobs": ResolvedSource(CareerSourceType.JSONLD, None),
    "https://jobs.lever.co/acme": ResolvedSource(CareerSourceType.LEVER, "acme"),
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


def test_url_variant_of_same_board_does_not_create_a_second_source(
    db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A sheet edit that only changes a query string (tracking param,
    office filter, ...) must not spawn a duplicate CareerSource for what
    both resolve to the same board (same source_type + external_identifier)
    - found for real on Torq/Tango during Phase 4 live verification: two
    URL variants had created two sources, and each independently pulled
    (and stored) the same real jobs, doubling them.
    """
    _mock_rows(
        monkeypatch, [CompanySheetRow(name="Acme", url="https://job-boards.greenhouse.io/acme")]
    )
    company_sync.sync_companies_from_sheet(db_session)

    _mock_rows(
        monkeypatch,
        [
            CompanySheetRow(
                name="Acme", url="https://job-boards.greenhouse.io/acme?offices%5B%5D=1"
            )
        ],
    )
    result = company_sync.sync_companies_from_sheet(db_session)

    assert result.sources_created == 0
    company = db_session.execute(
        select(Company).where(Company.normalized_name == "acme")
    ).scalar_one()
    sources = list(
        db_session.execute(
            select(CareerSource).where(CareerSource.company_id == company.id)
        ).scalars()
    )
    assert len(sources) == 1


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


def _sources_of(db_session: Session, normalized_name: str) -> list[CareerSource]:
    company = db_session.execute(
        select(Company).where(Company.normalized_name == normalized_name)
    ).scalar_one()
    return list(
        db_session.execute(
            select(CareerSource)
            .where(CareerSource.company_id == company.id)
            .order_by(CareerSource.id)
        ).scalars()
    )


def test_same_board_listed_twice_in_one_sync_does_not_abort_the_sync(
    db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The session is autoflush=False: the second row's lookup couldn't
    see the first row's pending CareerSource, so both were inserted and
    the unique constraint rolled back the entire sync at commit."""
    _mock_rows(
        monkeypatch,
        [
            CompanySheetRow(name="Acme", url="https://job-boards.greenhouse.io/acme"),
            CompanySheetRow(name="Acme", url="https://job-boards.greenhouse.io/acme"),
        ],
    )

    result = company_sync.sync_companies_from_sheet(db_session)

    assert result.companies_created == 1
    assert result.sources_created == 1
    assert len(_sources_of(db_session, "acme")) == 1


def test_source_re_resolving_to_a_better_type_is_updated_in_place(
    db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A probe that timed out on the first sync leaves generic_html; when
    a later sync sees the JSON-LD markup, the row must switch rather than
    stay stuck on a type with no adapter forever."""
    url = "https://careers.example.com/jobs"
    monkeypatch.setitem(_RESOLUTIONS, url, ResolvedSource(CareerSourceType.GENERIC_HTML, None))
    _mock_rows(monkeypatch, [CompanySheetRow(name="Acme", url=url)])
    company_sync.sync_companies_from_sheet(db_session)
    (source,) = _sources_of(db_session, "acme")
    assert source.source_type == CareerSourceType.GENERIC_HTML

    monkeypatch.setitem(_RESOLUTIONS, url, ResolvedSource(CareerSourceType.JSONLD, None))
    result = company_sync.sync_companies_from_sheet(db_session)

    assert result.sources_created == 0
    (source,) = _sources_of(db_session, "acme")
    assert source.source_type == CareerSourceType.JSONLD
    assert source.poll_interval_minutes == 15

    # ...but a *failed* probe on a later sync must never downgrade it back.
    monkeypatch.setitem(_RESOLUTIONS, url, ResolvedSource(CareerSourceType.GENERIC_HTML, None))
    company_sync.sync_companies_from_sheet(db_session)
    (source,) = _sources_of(db_session, "acme")
    assert source.source_type == CareerSourceType.JSONLD


def test_row_pointing_at_a_different_board_retires_the_old_source_and_its_jobs(
    db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.models.enums import JobStatus
    from app.models.job_posting import JobPosting

    _mock_rows(
        monkeypatch, [CompanySheetRow(name="Acme", url="https://job-boards.greenhouse.io/acme")]
    )
    company_sync.sync_companies_from_sheet(db_session)
    (old_source,) = _sources_of(db_session, "acme")
    job = JobPosting(
        company_id=old_source.company_id,
        career_source_id=old_source.id,
        external_job_id="1",
        title="Engineer",
        normalized_title="engineer",
        source_url="https://job-boards.greenhouse.io/acme/jobs/1",
        content_hash="x" * 64,
        status=JobStatus.ACTIVE,
    )
    db_session.add(job)
    db_session.commit()

    _mock_rows(monkeypatch, [CompanySheetRow(name="Acme", url="https://jobs.lever.co/acme")])
    result = company_sync.sync_companies_from_sheet(db_session)

    assert result.sources_created == 1
    old, new = _sources_of(db_session, "acme")
    assert old.enabled is False
    assert new.source_type == CareerSourceType.LEVER
    assert new.enabled is True
    db_session.refresh(job)
    assert job.status == JobStatus.CLOSED  # nothing polls that board any more


def test_embedded_board_resolution_updates_the_generic_source_in_place(
    db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A company page first stored as generic_html later resolves (after
    the resolver learned to see embedded boards) to the Comeet board it
    wraps: same row, new type/identifier, and the board URL is what gets
    crawled from now on - never a second source."""
    page = "https://www.embedded-test.example/careers/"
    monkeypatch.setitem(_RESOLUTIONS, page, ResolvedSource(CareerSourceType.GENERIC_HTML, None))
    _mock_rows(monkeypatch, [CompanySheetRow(name="Embedded Test Co", url=page)])
    company_sync.sync_companies_from_sheet(db_session)

    monkeypatch.setitem(
        _RESOLUTIONS,
        page,
        ResolvedSource(
            CareerSourceType.COMEET, "63.00B", board_url="https://www.comeet.com/jobs/embedded-test/63.00B"
        ),
    )
    result = company_sync.sync_companies_from_sheet(db_session)

    assert result.sources_created == 0
    (source,) = _sources_of(db_session, "embedded test co")
    assert source.source_type == CareerSourceType.COMEET
    assert source.external_identifier == "63.00B"
    assert source.source_url == "https://www.comeet.com/jobs/embedded-test/63.00B"
    assert source.enabled is True
    assert source.next_check_at is None

    # ...and a later sync resolving the same board again is a no-op.
    company_sync.sync_companies_from_sheet(db_session)
    assert len(_sources_of(db_session, "embedded test co")) == 1


def test_resync_from_stored_urls_reresolves_without_reading_the_sheet(
    db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    page = "https://www.embedded-test.example/careers/"
    monkeypatch.setitem(_RESOLUTIONS, page, ResolvedSource(CareerSourceType.GENERIC_HTML, None))
    _mock_rows(monkeypatch, [CompanySheetRow(name="Embedded Test Co", url=page)])
    company_sync.sync_companies_from_sheet(db_session)

    def _boom() -> list[CompanySheetRow]:
        raise AssertionError("the sheet must not be read")

    monkeypatch.setattr(company_sync, "read_company_rows", _boom)
    monkeypatch.setitem(
        _RESOLUTIONS, page, ResolvedSource(CareerSourceType.WORKDAY, "embedded/External")
    )

    result = company_sync.resync_companies_from_stored_urls(db_session)

    assert result.companies_seen >= 1
    (source,) = _sources_of(db_session, "embedded test co")
    assert source.source_type == CareerSourceType.WORKDAY
    assert source.external_identifier == "embedded/External"


def test_disabling_a_company_retires_its_sources(
    db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    row = CompanySheetRow(name="Acme", url="https://job-boards.greenhouse.io/acme")
    _mock_rows(monkeypatch, [row])
    company_sync.sync_companies_from_sheet(db_session)

    _mock_rows(monkeypatch, [])
    company_sync.sync_companies_from_sheet(db_session)
    (source,) = _sources_of(db_session, "acme")
    assert source.enabled is False

    _mock_rows(monkeypatch, [row])
    company_sync.sync_companies_from_sheet(db_session)
    (source,) = _sources_of(db_session, "acme")
    assert source.enabled is True
    assert source.next_check_at is None  # crawled again on the next tick


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


def test_sync_from_excel_produces_the_same_result_shape_as_sheet_sync(
    db_session: Session, tmp_path: Path
) -> None:
    path = tmp_path / "companies.xlsx"
    workbook = openpyxl.Workbook()
    sheet = workbook.active
    assert sheet is not None
    sheet.append(("Hiring Partners Elevation", None))
    sheet.append(("Full Name", "Link", "Location"))
    sheet.append(("Acme", "https://job-boards.greenhouse.io/acme"))
    workbook.save(path)

    result = company_sync.sync_companies_from_excel(db_session, path)

    assert result.companies_created == 1
    company = db_session.execute(
        select(Company).where(Company.normalized_name == "acme")
    ).scalar_one()
    assert company.name == "Acme"

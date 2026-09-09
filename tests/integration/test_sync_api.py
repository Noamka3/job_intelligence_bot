from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.ingestion.resolver import ResolvedSource
from app.models.enums import CareerSourceType
from app.services.sheets import company_sync
from app.services.sheets.reader import CompanySheetRow


@pytest.fixture(autouse=True)
def _patch_resolver(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        company_sync,
        "resolve_career_source",
        lambda url: ResolvedSource(CareerSourceType.GREENHOUSE, "acme"),
    )


def test_sync_endpoint_creates_companies(
    api_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        company_sync,
        "read_company_rows",
        lambda: [CompanySheetRow(name="Acme", url="https://job-boards.greenhouse.io/acme")],
    )

    response = api_client.post("/sync/google-sheet")

    assert response.status_code == 200
    body = response.json()
    assert body["companies_created"] == 1
    assert body["sources_created"] == 1

    companies_response = api_client.get("/companies")
    assert companies_response.status_code == 200
    assert any(c["name"] == "Acme" for c in companies_response.json())

    sources_response = api_client.get("/sources")
    assert sources_response.status_code == 200
    assert sources_response.json()[0]["source_type"] == "greenhouse"


def test_sync_endpoint_returns_502_on_sheet_read_failure(
    api_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    def _boom() -> list[CompanySheetRow]:
        raise RuntimeError("Sheets API unavailable")

    monkeypatch.setattr(company_sync, "read_company_rows", _boom)

    response = api_client.post("/sync/google-sheet")

    assert response.status_code == 502


def test_companies_filter_by_enabled(
    api_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        company_sync,
        "read_company_rows",
        lambda: [CompanySheetRow(name="Acme", url="https://job-boards.greenhouse.io/acme")],
    )
    api_client.post("/sync/google-sheet")

    response = api_client.get("/companies", params={"enabled": "true"})
    assert response.status_code == 200
    assert all(c["enabled"] for c in response.json())

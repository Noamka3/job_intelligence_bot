from __future__ import annotations

from fastapi.testclient import TestClient

from app.main import app


def test_health_endpoint_reports_dependencies() -> None:
    client = TestClient(app)
    response = client.get("/health")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    names = {dependency["name"] for dependency in body["dependencies"]}
    assert names == {"database", "redis"}
    assert all(dependency["ok"] for dependency in body["dependencies"])

"""Access control: off by default (a tool bound to 127.0.0.1), and a real
gate once DASHBOARD_PASSWORD is set."""

from __future__ import annotations

import base64

import pytest
from fastapi.testclient import TestClient

from app.core.config import get_settings
from app.core.security import AccessControlMiddleware, FailedAttempts


def _basic(user: str, password: str) -> dict[str, str]:
    token = base64.b64encode(f"{user}:{password}".encode()).decode()
    return {"Authorization": f"Basic {token}"}


def test_no_password_configured_means_no_authentication(api_client: TestClient) -> None:
    assert api_client.get("/dashboard/stats").status_code == 200


def test_every_response_carries_the_hardening_headers(api_client: TestClient) -> None:
    response = api_client.get("/health")

    assert response.headers["X-Content-Type-Options"] == "nosniff"
    assert response.headers["X-Frame-Options"] == "DENY"
    assert response.headers["Referrer-Policy"] == "no-referrer"


def test_a_configured_password_gates_the_api_but_not_health(
    api_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings = get_settings()
    monkeypatch.setattr(settings, "dashboard_user", "noam")
    monkeypatch.setattr(settings, "dashboard_password", "s3cret")

    anonymous = api_client.get("/dashboard/stats")
    assert anonymous.status_code == 401
    assert anonymous.headers["WWW-Authenticate"].startswith("Basic ")

    # Liveness stays open, so a health check never needs the password.
    assert api_client.get("/health").status_code in (200, 503)

    assert api_client.get("/dashboard/stats", headers=_basic("noam", "wrong")).status_code == 401
    assert api_client.get("/dashboard/stats", headers=_basic("wrong", "s3cret")).status_code == 401
    assert api_client.get("/dashboard/stats", headers=_basic("noam", "s3cret")).status_code == 200


def test_malformed_authorization_headers_are_rejected_cleanly(
    api_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings = get_settings()
    monkeypatch.setattr(settings, "dashboard_user", "noam")
    monkeypatch.setattr(settings, "dashboard_password", "s3cret")

    for header in ({"Authorization": "Basic !!not-base64!!"}, {"Authorization": "Bearer token"}):
        assert api_client.get("/dashboard/stats", headers=header).status_code == 401


def test_a_write_the_browser_marks_as_cross_site_is_refused(api_client: TestClient) -> None:
    """Browsers attach cached Basic credentials to requests from other
    sites too, so a page elsewhere could submit the upload form in the
    owner's name. Sec-Fetch-Site is the browser's own word on where a
    request came from."""
    cross_site = {"Sec-Fetch-Site": "cross-site"}
    assert (
        api_client.post(
            "/jobs/1/feedback", json={"action": "saved"}, headers=cross_site
        ).status_code
        == 403
    )
    assert api_client.post("/candidate/resume", headers=cross_site).status_code == 403
    # Reads are harmless without CORS, and same-site writes go through.
    assert api_client.get("/dashboard/stats", headers=cross_site).status_code == 200
    same_site = {"Sec-Fetch-Site": "same-origin"}
    assert (
        api_client.post(
            "/jobs/999999/feedback", json={"action": "saved"}, headers=same_site
        ).status_code
        == 404
    )


def test_too_many_wrong_passwords_lock_the_address_out(
    api_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings = get_settings()
    monkeypatch.setattr(settings, "dashboard_user", "noam")
    monkeypatch.setattr(settings, "dashboard_password", "s3cret")
    monkeypatch.setattr(AccessControlMiddleware, "failed_attempts", FailedAttempts())

    for _ in range(20):
        assert (
            api_client.get("/dashboard/stats", headers=_basic("noam", "wrong")).status_code == 401
        )
    # Even the right password is refused now: the guesser must wait.
    assert api_client.get("/dashboard/stats", headers=_basic("noam", "s3cret")).status_code == 429
    assert api_client.get("/health").status_code in (200, 503)

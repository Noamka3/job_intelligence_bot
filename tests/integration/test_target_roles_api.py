from __future__ import annotations

from fastapi.testclient import TestClient


def test_create_and_list_target_roles(api_client: TestClient) -> None:
    create_response = api_client.post(
        "/target-roles",
        json={"canonical_name": "Junior Software Engineer", "aliases": ["Software Engineer I"]},
    )
    assert create_response.status_code == 201
    role_id = create_response.json()["id"]

    list_response = api_client.get("/target-roles")
    assert list_response.status_code == 200
    assert any(role["id"] == role_id for role in list_response.json())


def test_patch_target_role(api_client: TestClient) -> None:
    create_response = api_client.post(
        "/target-roles", json={"canonical_name": "Junior Software Engineer"}
    )
    role_id = create_response.json()["id"]

    patch_response = api_client.patch(f"/target-roles/{role_id}", json={"enabled": False})
    assert patch_response.status_code == 200
    assert patch_response.json()["enabled"] is False


def test_patch_missing_target_role_returns_404(api_client: TestClient) -> None:
    response = api_client.patch("/target-roles/999999", json={"enabled": False})
    assert response.status_code == 404

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

from backend.control_api import router
from backend.control_store import ControlStore

PASSWORD = "Example-Only-Password-2026"


@pytest.fixture
def api(tmp_path):
    store = ControlStore(tmp_path / "control.sqlite3")
    created = store.bootstrap_admin()
    app = FastAPI()
    app.state.control_store = store
    app.include_router(router)
    with TestClient(app) as client:
        yield client, store, created


def login_and_change(client, created):
    response = client.post("/api/auth/login", json={"username": created["user"]["username"], "password": created["temporary_password"]})
    assert response.status_code == 200
    session = response.json()
    initial = {"Authorization": "Bearer " + session["token"]}
    response = client.post("/api/auth/password", headers=initial, json={"current_password": created["temporary_password"], "new_password": PASSWORD})
    assert response.status_code == 200
    return {"Authorization": "Bearer " + response.json()["token"]}


def test_auth_forced_change_logout_and_no_cookie_fallback(api):
    client, store, created = api
    assert client.get("/api/plans").status_code == 401
    response = client.post("/api/auth/login", json={"username": "admin", "password": created["temporary_password"]})
    first = {"Authorization": "Bearer " + response.json()["token"]}
    assert client.get("/api/auth/me", headers=first).json()["must_change_password"]
    denied = client.get("/api/users", headers=first)
    assert denied.status_code == 403 and denied.json()["detail"]["code"] == "PASSWORD_CHANGE_REQUIRED"
    headers = login_and_change(client, created)
    assert client.get("/api/auth/me", headers=first).status_code == 401
    assert client.get("/api/users", headers=headers).status_code == 200
    assert client.post("/api/auth/logout", headers=headers).status_code == 200
    assert client.get("/api/auth/me", headers=headers).status_code == 401
    client.cookies.set("token", headers["Authorization"].split()[1])
    assert client.get("/api/plans").status_code == 401


def test_users_roles_scopes_and_plan_import_api(api):
    client, store, created = api
    admin_headers = login_and_change(client, created)
    body = {"username": "manager", "display_name": "Manager", "role": "manager", "terminals": ["cua_lo"]}
    created = client.post("/api/users", headers=admin_headers, json=body)
    assert created.status_code == 201 and "temporary_password" in created.json()
    manager_headers = login_and_change(client, created.json())
    assert client.get("/api/users", headers=manager_headers).status_code == 403
    plan = {"terminal": "cua_lo", "month": "2026-09", "metric": "tonnage", "amount": 10, "reference": "Internal planning document"}
    assert client.post("/api/plans", headers=manager_headers, json={**plan, "terminal": "ben_thuy"}).status_code == 403
    created = client.post("/api/plans/import", headers=manager_headers, json={"rows": [plan, {**plan, "metric": "teu"}]})
    assert created.status_code == 201 and created.json()["count"] == 2
    plan_id = created.json()["items"][0]["id"]
    assert client.post(f"/api/plans/{plan_id}/approve", headers=manager_headers).status_code == 422
    assert client.post(f"/api/plans/{plan_id}/approve", headers=manager_headers, json={'expected_revision': 1}).json()["status"] == "approved"
    assert client.get("/api/plans?terminal=ben_thuy", headers=manager_headers).status_code == 403
    assert client.get("/api/plans?period_type=voyage", headers=manager_headers).json()["total"] == 0
    assert client.get("/api/plans?period_type=month", headers=manager_headers).json()["total"] == 2
    assert client.patch("/api/users/2", headers=admin_headers, json={"is_active": False}).status_code == 200
    assert client.get("/api/plans", headers=manager_headers).status_code == 401


def test_login_uses_generic_errors_and_throttles(api):
    client, _, _ = api
    failures = [client.post("/api/auth/login", json={"username": "admin", "password": "incorrect-password"}) for _ in range(6)]
    assert [response.status_code for response in failures] == [401, 401, 401, 401, 401, 429]
    assert "incorrect-password" not in str([response.json() for response in failures])


def test_extra_fields_cannot_assign_roles_or_browser_snapshot_totals(api):
    client, _, created = api
    headers = login_and_change(client, created)
    invalid = {"terminal": "cua_lo", "month": "2026-09", "metric": "tonnage", "amount": 1, "status": "approved", "approved_by": 1}
    assert client.post("/api/plans", headers=headers, json=invalid).status_code == 422
    assert client.get("/api/plans", headers=headers).json()["total"] == 0
    assert client.patch("/api/users/1", headers=headers, json={"password_hash": "fake"}).status_code == 422

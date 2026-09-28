"""HTTP contract/security checks with temporary state and synthetic data only."""
from copy import deepcopy
import importlib
import os
from pathlib import Path
import re
import sqlite3
import subprocess
import sys
from datetime import datetime, timezone

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

from backend.corporate_api.auth import MachineStore
from backend.corporate_api.contracts import IDENTITY, MODELS, PRODUCTION
from backend.corporate_api.operation_contracts import OPERATION_PATHS

RESOURCE_KEYS = frozenset(MODELS)  # Existing S-domain behavior remains covered separately.
from backend.corporate_api.store import ExportStore

routes = importlib.import_module("backend.corporate_api.router")
PASSWORD = "synthetic-machine-http-password"
READ_AT = datetime.now(timezone.utc).isoformat()


def fixture_rows():
    report = {"reportDate": "20260921"}
    master = {**report, "createdDate": None, "modifiedDate": None}
    container = {**report, "companyId": "CNT", "originId": None, "containerWeight": 30.125,
                 "containerTEU": 2, "handlingMethodId": "SYN-METHOD-1", "finishDate": "20260916",
                 "containerOperatorId": None, "containerSizeId": "SYN-40F"}
    bulk = {**report, "finishDate": "20260916", "companyId": "CNT", "cargoTypeId": "SYN-BULK",
            "cargoCategoryId": "SYN-CARGO-1", "handlingMethodId": "SYN-METHOD-1", "bulkOriginId": None, "bulkWeight": 12.375}
    return {
        "contQuayVolumesCB": [{**container, "classId": "SYN-CLASS-1", "shipId": "SYN-SHIP-1", "shipOperatorId": None}],
        "contGateVolumesCB": [container],
        "bulkQuayVolumesCB": [{**bulk, "shipId": "SYN-SHIP-1", "shipAgentId": None, "shipClassId": "SYN-CLASS-1"}],
        "bulkGateVolumesCB": [{**bulk, "customerCode": None}],
        "shipDetails": [{**master, "shipId": f"SYN-SHIP-{index}", "shipFullName": f"Synthetic vessel {index}",
                         "shipIMO": None, "shipGroup": None, "flagState": None, "shipLOA": 100.125,
                         "shipBeam": None, "shipGRT": 0, "shipType": None, "shipDWT": None, "shipOwner": None}
                        for index in (1, 2, 3)],
        "customers": [{**report, "customerCode": "SYN-CUSTOMER-1", "customerNameVN": "Synthetic customer",
                       "customerNameEN": None, "customerTaxCode": None, "customerPhoneNum": None,
                       "customerAddress": None, "customerEmail": None, "isCarrier": False, "isAgent": None,
                       "customerStatus": None, "metadata": {"isDeleted": False, "createdDate": None, "modifiedDate": None}}],
        "cargoType": [{**master, "cargoTypeId": "SYN-BULK", "cargoTypeName": "Synthetic bulk"}],
        "cargoCategory": [{**master, "cargoTypeId": "SYN-BULK", "cargoParentId": None, "cargoId": "SYN-CARGO-1", "cargoName": "Synthetic cargo"}],
        "handlingMethodList": [{**master, "handlingMethodId": "SYN-METHOD-1", "handlingMethodName": "Synthetic handling"}],
        "class": [{**master, "classId": "SYN-CLASS-1", "className": "Synthetic class"}],
        "origins": [{**master, "originId": "SYN-ORIGIN-1", "originName": "Synthetic origin"}],
        "containerSize": [{**master, "containerSizeId": "SYN-40F", "localSzTp": "40F", "isoSzTp": None,
                           "sizeCode": None, "heightCode": None, "containerTypeCode": None}],
    }


def publish(exports, resource, rows):
    return exports.publish(resource, "CNT", rows, read_at=READ_AT, rule_version="synthetic-http-v1",
                           coverage=[["20260901", "20260930"]] if resource in PRODUCTION else None,
                           warnings=["Synthetic data for tests only"])


def query(resource, **changes):
    values = {"companyId": "CNT"}
    if resource in PRODUCTION:
        values.update(startDate="20260901", endDate="20260930")
    return {**values, **changes}


@pytest.fixture
def api(tmp_path, monkeypatch):
    monkeypatch.setenv("CORPORATE_API_ENABLED", "true")
    monkeypatch.setenv("DASHBOARD_STATE_PATH", str(tmp_path / "control.sqlite3"))
    monkeypatch.setenv("CORPORATE_STATE_PATH", str(tmp_path / "machine.sqlite3"))
    monkeypatch.setenv("CORPORATE_EXPORT_PATH", str(tmp_path / "exports.sqlite3"))
    machines = MachineStore()
    machines.create_client("corporate-reader", PASSWORD, company_ids=["CNT"], resources=sorted(RESOURCE_KEYS))
    exports = ExportStore()
    rows = fixture_rows()
    for resource, items in rows.items():
        publish(exports, resource, items)
    app = FastAPI()
    app.include_router(routes.router)
    app.state.corporate_auth = machines
    app.state.corporate_exports = exports
    with TestClient(app) as client:
        response = client.post("/api/login", json={"Username": "corporate-reader", "Password": PASSWORD})
        assert response.status_code == 200
        token = response.json()["accessToken"]
        headers = {"Authorization": f"Bearer {token}"}
        yield {"app": app, "client": client, "machines": machines, "exports": exports, "rows": rows,
               "token": token, "headers": headers, "path": tmp_path}


def test_thirteen_base_methods_and_no_roro_contract(api):
    paths = api["app"].openapi()["paths"]
    base_paths = {path: set(methods) for path, methods in paths.items() if "{" not in path}
    assert base_paths == {"/api/login": {"post"}, **{f"/api/{resource}": {"get"} for resource in RESOURCE_KEYS},
                          **{path: {"get"} for path in OPERATION_PATHS.values()}}
    assert not any("roro" in path.lower() for path in paths)
    assert "/api/containerSize/{record_id}" not in paths


def test_stale_export_returns_compact_error_instead_of_success(api):
    from datetime import timedelta
    old = (datetime.now(timezone.utc) - timedelta(days=3)).isoformat()
    with api['exports'].db() as db:
        db.execute("UPDATE export_versions SET read_at=? WHERE resource='origins'", (old,))
    response = api['client'].get('/api/origins', params=query('origins'), headers=api['headers'])
    assert response.status_code == 503
    assert response.headers['X-Error-Code'] == 'DATASET_STALE'
    assert response.headers['Retry-After'] == '300'
    assert set(response.json()) == {'data', 'code', 'message'}
    assert response.json()['data'] == [] and response.json()['code'] == '0'
    assert api["client"].get("/api/login").status_code == 405
    assert api["client"].post("/api/shipDetails", headers=api["headers"]).status_code == 405


@pytest.mark.parametrize("resource", sorted(RESOURCE_KEYS))
def test_all_dataset_dtos_are_serialized_with_metadata_and_precise_numbers(api, resource):
    response = api["client"].get(f"/api/{resource}", params=query(resource), headers=api["headers"])
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["code"] == "1" and len(body["data"]) == len(api["rows"][resource])
    assert set(body) == {"data", "code", "message"}
    for row, expected in zip(body["data"], api["rows"][resource]):
        assert re.fullmatch(r"\d{8}", row["reportDate"])
        assert set(row) == set(MODELS[resource].model_fields)
        assert {key: value for key, value in row.items() if key != "reportDate"} == {key: value for key, value in expected.items() if key != "reportDate"}
        for key in ("containerWeight", "containerTEU", "bulkWeight", "shipLOA", "shipGRT"):
            if key in row and row[key] is not None:
                assert isinstance(row[key], (float, int)) and not isinstance(row[key], bool)
    assert response.headers['X-Page'] == '1'
    assert response.headers['X-Limit'] == '20'
    assert int(response.headers['X-Total-Count']) == len(body['data'])
    assert response.headers['X-Has-Next'] == 'false'
    assert response.headers["X-Source-Read-At"] == READ_AT
    assert response.headers["X-Corporate-Contract"] == "S-v3-CNT-1"
    assert response.headers["Cache-Control"] == "no-store"
    assert response.headers["X-Content-Type-Options"] == "nosniff"


def test_get_requires_machine_token_and_denies_company_or_resource_before_export_read(api, monkeypatch):
    client = api["client"]
    absent = client.get("/api/customers", params=query("customers"))
    assert absent.status_code == 401 and absent.headers["WWW-Authenticate"] == "Bearer"
    assert absent.json()["code"] == "0"
    api["machines"].create_client("customers-only", PASSWORD, company_ids=["CNT"], resources=["customers"])
    token = api["machines"].login("customers-only", PASSWORD, "synthetic-client")["accessToken"]

    def no_read(*args, **kwargs):
        raise AssertionError("Unauthorized request must not read export rows")

    monkeypatch.setattr(api["exports"], "read", no_read)
    for resource, company, headers in [("shipDetails", "CNT", {"Authorization": f"Bearer {token}"}),
                                        ("customers", "OTHER", api["headers"]),
                                        ("customers", "cnt", api["headers"])]:
        response = client.get(f"/api/{resource}", params={"companyId": company}, headers=headers)
        assert response.status_code == 403
        assert response.json()["data"] == [] and response.json()["code"] == "0"


def test_dashboard_personal_session_is_rejected_by_corporate_api(api):
    from backend.control_store import ControlStore
    humans = ControlStore(api["path"] / "control.sqlite3")
    account = humans.bootstrap_admin("synthetic-human")
    token = humans.login("synthetic-human", account["temporary_password"], "synthetic-client")["token"]
    response = api["client"].get("/api/customers", params=query("customers"), headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 401 and response.json()["code"] == "0"


@pytest.mark.parametrize("resource,parameters", [
    ("shipDetails", {}), ("shipDetails", {"companyId": "CNT", "page": 0}),
    ("shipDetails", {"companyId": "CNT", "limit": 101}),
    ("shipDetails", {"companyId": "CNT", "snapshotId": "invalid"}),
    ("shipDetails", {"companyId": "CNT", "unknown": "private-value-not-for-errors"}),
    ("contQuayVolumesCB", {"companyId": "CNT"}),
    ("contQuayVolumesCB", {"companyId": "CNT", "startDate": "20260230", "endDate": "20260301"}),
    ("contQuayVolumesCB", {"companyId": "CNT", "startDate": "20260916"}),
    ("contQuayVolumesCB", {"companyId": "CNT", "startDate": "20260920", "endDate": "20260901"}),
    ("contGateVolumesCB", {"companyId": "CNT", "startDate": "20260901", "endDate": "20260930", "shipId": "private-value-not-for-errors"}),
    ("shipDetails", {"companyId": "CNT", "startDate": "20260901", "endDate": "20260930"}),
])
def test_query_validation_uses_safe_422_envelope(api, resource, parameters):
    response = api["client"].get(f"/api/{resource}", params=parameters, headers=api["headers"])
    assert response.status_code == 422
    assert response.json()["code"] == "0" and response.json()["data"] == []
    assert "private-value-not-for-errors" not in response.text
    assert "detail" not in response.json()
    assert response.headers["Cache-Control"] == "no-store"


@pytest.mark.parametrize("body", [
    {"Username": "corporate-reader", "Password": "private-body-secret", "extra": "private-body-secret"},
    {"Username": "corporate-reader", "Password": "private-body-secret" * 100},
    {"Username": "corporate-reader", "Password": {"value": "private-body-secret"}},
    {"Username": "corporate-reader"},
])
def test_login_validation_never_echoes_password_input(api, body):
    response = api["client"].post("/api/login", json=body)
    assert response.status_code == 422 and response.json()["code"] == "0"
    assert "private-body-secret" not in response.text
    with sqlite3.connect(api["machines"].path) as db:
        assert db.execute("SELECT COUNT(*) FROM machine_login_attempts").fetchone()[0] == 0


def test_disabled_endpoints_do_not_initialize_or_read_any_store(tmp_path, monkeypatch):
    monkeypatch.delenv("CORPORATE_API_ENABLED", raising=False)

    def prohibited(*args, **kwargs):
        raise AssertionError("Disabled corporate API must not access state")

    monkeypatch.setattr(routes, "default_auth", prohibited)
    monkeypatch.setattr(routes, "default_exports", prohibited)
    app = FastAPI()
    app.include_router(routes.router)
    with TestClient(app) as client:
        responses = [client.post("/api/login", json={"Username": "corporate-reader", "Password": PASSWORD})]
        responses.extend(client.get(f"/api/{resource}", params=query(resource), headers={"Authorization": "Bearer unused"}) for resource in RESOURCE_KEYS)
    assert all(response.status_code == 503 and response.headers["X-Error-Code"] == "CORPORATE_API_DISABLED" for response in responses)
    assert list(tmp_path.iterdir()) == []


def test_snapshot_pagination_remains_stable_after_new_publication(api):
    client, headers = api["client"], api["headers"]
    first = client.get("/api/shipDetails", params=query("shipDetails", limit=1), headers=headers)
    snapshot = first.headers["X-Snapshot-Id"]
    changed = deepcopy(api["rows"]["shipDetails"][:1])
    changed[0].update(shipId="SYN-SHIP-99", shipFullName="Synthetic replacement")
    publish(api["exports"], "shipDetails", changed)
    pinned = client.get("/api/shipDetails", params=query("shipDetails", page=2, limit=1, snapshotId=snapshot), headers=headers)
    assert pinned.status_code == 200 and pinned.json()["data"][0]["shipId"] == "SYN-SHIP-2"
    assert pinned.headers["X-Total-Count"] == "3"
    assert pinned.headers["X-Snapshot-Id"] == snapshot
    unpinned = client.get("/api/shipDetails", params=query("shipDetails", page=2), headers=headers)
    assert unpinned.status_code == 409 and unpinned.headers["X-Error-Code"] == "SNAPSHOT_REQUIRED"
    latest = client.get("/api/shipDetails", params=query("shipDetails"), headers=headers)
    assert latest.json()["data"][0]["shipId"] == "SYN-SHIP-99"
    assert latest.headers["X-Snapshot-Id"] != snapshot
    cross_resource = client.get("/api/customers", params=query("customers", snapshotId=snapshot), headers=headers)
    assert cross_resource.status_code == 410 and cross_resource.json()["code"] == "0"


@pytest.mark.parametrize("resource", sorted(set(IDENTITY) - {"containerSize"}))
def test_master_detail_routes_return_one_row_or_safe_404(api, resource):
    record_id = api["rows"][resource][0][IDENTITY[resource]]
    response = api["client"].get(f"/api/{resource}/{record_id}", params=query(resource), headers=api["headers"])
    assert response.status_code == 200 and len(response.json()["data"]) == 1
    assert response.json()["data"][0][IDENTITY[resource]] == record_id
    missing = api["client"].get(f"/api/{resource}/SYN-NOT-PUBLISHED", params=query(resource), headers=api["headers"])
    assert missing.status_code == 404 and missing.headers["X-Error-Code"] == "NOT_FOUND"
    assert missing.json()["data"] == []
    later_page = api["client"].get(f"/api/{resource}/{record_id}", params=query(resource, page=2), headers=api["headers"])
    assert later_page.status_code == 422


def test_unpublished_coverage_is_503_and_known_empty_coverage_is_200(api):
    client, headers = api["client"], api["headers"]
    uncovered = client.get("/api/bulkGateVolumesCB", params=query("bulkGateVolumesCB", startDate="20261001", endDate="20261002"), headers=headers)
    assert uncovered.status_code == 503 and uncovered.headers["X-Error-Code"] == "PERIOD_NOT_READY"
    assert uncovered.headers["Retry-After"] == "300"
    publish(api["exports"], "bulkGateVolumesCB", [])
    empty = client.get("/api/bulkGateVolumesCB", params=query("bulkGateVolumesCB"), headers=headers)
    assert empty.status_code == 200 and empty.json()["data"] == [] and empty.headers["X-Total-Count"] == "0"


def test_sqlite_error_is_safe_and_includes_retry_metadata(api, monkeypatch, caplog):
    private = "secret-server-and-password-never-returned"

    def fail(*args, **kwargs):
        raise sqlite3.OperationalError(private)

    monkeypatch.setattr(api["exports"], "read", fail)
    response = api["client"].get("/api/customers", params=query("customers"), headers=api["headers"])
    assert response.status_code == 503 and response.headers["X-Error-Code"] == "STORAGE_UNAVAILABLE"
    assert response.headers["Retry-After"] == "30"
    assert private not in response.text + caplog.text


def test_main_mount_keeps_human_and_machine_auth_separate_without_route_collision(api, monkeypatch):
    from backend import main
    from backend.control_store import ControlStore
    humans = ControlStore(api["path"] / "control.sqlite3")
    account = humans.bootstrap_admin("synthetic-human")
    human_token = humans.login("synthetic-human", account["temporary_password"], "synthetic-client")["token"]
    monkeypatch.setattr(main.app.state, "control_store", humans, raising=False)
    monkeypatch.setattr(main.app.state, "corporate_auth", api["machines"], raising=False)
    monkeypatch.setattr(main.app.state, "corporate_exports", api["exports"], raising=False)
    paths = main.app.openapi()["paths"]
    assert "post" in paths["/api/login"] and "post" in paths["/api/auth/login"]
    assert "get" in paths["/api/dashboard"] and "get" in paths["/api/customers"]
    with TestClient(main.app) as client:
        assert client.get("/api/auth/me", headers={"Authorization": f"Bearer {human_token}"}).status_code == 200
        assert client.get("/api/auth/me", headers=api["headers"]).status_code == 401
        assert client.get("/api/customers", params=query("customers"), headers=api["headers"]).status_code == 200
        assert client.get("/api/customers", params=query("customers"), headers={"Authorization": f"Bearer {human_token}"}).status_code == 401


def test_flat_backend_import_matches_docker_working_directory_without_state_creation(tmp_path):
    backend = Path(__file__).resolve().parents[1] / "backend"
    env = {**os.environ, "DASHBOARD_STATE_PATH": str(tmp_path / "control.sqlite3"),
           "CORPORATE_STATE_PATH": str(tmp_path / "corporate.sqlite3"),
           "CORPORATE_EXPORT_PATH": str(tmp_path / "exports.sqlite3"), "CORPORATE_API_ENABLED": "false"}
    script = "from main import app; from corporate_api.auth import MachineStore; paths=app.openapi()['paths']; assert '/api/login' in paths and '/api/auth/login' in paths; print('imports-ok')"
    result = subprocess.run([sys.executable, "-c", script], cwd=backend, env=env, capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, "Flat backend import failed"
    assert result.stdout.strip() == "imports-ok"
    assert list(tmp_path.iterdir()) == []

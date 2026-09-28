"""Operation-catalog HTTP and publication checks; isolated synthetic data only.

These checks do not connect to SQL Server or establish live source readiness.
"""
from datetime import datetime, timedelta, timezone
import importlib

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

from backend.corporate_api.auth import MachineStore
from backend.corporate_api.operation_contracts import OPERATION_IDENTITY, OPERATION_MODELS
from backend.corporate_api.store import ExportStore


routes = importlib.import_module("backend.corporate_api.router")
PASSWORD = "synthetic-operation-catalog-password"
PATHS = {
    "oprt.portEquipment": "/api/oprt/catalog/portEquipment",
    "oprt.portEquipType": "/api/oprt/catalog/portEquipType",
    "oprt.portWHYard": "/api/oprt/catalog/portWHYard",
    "oprt.portWHYardType": "/api/oprt/catalog/portWHYardType",
    "oprt.berths": "/api/oprt/catalog/berths",
    "oprt.jobType": "/api/oprt/catalog/jobType",
    "oprt.jobMethod": "/api/oprt/catalog/jobMethod",
    "oprt.deliveryMethod": "/api/oprt/catalog/deliveryMethod",
    "oprt.serviceType": "/api/oprt/catalog/serviceType",
    "oprt.cargoItems": "/api/oprt/catalog/cargoItems",
    "oprt.cargoGroups": "/api/oprt/catalog/cargoGroups",
    "oprt.unitMeasurement": "/api/oprt/catalog/unitMeasurement",
    "oprt.cargoDirect": "/api/oprt/catalog/cargoDirect",
    "oprt.operationLocationType": "/api/oprt/catalog/operationLocationType",
    "oprt.portOpTeam": "/api/oprt/portOpTeam",
    "oprt.portOpStaff": "/api/oprt/portOpStaff",
    "oprt.vesselType": "/api/oprt/catalog/vesselType",
    "oprt.equipments": "/api/oprt/catalog/equipments",
    "oprt.contwhYards": "/api/oprt/catalog/contwhYards",
    "oprt.contSizeType": "/api/oprt/catalog/contSizeType",
}


def _example(node, root):
    """Build synthetic valid values, not a claim about a production source."""
    if "$ref" in node:
        return _example(root["$defs"][node["$ref"].rsplit("/", 1)[1]], root)
    if "const" in node:
        return node["const"]
    if "enum" in node:
        return node["enum"][0]
    if "anyOf" in node:
        options = [option for option in node["anyOf"] if option.get("type") != "null"]
        return _example(options[0], root)
    kind = node.get("type")
    if kind == "object":
        return {key: _example(value, root) for key, value in node["properties"].items()}
    if kind == "array":
        return []
    if kind == "boolean":
        return False
    if kind == "integer":
        return max(1, node.get("minimum", 0))
    if kind == "number":
        return max(1.25, node.get("minimum", 0))
    if node.get("format") == "date":
        return "2026-09-01"
    if node.get("format") == "date-time":
        return "2026-09-01T08:00:00+07:00"
    return "1"


def fixture_row(resource, index=1, **changes):
    model = OPERATION_MODELS[resource]
    schema = model.model_json_schema(mode="validation")
    row = _example(schema, schema)
    row.update(companyId="CNT", reportDate="2026-09-25", isDeleted=False,
               createdDate="2026-09-01T08:00:00+07:00",
               modifiedDate="2026-09-05T09:00:00+07:00")
    row[OPERATION_IDENTITY[resource]] = str(index)
    if "isUpdated" in model.model_fields:
        row["isUpdated"] = True
    row.update(changes)
    return model.model_validate(row).model_dump(mode="json")


def query(**changes):
    return {"companyId": "CNT", "startDate": "20260901", "endDate": "20260930", **changes}


def publish(exports, resource, rows, read_at=None):
    return exports.publish(resource, "CNT", rows,
                           read_at=read_at or datetime.now(timezone.utc).isoformat(),
                           rule_version="synthetic-operations-http-v1")


@pytest.fixture
def api(tmp_path, monkeypatch):
    monkeypatch.setenv("CORPORATE_API_ENABLED", "true")
    monkeypatch.setenv("DASHBOARD_STATE_PATH", str(tmp_path / "dashboard.sqlite3"))
    monkeypatch.setenv("CORPORATE_STATE_PATH", str(tmp_path / "machines.sqlite3"))
    monkeypatch.setenv("CORPORATE_EXPORT_PATH", str(tmp_path / "exports.sqlite3"))
    machines, exports = MachineStore(), ExportStore()
    machines.create_client("operation-reader", PASSWORD, company_ids=["CNT"], resources=sorted(PATHS))
    rows = {resource: [fixture_row(resource)] for resource in PATHS}
    rows["oprt.vesselType"] = [fixture_row("oprt.vesselType", index) for index in (1, 2, 3)]
    for resource, items in rows.items():
        publish(exports, resource, items)
    app = FastAPI()
    app.include_router(routes.router)
    app.state.corporate_auth, app.state.corporate_exports = machines, exports
    with TestClient(app) as client:
        response = client.post("/api/login", json={"Username": "operation-reader", "Password": PASSWORD})
        assert response.status_code == 200, response.text
        yield {"client": client, "app": app, "exports": exports, "machines": machines, "rows": rows,
               "headers": {"Authorization": "Bearer " + response.json()["accessToken"]}}


def test_exactly_twenty_operation_catalog_list_routes_and_no_unspecified_methods(api):
    paths = api["app"].openapi()["paths"]
    operation_paths = {path: set(methods) for path, methods in paths.items() if path.startswith("/api/oprt/")}
    assert operation_paths == {path: {"get"} for path in PATHS.values()}
    assert set(OPERATION_MODELS) == set(PATHS)
    for path in PATHS.values():
        assert api["client"].post(path, headers=api["headers"]).status_code == 405
        assert api["client"].get(path + "/1", params=query(), headers=api["headers"]).status_code == 404


@pytest.mark.parametrize("resource", sorted(PATHS))
def test_every_catalog_has_precise_wire_shape_and_compact_envelope(api, resource):
    response = api["client"].get(PATHS[resource], params=query(), headers=api["headers"])
    assert response.status_code == 200, response.text
    body = response.json()
    assert set(body) == {"data", "code", "message"}
    assert body["code"] == "1"
    assert len(body["data"]) == len(api["rows"][resource])
    for actual, expected in zip(body["data"], api["rows"][resource]):
        assert set(actual) == set(OPERATION_MODELS[resource].model_fields)
        assert {key: value for key, value in actual.items() if key != "reportDate"} == {
            key: value for key, value in expected.items() if key != "reportDate"}
        datetime.strptime(actual["reportDate"], "%Y-%m-%d")
        assert actual["companyId"] == "CNT"
        assert isinstance(actual[OPERATION_IDENTITY[resource]], str)
        assert "sourceDatabase" not in actual
        assert not any(key.startswith("_") for key in actual)
    assert response.headers["X-Total-Count"] == str(len(body["data"]))
    assert response.headers["X-Page"] == "1"
    assert response.headers["X-Limit"] == "20"
    assert response.headers["X-Total-Pages"] == "1"
    assert response.headers["X-Has-Next"] == "false"
    assert response.headers["Cache-Control"] == "no-store"
    assert response.headers["X-Content-Type-Options"] == "nosniff"


@pytest.mark.parametrize("resource", sorted(PATHS))
def test_each_catalog_requires_token_and_current_resource_grant(api, resource, monkeypatch):
    path = PATHS[resource]
    absent = api["client"].get(path, params=query())
    assert absent.status_code == 401
    assert absent.headers["WWW-Authenticate"] == "Bearer"
    api["machines"].create_client("s-only-reader", PASSWORD, company_ids=["CNT"], resources=["origins"])
    token = api["machines"].login("s-only-reader", PASSWORD, "test-host")["accessToken"]

    def no_read(*args, **kwargs):
        raise AssertionError("An unauthorized request must not read the export store")

    monkeypatch.setattr(api["exports"], "read", no_read)
    response = api["client"].get(path, params=query(), headers={"Authorization": "Bearer " + token})
    assert response.status_code == 403
    for company in ("OTHER", "cnt"):
        response = api["client"].get(path, params=query(companyId=company), headers=api["headers"])
        assert response.status_code == 403
        assert response.json()["data"] == []


def test_paging_pins_old_snapshot_while_new_publication_is_available(api):
    resource = "oprt.vesselType"
    first = api["client"].get(PATHS[resource], params=query(limit=2), headers=api["headers"])
    assert first.status_code == 200
    snapshot = first.headers["X-Snapshot-Id"]
    assert first.headers["X-Total-Count"] == "3"
    assert first.headers["X-Total-Pages"] == "2"
    assert first.headers["X-Has-Next"] == "true"
    missing = api["client"].get(PATHS[resource], params=query(page=2, limit=2), headers=api["headers"])
    assert missing.status_code == 409 and missing.headers["X-Error-Code"] == "SNAPSHOT_REQUIRED"
    publish(api["exports"], resource, [fixture_row(resource, 4)])
    second = api["client"].get(PATHS[resource], params=query(page=2, limit=2, snapshotId=snapshot), headers=api["headers"])
    assert second.status_code == 200
    assert [row["vesselTypeId"] for row in first.json()["data"] + second.json()["data"]] == ["1", "2", "3"]
    assert second.headers["X-Snapshot-Id"] == snapshot
    assert second.headers["X-Total-Count"] == "3"
    assert second.headers["X-Has-Next"] == "false"
    fresh = api["client"].get(PATHS[resource], params=query(), headers=api["headers"])
    assert [row["vesselTypeId"] for row in fresh.json()["data"]] == ["4"]
    wrong_resource = api["client"].get(PATHS["oprt.jobType"], params=query(snapshotId=snapshot), headers=api["headers"])
    assert wrong_resource.status_code == 410


def test_delta_filter_uses_created_or_modified_and_keeps_soft_deletions(api):
    resource = "oprt.vesselType"
    # A row created during the requested period still qualifies if edited later.
    rows = [fixture_row(resource, 1, createdDate="2026-09-01T00:00:00+07:00", modifiedDate="2026-10-01T08:00:00+07:00"),
            fixture_row(resource, 2, createdDate="2026-08-01T08:00:00+07:00", modifiedDate="2026-09-30T23:59:59+07:00", isDeleted=True),
            fixture_row(resource, 3, createdDate="2026-08-01T08:00:00+07:00", modifiedDate="2026-08-30T23:59:59+07:00")]
    publish(api["exports"], resource, rows)
    response = api["client"].get(PATHS[resource], params=query(), headers=api["headers"])
    assert response.status_code == 200, response.text
    assert [row["vesselTypeId"] for row in response.json()["data"]] == ["1", "2"]
    assert response.json()["data"][1]["isDeleted"] in (1, True)


def test_delta_dates_convert_timezone_before_inclusive_filter(api):
    resource = "oprt.vesselType"
    rows = [fixture_row(resource, 1, createdDate="2026-08-31T17:00:00Z", modifiedDate=None),
            fixture_row(resource, 2, createdDate="2026-09-30T17:00:00Z", modifiedDate=None)]
    publish(api["exports"], resource, rows)
    response = api["client"].get(PATHS[resource], params=query(), headers=api["headers"])
    assert response.status_code == 200, response.text
    assert [row["vesselTypeId"] for row in response.json()["data"]] == ["1"]


def test_row_without_any_change_date_returns_filter_not_ready_instead_of_disappearing(api):
    resource = "oprt.vesselType"
    publish(api["exports"], resource, [fixture_row(resource, 1), fixture_row(resource, 2, createdDate=None, modifiedDate=None)])
    response = api["client"].get(PATHS[resource], params=query(), headers=api["headers"])
    assert response.status_code == 503
    assert response.headers["X-Error-Code"] == "FILTER_NOT_READY"
    assert response.json()["data"] == [] and response.json()["code"] == "0"


@pytest.mark.parametrize("values", [
    {"companyId": "CNT"},
    {"companyId": "CNT", "startDate": "20260901"},
    query(startDate="2026-09-01"),
    query(startDate="20260230"),
    query(startDate="19691231"),
    query(startDate="20261001", endDate="20260901"),
    query(limit=0), query(limit=101), query(page=0), query(shipId="1"),
])
def test_operation_date_and_filter_contracts_are_enforced(api, values):
    response = api["client"].get(PATHS["oprt.vesselType"], params=values, headers=api["headers"])
    assert response.status_code == 422, response.text
    assert set(response.json()) == {"data", "code", "message"}


def test_stale_and_unpublished_catalogs_are_not_successful_empty_lists(api):
    resource = "oprt.vesselType"
    old = (datetime.now(timezone.utc) - timedelta(days=3)).isoformat()
    publish(api["exports"], resource, [fixture_row(resource)], read_at=old)
    stale = api["client"].get(PATHS[resource], params=query(), headers=api["headers"])
    assert stale.status_code == 503 and stale.headers["X-Error-Code"] == "DATASET_STALE"
    with api["exports"].db() as db:
        db.execute("DELETE FROM export_versions WHERE resource=?", (resource,))
    absent = api["client"].get(PATHS[resource], params=query(), headers=api["headers"])
    assert absent.status_code == 503 and absent.headers["X-Error-Code"] == "DATASET_NOT_READY"


def test_get_reads_only_exports_even_if_source_connection_is_unavailable(api, monkeypatch):
    sql = importlib.import_module("backend.corporate_api.sql")

    def no_sql(*args, **kwargs):
        raise AssertionError("HTTP GET must not query source SQL")

    monkeypatch.setattr(sql, "query_source", no_sql)
    for resource, path in PATHS.items():
        response = api["client"].get(path, params=query(), headers=api["headers"])
        assert response.status_code == 200, (resource, response.text)


def test_duplicate_native_identity_cannot_replace_active_catalog(api):
    resource = "oprt.vesselType"
    before = api["exports"].describe()
    first, conflicting = fixture_row(resource, 1), fixture_row(resource, 1, vesselTypeName="Conflicting object")
    with pytest.raises(ValueError, match="Duplicate"):
        publish(api["exports"], resource, [first, conflicting])
    assert api["exports"].describe() == before


def test_company_in_rows_cannot_disagree_with_publication_company(api):
    with pytest.raises(ValueError):
        publish(api["exports"], "oprt.vesselType", [fixture_row("oprt.vesselType", companyId="OTHER")])


def test_failed_batch_rolls_back_prior_catalog_publication(api):
    before = api["exports"].describe()
    with pytest.raises(ValueError):
        with api["exports"].atomic_publication():
            publish(api["exports"], "oprt.vesselType", [fixture_row("oprt.vesselType", 4)])
            publish(api["exports"], "oprt.jobType", [fixture_row("oprt.jobType", 1), fixture_row("oprt.jobType", 1)])
    assert api["exports"].describe() == before


def _preview(rows, profile):
    from backend.corporate_api.manage_exports import FORMAT, profile_digest
    return {"format": FORMAT, "companyId": "CNT", "profileDigest": profile_digest(profile),
            "sourceReadAt": datetime.now(timezone.utc).isoformat(),
            "startDate": "20260901", "endDate": "20260930",
            "datasets": {resource: {"ready": True, "blockers": [], "coverage": [], "rows": items}
                         for resource, items in rows.items()}}


def test_catalog_publication_checks_missing_dependencies_even_if_ready_flag_is_true(tmp_path):
    from backend.corporate_api.manage_exports import publish_preview
    store = ExportStore(tmp_path / "oprt-exports.sqlite3")
    profile = {"approved": True}
    rows = {"oprt.cargoItems": [fixture_row("oprt.cargoItems", cargoGroupId="404")]}
    with pytest.raises(ValueError, match="unresolved reference"):
        publish_preview(_preview(rows, profile), profile, store, list(rows))
    assert store.describe() == []


def test_catalog_replacement_cannot_remove_ids_referenced_by_published_operations(tmp_path):
    from backend.corporate_api.manage_exports import publish_preview
    store = ExportStore(tmp_path / "oprt-exports.sqlite3")
    profile = {"approved": True}
    rows = {"oprt.cargoGroups": [fixture_row("oprt.cargoGroups", 1)],
            "oprt.cargoItems": [fixture_row("oprt.cargoItems", cargoGroupId="1")]}
    publish_preview(_preview(rows, profile), profile, store, list(rows))
    before = store.describe()
    rows = {"oprt.cargoGroups": [fixture_row("oprt.cargoGroups", 2)]}
    with pytest.raises(ValueError, match="reference"):
        publish_preview(_preview(rows, profile), profile, store, list(rows))
    assert store.describe() == before


def test_list_references_require_each_target_id_before_publication(tmp_path):
    from backend.corporate_api.manage_exports import publish_preview
    store = ExportStore(tmp_path / "oprt-exports.sqlite3")
    profile = {"approved": True}
    rows = {"oprt.portEquipType": [fixture_row("oprt.portEquipType", 1)],
            "oprt.operationLocationType": [fixture_row("oprt.operationLocationType", 1)],
            "oprt.portEquipment": [fixture_row("oprt.portEquipment", equipmentTypeId="1",
                                                 operationLocationTypeId=["1", "404"])]}
    with pytest.raises(ValueError, match="unresolved reference"):
        publish_preview(_preview(rows, profile), profile, store, list(rows))
    assert store.describe() == []

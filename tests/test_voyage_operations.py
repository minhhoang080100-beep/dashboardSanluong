"""Operation table filters never redefine voyage production or source facts."""

from copy import deepcopy
from datetime import date, datetime
from decimal import Decimal
import importlib

from fastapi.testclient import TestClient
import pytest

from backend import repository

main = importlib.import_module("backend.main")


@pytest.fixture(autouse=True)
def fixed_today(monkeypatch):
    monkeypatch.setattr(repository, "vietnam_today", lambda: date(2026, 9, 12))


def operation(identifier, weight="10", quantity="0", unit="TAN", shift_id="2", shift_code="C2"):
    weight = None if weight is None else Decimal(weight)
    quantity = None if quantity is None else Decimal(quantity)
    return {
        "kind": "fact", "terminal_id": "cua_lo", "terminal_name": "Test terminal",
        "id": str(identifier), "operation_code": f"TEST-{identifier}",
        "operation_day": date(2026, 9, 11), "vessel_id": "101",
        "vessel_name": "Synthetic vessel", "voyage_code": "TEST-101",
        "arrival_at": datetime(2026, 9, 11, 17), "departure_at": None,
        "cargo_name": "Synthetic cargo", "direction_id": 1,
        "customer_id": "customer-test", "customer_name": "Synthetic customer",
        "native_weight": weight, "tonne_factor": Decimal(1) if unit == "TAN" else None,
        "unit_code": unit, "unit_name": unit, "record_count": 1,
        "known_weight_count": int(weight is not None), "missing_weight_count": int(weight is None),
        "container_row_count": 0, "missing_quantity_count": 0,
        "negative_value_count": int(any(value is not None and value < 0 for value in (weight, quantity))),
        "teu": Decimal(0), "latest_operation_at": datetime(2026, 9, 11, 19),
        "job_method": "Synthetic method", "job_method_code": "METHOD-TEST",
        "quantity": quantity, "quantity_unit": "CHIEC", "quantity_unit_name": "CHIEC",
        "shift_id": shift_id, "shift_code": shift_code,
    }


@pytest.fixture
def mixed_rows():
    return [operation(1, "10", "0"), operation(2, "0", "0"),
            operation(3, None, "0"), operation(4, None, "2"),
            operation(5, "-3", "0"), operation(6, "0", "-2"),
            operation(7, "7", "0", unit="M3"), operation(8, "0", None),
            operation(9, None, None, shift_id=None, shift_code=None)]


def stub_repository(monkeypatch, rows):
    repo = repository.DashboardRepository()
    calls = []

    def execute(query, params):
        calls.append((query, params))
        return deepcopy(sorted(rows, key=lambda row: int(row["id"]), reverse=True))

    monkeypatch.setattr(repo, "_execute_query", execute)
    return repo, calls


def detail(repo, operation_filter="all", page=1, page_size=25):
    return repo.get_voyage_detail("cua_lo", 101, "2026-09-01", "2026-09-12",
                                  page, page_size, operation_filter)


def test_filtering_changes_only_table_and_paginates_after_filtering(monkeypatch, mixed_rows):
    repo, calls = stub_repository(monkeypatch, mixed_rows)
    original_rows = deepcopy(mixed_rows)
    all_rows = detail(repo)
    with_values = detail(repo, "with_values", page=2, page_size=2)
    missing = detail(repo, "missing_weight", page_size=2)

    for response in (with_values, missing):
        for key in ("header", "summary", "cargo", "daily", "native_units"):
            assert response[key] == all_rows[key]
        assert response["summary"]["record_count"] == 9
        assert response["summary"]["tonnage"] == 7
        assert response["operations"]["counts"] == {"all": 9, "with_values": 5, "missing_weight": 3}
        assert response["operations"]["total_all"] == 9

    assert with_values["operations"]["total"] == 5
    assert with_values["operations"]["total_pages"] == 3
    assert [row["id"] for row in with_values["operations"]["rows"]] == ["5", "4"]
    assert missing["operations"]["total"] == 3
    assert missing["operations"]["total_pages"] == 2
    assert [row["id"] for row in missing["operations"]["rows"]] == ["9", "4"]
    assert mixed_rows == original_rows
    assert len(calls) == 3
    assert calls[0] == calls[1] == calls[2]  # Same facts, source scope and ordering.


def test_native_nonzero_filter_keeps_negative_nonmass_and_unknown_weight_rows(monkeypatch, mixed_rows):
    repo, _ = stub_repository(monkeypatch, mixed_rows)
    response = detail(repo, "with_values")
    rows = {row["id"]: row for row in response["operations"]["rows"]}

    assert list(rows) == ["7", "6", "5", "4", "1"]
    assert rows["7"]["weight"] == 7 and rows["7"]["weight_unit"] == "M3"
    assert rows["7"]["tonnage"] is None and rows["7"]["teu"] == 0
    assert rows["6"]["quantity"] == -2 and rows["6"]["tonnage"] == 0
    assert rows["5"]["weight"] == -3
    assert rows["4"]["weight"] is None and rows["4"]["quantity"] == 2
    assert rows["4"]["tonnage"] is None and rows["4"]["teu"] == 0


def test_missing_weight_uses_native_null_and_keeps_unknown_unit_separate(monkeypatch, mixed_rows):
    repo, _ = stub_repository(monkeypatch, mixed_rows)
    response = detail(repo, "missing_weight")
    rows = response["operations"]["rows"]
    assert [row["id"] for row in rows] == ["9", "4", "3"]
    assert all(row["weight"] is None for row in rows)
    assert [row["quantity"] for row in rows] == [None, 2, 0]
    assert response["operations"]["counts"]["missing_weight"] == 3
    assert response["summary"]["record_count"] == 9


@pytest.mark.parametrize("operation_filter,rows", [
    ("with_values", [operation(1, "0", "0"), operation(2, None, "0")]),
    ("missing_weight", [operation(1, "0", None), operation(2, "5", "0")]),
])
def test_empty_filtered_first_page_preserves_existing_voyage(monkeypatch, operation_filter, rows):
    repo, _ = stub_repository(monkeypatch, rows)
    response = detail(repo, operation_filter)
    assert response["header"]["voyage_id"] == "101"
    assert response["summary"]["record_count"] == 2
    assert response["operations"]["total_all"] == 2
    assert response["operations"]["total"] == 0
    assert response["operations"]["total_pages"] == 0
    assert response["operations"]["page"] == 1
    assert response["operations"]["rows"] == []
    with pytest.raises(ValueError):
        detail(repo, operation_filter, page=2)


@pytest.mark.parametrize("operation_filter", ["invalid", "ALL", "", None, True, [], {"all": True}])
def test_invalid_filters_fail_before_source_read(monkeypatch, operation_filter):
    repo, calls = stub_repository(monkeypatch, [])
    with pytest.raises(ValueError):
        detail(repo, operation_filter)
    assert calls == []


def test_filter_metadata_and_shift_fields_preserve_source_identity(monkeypatch):
    rows = [operation(1, shift_id=2, shift_code="C2"),
            operation(2, shift_id=None, shift_code=None)]
    repo, calls = stub_repository(monkeypatch, rows)
    response = detail(repo, "with_values")
    assert response["operations"]["filter"] == "with_values"
    assert response["meta"]["filters"]["operation_filter"] == "with_values"
    assert response["meta"]["filters"]["start_date"] == "2026-09-01"
    assert response["meta"]["filters"]["end_date"] == "2026-09-12"
    assert response["meta"]["read_consistency"] == "single_fact_set"
    assert [(row["shift_id"], row["shift_code"]) for row in response["operations"]["rows"]] == [(None, None), ("2", "C2")]
    assert "CAST(t.shiftId AS nvarchar(128)) AS shift_id" in calls[0][0]
    assert "LEFT JOIN SmartTOS.dbo.Shift sh ON t.shiftId = sh.shiftId" in calls[0][0]


def test_default_api_keeps_all_headers_and_filter_is_opt_in(monkeypatch, mixed_rows):
    repo, calls = stub_repository(monkeypatch, mixed_rows)
    monkeypatch.setattr(main, "dashboard_repo", repo)
    base = "/api/voyages/cua_lo/101?start_date=2026-09-01&end_date=2026-09-12"
    with TestClient(main.app) as client:
        default = client.get(base)
        explicit_all = client.get(base + "&operation_filter=all")
        filtered = client.get(base + "&operation_filter=with_values&page=3&page_size=2")

    assert default.status_code == explicit_all.status_code == filtered.status_code == 200
    default, explicit_all, filtered = default.json(), explicit_all.json(), filtered.json()
    assert default["operations"] == explicit_all["operations"]
    assert default["operations"]["filter"] == "all"
    assert default["operations"]["total"] == default["summary"]["record_count"] == 9
    assert [row["id"] for row in default["operations"]["rows"]] == [str(i) for i in range(9, 0, -1)]
    assert filtered["operations"]["filter"] == "with_values"
    assert filtered["operations"]["total"] == 5
    assert [row["id"] for row in filtered["operations"]["rows"]] == ["1"]
    assert filtered["summary"] == default["summary"]
    assert len(calls) == 3


def test_api_rejects_unknown_filter_without_query_and_empty_page_two_with_422(monkeypatch):
    repo, calls = stub_repository(monkeypatch, [operation(1, "0", "0")])
    monkeypatch.setattr(main, "dashboard_repo", repo)
    base = "/api/voyages/cua_lo/101?start_date=2026-09-01&end_date=2026-09-12"
    with TestClient(main.app) as client:
        unknown = client.get(base + "&operation_filter=invalid")
        assert unknown.status_code == 422
        assert calls == []
        first = client.get(base + "&operation_filter=with_values")
        assert first.status_code == 200 and first.json()["operations"]["total_pages"] == 0
        second = client.get(base + "&operation_filter=with_values&page=2")
        assert second.status_code == 422
    assert len(calls) == 2

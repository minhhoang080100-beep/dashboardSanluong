"""Voyage calendar trimming preserves reported facts; no live SQL is accessed."""

from copy import deepcopy
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
import importlib

from fastapi.testclient import TestClient
import pytest

from backend import repository

main = importlib.import_module("backend.main")


@pytest.fixture(autouse=True)
def fixed_today(monkeypatch):
    monkeypatch.setattr(repository, "vietnam_today", lambda: date(2026, 9, 12))


def operation(identifier, day, weight="10", arrival=None, departure=None):
    weight_value = None if weight is None else Decimal(weight)
    return {
        "kind": "fact", "terminal_id": "cua_lo", "terminal_name": "Test terminal",
        "id": str(identifier), "operation_code": f"TEST-{identifier}",
        "operation_day": date.fromisoformat(day), "vessel_id": "101",
        "vessel_name": "Synthetic vessel", "voyage_code": "TEST-101",
        "arrival_at": arrival, "departure_at": departure,
        "cargo_name": "Synthetic cargo", "direction_id": 1,
        "customer_id": "customer-test", "customer_name": "Synthetic customer",
        "native_weight": weight_value, "tonne_factor": Decimal(1),
        "unit_code": "TAN", "unit_name": "TAN", "record_count": 1,
        "known_weight_count": int(weight_value is not None),
        "missing_weight_count": int(weight_value is None),
        "container_row_count": 0, "missing_quantity_count": 0,
        "negative_value_count": int(weight_value is not None and weight_value < 0),
        "teu": Decimal(0), "latest_operation_at": datetime.fromisoformat(day + "T19:00:00"),
        "job_method": "Synthetic method", "job_method_code": "METHOD-TEST",
        "quantity": Decimal(1), "quantity_unit": "CHIEC", "quantity_unit_name": "CHIEC",
    }


def stub_repository(monkeypatch, rows):
    repo = repository.DashboardRepository()
    calls = []

    def execute(query, params):
        calls.append(params)
        return deepcopy(sorted(rows, key=lambda row: (row["operation_day"], int(row["id"])), reverse=True))

    monkeypatch.setattr(repo, "_execute_query", execute)
    return repo, calls


def calendar_days(start, end):
    day = date.fromisoformat(start)
    finish = date.fromisoformat(end)
    return [(day + timedelta(days=i)).isoformat() for i in range((finish - day).days + 1)]


@pytest.mark.parametrize("value,expected", [
    (date(2026, 9, 11), date(2026, 9, 11)),
    (datetime(2026, 9, 11, 17), date(2026, 9, 11)),
    ("2026-09-11", date(2026, 9, 11)),
    ("2026-09-11T17:00:00", date(2026, 9, 11)),
    ("2026-09-10T22:30:00Z", date(2026, 9, 11)),
    (datetime(2026, 9, 10, 22, 30, tzinfo=timezone.utc), date(2026, 9, 11)),
    ("2026-09-11T01:00:00+14:00", date(2026, 9, 10)),
    (None, None), ("", None), ("not-a-date", None), ("2026-99-01", None),
    (datetime(1, 1, 1), None), (date(1899, 12, 31), None), ("0001-01-01T00:00:00", None),
    ("0001-01-01T00:00:00+14:00", None),
])
def test_calendar_day_uses_vietnam_day_and_rejects_invalid_source_timestamps(value, expected):
    assert repository._voyage_calendar_day(value) == expected


def test_recent_arrival_trims_only_voyage_daily_not_dashboard_period(monkeypatch):
    arrival = datetime(2026, 9, 11, 17)
    rows = [operation(1, "2026-09-11", "12", arrival), operation(2, "2026-09-12", "8", arrival)]
    repo, calls = stub_repository(monkeypatch, rows)

    dashboard = repo.get_dashboard("2026-09-01", "2026-09-12", "cua_lo")
    detail = repo.get_voyage_detail("cua_lo", 101, "2026-09-01", "2026-09-12")

    assert [row["date"] for row in dashboard["daily_history"]] == calendar_days("2026-09-01", "2026-09-12")
    assert [row["date"] for row in detail["daily"]] == ["2026-09-11", "2026-09-12"]
    assert detail["daily"] == dashboard["daily_history"][-2:]
    assert detail["summary"]["tonnage"] == dashboard["overview"]["total_tonnage"] == 20
    assert detail["operations"]["total"] == detail["summary"]["record_count"] == 2
    assert detail["meta"]["filters"]["start_date"] == "2026-09-01"
    assert detail["meta"]["filters"]["end_date"] == "2026-09-12"
    assert calls[1] == (date(2026, 9, 1), date(2026, 9, 13), 101)


@pytest.mark.parametrize("arrival", [None, datetime(1, 1, 1), "invalid-timestamp"])
def test_unavailable_arrival_starts_at_first_operation_and_keeps_internal_zero_days(monkeypatch, arrival):
    rows = [operation(1, "2026-09-07", None, arrival), operation(2, "2026-09-09", "5", arrival)]
    repo, _ = stub_repository(monkeypatch, rows)
    detail = repo.get_voyage_detail("cua_lo", 101, "2026-09-01", "2026-09-12")

    assert [row["date"] for row in detail["daily"]] == calendar_days("2026-09-07", "2026-09-12")
    assert detail["daily"][0]["record_count"] == 1
    assert detail["daily"][0]["tonnage"] is None
    assert detail["daily"][1]["record_count"] == 0
    assert detail["daily"][1]["tonnage"] == 0
    assert detail["summary"]["tonnage"] == 5


def test_arrival_before_reporting_period_never_extends_calendar_outside_filter(monkeypatch):
    rows = [operation(1, "2026-09-07", arrival=datetime(2026, 8, 30, 23))]
    repo, _ = stub_repository(monkeypatch, rows)
    detail = repo.get_voyage_detail("cua_lo", 101, "2026-09-01", "2026-09-12")
    assert [row["date"] for row in detail["daily"]] == calendar_days("2026-09-01", "2026-09-12")


def test_departure_clips_padding_inclusively_without_removing_idle_days_in_port(monkeypatch):
    arrival, departure = datetime(2026, 9, 6, 22), datetime(2026, 9, 9, 5)
    rows = [operation(1, "2026-09-07", "4", arrival, departure), operation(2, "2026-09-09", "6", arrival, departure)]
    repo, _ = stub_repository(monkeypatch, rows)
    detail = repo.get_voyage_detail("cua_lo", 101, "2026-09-01", "2026-09-12")

    assert [row["date"] for row in detail["daily"]] == calendar_days("2026-09-06", "2026-09-09")
    assert [row["tonnage"] for row in detail["daily"]] == [0, 4, 0, 6]
    assert sum(row["record_count"] for row in detail["daily"]) == 2


def test_inconsistent_arrival_departure_cannot_remove_null_zero_or_negative_operation_days(monkeypatch):
    arrival, departure = datetime(2026, 9, 10, 9), datetime(2026, 9, 8, 18)
    rows = [operation(1, "2026-09-07", "-2", arrival, departure),
            operation(2, "2026-09-09", None, arrival, departure),
            operation(3, "2026-09-11", "0", arrival, departure)]
    repo, _ = stub_repository(monkeypatch, rows)
    detail = repo.get_voyage_detail("cua_lo", 101, "2026-09-01", "2026-09-12")

    assert [row["date"] for row in detail["daily"]] == calendar_days("2026-09-07", "2026-09-11")
    actual_days = {row["date"] for row in detail["daily"] if row["record_count"]}
    assert actual_days == {"2026-09-07", "2026-09-09", "2026-09-11"}
    assert [row["weight"] for row in detail["operations"]["rows"]] == [0, None, -2]
    assert sum(row["record_count"] for row in detail["daily"]) == detail["summary"]["record_count"] == 3
    assert sum(row["tonnage"] or 0 for row in detail["daily"]) == detail["summary"]["tonnage"] == -2


def test_utc_arrival_and_departure_use_vietnam_calendar_in_integrated_detail(monkeypatch):
    rows = [operation(1, "2026-09-11", "10", "2026-09-10T22:30:00Z", "2026-09-11T18:30:00Z")]
    repo, _ = stub_repository(monkeypatch, rows)
    detail = repo.get_voyage_detail("cua_lo", 101, "2026-09-01", "2026-09-12")
    assert [row["date"] for row in detail["daily"]] == ["2026-09-11", "2026-09-12"]


def test_http_daily_window_and_totals_are_independent_of_operation_page(monkeypatch):
    arrival = datetime(2026, 9, 9, 6)
    rows = [operation(1, "2026-09-09", "3", arrival),
            operation(2, "2026-09-11", "5", arrival),
            operation(3, "2026-09-12", "7", arrival)]
    repo, calls = stub_repository(monkeypatch, rows)
    monkeypatch.setattr(main, "dashboard_repo", repo)

    with TestClient(main.app) as client:
        first = client.get("/api/voyages/cua_lo/101?start_date=2026-09-01&end_date=2026-09-12&page=1&page_size=1")
        last = client.get("/api/voyages/cua_lo/101?start_date=2026-09-01&end_date=2026-09-12&page=3&page_size=1")

    assert first.status_code == last.status_code == 200
    first, last = first.json(), last.json()
    assert first["daily"] == last["daily"]
    assert first["summary"] == last["summary"]
    assert [row["date"] for row in first["daily"]] == calendar_days("2026-09-09", "2026-09-12")
    assert first["summary"]["tonnage"] == 15
    assert first["operations"]["rows"][0]["id"] == "3"
    assert last["operations"]["rows"][0]["id"] == "1"
    assert first["operations"]["total"] == last["operations"]["total"] == 3
    assert len(calls) == 2  # One unchanged source read per response, not per panel.

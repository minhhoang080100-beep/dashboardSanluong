"""Report snapshots retain exact source scope while bounding work and memory."""

from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from datetime import date, datetime
from decimal import Decimal
import json
from threading import Event, Lock

import pytest

from backend import repository
from backend.database import DatabaseUnavailable
from backend.reporting import (ReportingService, ReportBusy, ReportCapacityError,
                               ReportSnapshotNotFound)


@pytest.fixture(autouse=True)
def fixed_today(monkeypatch):
    monkeypatch.setattr(repository, "vietnam_today", lambda: date(2026, 9, 13))


def fact(identifier, *, day=12, terminal="cua_lo", voyage="101", source_voyage=None,
         weight="10", quantity="0", unit="TAN", cargo="Stone", customer="7", shift="2"):
    operation_day = date(2026, 9, day)
    weight = Decimal(weight) if weight is not None else None
    quantity = Decimal(quantity) if quantity is not None else None
    container = cargo in repository.DashboardRepository.container_cargo_codes
    return {
        "kind": "fact", "id": str(identifier), "operation_code": f"SYNTHETIC-{identifier}",
        "operation_day": operation_day, "terminal_id": terminal,
        "terminal_name": repository.TERMINALS[terminal][1], "vessel_id": voyage,
        "source_voyage_id": voyage if source_voyage is None else source_voyage,
        "vessel_name": "Synthetic vessel", "voyage_code": "SYNTHETIC-101",
        "arrival_at": datetime(2026, 9, 12, 8), "departure_at": None,
        "latest_operation_at": datetime.combine(operation_day, datetime.min.time()),
        "cargo_name": cargo, "direction_id": 1, "customer_id": customer,
        "customer_name": "Synthetic customer", "native_weight": weight,
        "tonne_factor": Decimal(1) if unit == "TAN" else None,
        "unit_code": unit, "unit_name": unit, "record_count": 1,
        "known_weight_count": int(weight is not None), "missing_weight_count": int(weight is None),
        "container_row_count": int(container), "missing_quantity_count": int(container and quantity is None),
        "negative_value_count": int(any(value is not None and value < 0 for value in (weight, quantity))),
        "teu": (quantity or Decimal(0)) * (1 if cargo.startswith("20") else 2) if container else Decimal(0),
        "quantity": quantity, "quantity_unit": "CONT" if container else "CHIEC",
        "quantity_unit_name": "Synthetic unit", "shift_id": shift,
        "shift_code": "C2" if shift is not None else None,
        "job_method": "Synthetic method", "job_method_code": "SYNTHETIC-METHOD",
    }


class StubRepository(repository.DashboardRepository):
    def __init__(self, rows):
        self.rows = rows
        self.calls = []
        self.lock = Lock()
        self.entered = None
        self.release = None
        self.failure = False

    def _execute_query(self, query, params=()):
        with self.lock:
            self.calls.append((query, params))
        if self.entered is not None:
            self.entered.set()
            assert self.release.wait(3), "Test did not release source read"
        if self.failure:
            raise DatabaseUnavailable()
        start, end = params[:2]
        selected = [row for row in self.rows if start <= row["operation_day"] < end]
        if len(params) == 3:
            selected = [row for row in selected if row["vessel_id"] == str(params[2])]
        if "SmartTOS_BenThuy.dbo" not in query:
            selected = [row for row in selected if row["terminal_id"] == "cua_lo"]
        elif "SmartTOS.dbo" not in query:
            selected = [row for row in selected if row["terminal_id"] == "ben_thuy"]
        return deepcopy(selected)


@pytest.fixture
def mixed():
    return [fact(1), fact(2, voyage=None, source_voyage="999", weight="7"),
            fact(1, terminal="ben_thuy", weight="5"), fact(4, weight=None, quantity="2"),
            fact(5, weight="3", unit="M3"), fact(6, weight="-2"),
            fact(7, cargo="20F", quantity="1", weight="1"),
            fact(8, cargo="40E", quantity="2", weight="2"),
            fact(9, weight="0", quantity="0", customer=None, shift=None),
            fact(10, day=11, weight="4"), fact(11, day=10, weight="9")]


def report(service, **kwargs):
    return service.get_report("2026-09-11", "2026-09-12", "all", **kwargs)


def report_id(service):
    return report(service)["meta"]["report_id"]


def test_all_report_panels_and_source_drill_preserve_orphan_mass(mixed):
    repo = StubRepository(mixed)
    service = ReportingService(repo)
    result = report(service)
    drill = service.drilldown(result["meta"]["report_id"], page_size=100)
    assert len(repo.calls) == 1
    assert result["overview"]["total_tonnage"] == drill["summary"]["tonnage"] == 27
    assert result["overview"]["record_count"] == drill["operations"]["total"] == 10
    assert result["overview"]["vessel_calls"] == 2
    assert result["meta"]["unassigned_voyage_totals"]["tonnage"] == 7
    assert sum(row["tonnage"] for row in result["voyages"]) == 20
    assert result["overview"]["total_teu"] == drill["summary"]["teu"] == 5
    assert drill["summary"]["tonnage_status"] == "partial"
    assert sum(row["record_count"] for row in drill["shifts"]) == 10
    assert sum(row["tonnage"] for row in drill["shifts"]) == 27
    assert len(drill["shifts"]) == 4  # Two terminals, two days, a distinct unknown shift.
    assert {row["terminal_id"] for row in result["terminals"]} == {"cua_lo", "ben_thuy"}
    assert result["meta"]["previous_period"]["record_count"] == 1
    orphan = service.drilldown(result["meta"]["report_id"], terminal="cua_lo", voyage_id="unassigned")
    assert orphan["summary"]["tonnage"] == 7
    assert orphan["operations"]["rows"][0]["source_voyage_id"] == "999"
    assert orphan["operations"]["rows"][0]["voyage_id"] is None


@pytest.mark.parametrize("filters,ids,total", [
    ({"day": "2026-09-11"}, {"10"}, 4),
    ({"terminal": "ben_thuy"}, {"1"}, 5),
    ({"cargo": "Hàng container"}, {"7", "8"}, 3),
    ({"customer_id": "7", "customer_terminal": "ben_thuy"}, {"1"}, 5),
    ({"customer_id": "unassigned", "customer_terminal": "cua_lo"}, {"9"}, 0),
    ({"issue": "missing_weight"}, {"4"}, None),
    ({"issue": "unknown_unit"}, {"5"}, None),
    ({"issue": "negative"}, {"6"}, -2),
])
def test_precise_scope_filters(mixed, filters, ids, total):
    service = ReportingService(StubRepository(mixed))
    response = service.drilldown(report_id(service), **filters)
    assert {row["id"] for row in response["operations"]["rows"]} == ids
    assert response["summary"]["tonnage"] == total
    assert response["summary"]["record_count"] == len(ids)


def test_table_filter_paging_does_not_change_scoped_totals_or_source_facts(mixed):
    service = ReportingService(StubRepository(mixed))
    identifier = report_id(service)
    missing = service.drilldown(identifier, operation_filter="missing_weight", page_size=1)
    values = service.drilldown(identifier, operation_filter="with_values", page_size=2, page=4)
    assert missing["summary"] == values["summary"]
    assert missing["operations"]["counts"] == {"all": 10, "with_values": 9, "missing_weight": 1}
    assert missing["operations"]["rows"][0]["weight"] is None
    assert missing["operations"]["total_all"] == 10
    assert values["operations"]["total"] == 9
    assert values["operations"]["total_pages"] == 5
    empty = service.drilldown(identifier, terminal="ben_thuy", operation_filter="missing_weight")
    assert empty["summary"]["tonnage"] == 5
    assert empty["operations"]["total_pages"] == 0 and empty["operations"]["rows"] == []
    with pytest.raises(ValueError):
        service.drilldown(identifier, terminal="ben_thuy", operation_filter="missing_weight", page=2)


@pytest.mark.parametrize("filters", [
    {"day": "2026-09-10"}, {"day": datetime(2026, 9, 12)}, {"terminal": "invalid"},
    {"issue": "typo"}, {"customer_id": "7"}, {"voyage_id": "101"},
    {"terminal": "cua_lo", "customer_id": "7", "customer_terminal": "ben_thuy"},
    {"operation_filter": "typo"}, {"page": True}, {"page_size": 101},
])
def test_invalid_drill_scope_is_rejected_without_new_query(mixed, filters):
    repo = StubRepository(mixed)
    service = ReportingService(repo)
    identifier = report_id(service)
    with pytest.raises(ValueError):
        service.drilldown(identifier, **filters)
    assert len(repo.calls) == 1


def test_snapshot_scope_cannot_be_expanded_to_another_terminal(mixed):
    service = ReportingService(StubRepository(mixed))
    identifier = service.get_report("2026-09-12", "2026-09-12", "cua_lo")["meta"]["report_id"]
    with pytest.raises(ValueError):
        service.drilldown(identifier, terminal="ben_thuy")
    with pytest.raises(ValueError):
        service.get_voyage_from_report(identifier, "ben_thuy", 101)


def test_cached_reports_are_cloned_and_old_snapshots_survive_refresh(mixed):
    repo = StubRepository(mixed)
    service = ReportingService(repo)
    first = report(service)
    identifier = first["meta"]["report_id"]
    first["overview"]["total_tonnage"] = -999
    second = report(service)
    assert second["meta"]["report_id"] == identifier
    assert second["meta"]["cache"]["status"] == "hit"
    assert second["overview"]["total_tonnage"] == 27
    repo.rows.append(fact(12, weight="100"))
    latest = report(service, refresh=True)
    assert latest["meta"]["report_id"] != identifier
    assert latest["overview"]["total_tonnage"] == 127
    assert service.drilldown(identifier)["summary"]["tonnage"] == 27
    assert service.get_report_snapshot(identifier)["overview"]["total_tonnage"] == 27
    assert len(repo.calls) == 2


def test_cache_ttl_and_retention_expire_independently(mixed):
    clock = [0.0]
    repo = StubRepository(mixed)
    service = ReportingService(repo, cache_ttl=10, snapshot_ttl=90, clock=lambda: clock[0])
    first = report_id(service)
    clock[0] = 11
    second = report_id(service)
    assert second != first
    assert service.get_report_snapshot(first)["meta"]["report_id"] == first
    clock[0] = 90
    with pytest.raises(ReportSnapshotNotFound):
        service.get_report_snapshot(first)
    assert service.get_report_snapshot(second)["meta"]["report_id"] == second
    assert len(repo.calls) == 2


@pytest.mark.parametrize("options", [{"cache_ttl": float("nan")}, {"snapshot_ttl": float("inf")},
                                    {"wait_timeout": float("inf")}])
def test_cache_duration_configuration_cannot_remove_bounds(options):
    with pytest.raises(ValueError):
        ReportingService(StubRepository([]), **options)


def test_snapshot_capacity_evicts_lru_and_never_truncates_source_rows():
    repo = StubRepository([fact(1), fact(2)])
    service = ReportingService(repo, max_snapshots=1, max_snapshot_rows=2)
    first = report_id(service)
    second = report(service, refresh=True)["meta"]["report_id"]
    with pytest.raises(ReportSnapshotNotFound):
        service.get_report_snapshot(first)
    repo.rows.append(fact(3))
    with pytest.raises(ReportCapacityError):
        report(service, refresh=True)
    assert service.drilldown(second)["operations"]["total"] == 2
    assert service.get_metrics()["cache"]["stored_rows"] == 2


def test_export_is_json_safe_stable_and_cannot_mutate_retained_facts(mixed):
    clock = [0.0]
    service = ReportingService(StubRepository(mixed), clock=lambda: clock[0])
    identifier = report_id(service)
    first = service.export_snapshot(identifier)
    expected = json.dumps(first, sort_keys=True)
    clock[0] += 10
    assert json.dumps(service.export_snapshot(identifier), sort_keys=True) == expected
    first["operations"][0]["weight"] = 999
    first["report"]["meta"]["filters"]["terminal"] = "invalid"
    assert json.dumps(service.export_snapshot(identifier), sort_keys=True) == expected
    keys = [row["row_key"] for row in first["operations"]]
    assert len(set(keys)) == len(keys)
    assert all(row["source_id"] == row["id"] and row["source_type"] == "tally_shift" for row in first["operations"])
    assert all("cache" not in item["meta"] for item in [service.export_snapshot(identifier)["report"]])
    unknown = next(row for row in first["report"]["customers"] if row["customer_id"] is None)
    assert unknown["customer_key"] == "cua_lo:unassigned"
    assert unknown["drilldown_customer_id"] == "unassigned"


def test_export_drilldown_aggregates_once_and_keeps_filter_scope_explicit(mixed, monkeypatch):
    repo = StubRepository(mixed)
    service = ReportingService(repo)
    identifier = report_id(service)
    aggregation_calls = []
    original = repo._dashboard_from_rows

    def tracked_aggregation(*args):
        aggregation_calls.append(len(args[0]))
        return original(*args)

    monkeypatch.setattr(repo, "_dashboard_from_rows", tracked_aggregation)
    exported = service.export_drilldown(identifier, terminal="cua_lo", operation_filter="missing_weight")
    assert aggregation_calls == [9]
    assert len(repo.calls) == 1
    assert exported["report"]["summary"]["tonnage"] == 22
    assert exported["report"]["summary"]["record_count"] == 9
    assert exported["report"]["meta"]["exported_row_count"] == 1
    assert exported["report"]["meta"]["operations_filter"] == "missing_weight"
    assert exported["report"]["meta"]["selection"]["terminal"] == "cua_lo"
    assert exported["report"]["meta"]["snapshot_filters"]["terminal"] == "all"
    assert [row["id"] for row in exported["operations"]] == ["4"]
    assert sum(row["record_count"] for row in exported["shifts"]) == 9
    json.dumps(exported)
    with pytest.raises(ValueError):
        service.export_drilldown(identifier, operation_filter="typo")


def test_closed_source_export_preserves_precision_and_source_timestamp_changes():
    repo = StubRepository([fact(1, weight="1.0001")])
    service = ReportingService(repo)
    first = service.export_snapshot(report_id(service))["operations"][0]
    repo.rows[0]["native_weight"] = Decimal("1.0002")
    repo.rows[0]["latest_operation_at"] = datetime(2026, 9, 12, 1)
    second_id = report(service, refresh=True)["meta"]["report_id"]
    second = service.export_snapshot(second_id)["operations"][0]
    assert first["weight"] == second["weight"] == 1
    assert first["operation_date"] == second["operation_date"]
    assert first["source_native_weight"] == "1.0001"
    assert second["source_native_weight"] == "1.0002"
    assert first["source_operation_at"] != second["source_operation_at"]
    assert json.dumps(first, sort_keys=True) != json.dumps(second, sort_keys=True)


def test_snapshot_global_row_budget_is_shared_across_cached_periods():
    repo = StubRepository([fact(1), fact(2, day=11)])
    service = ReportingService(repo, max_snapshots=8, max_snapshot_rows=2)
    one = service.get_report("2026-09-11", "2026-09-11", "all")["meta"]["report_id"]
    two = service.get_report("2026-09-12", "2026-09-12", "all")["meta"]["report_id"]
    # Touch the oldest snapshot; the other becomes the eviction candidate.
    service.get_report_snapshot(one)
    three = service.get_report("2026-09-11", "2026-09-11", "all", refresh=True)["meta"]["report_id"]
    assert service.get_report_snapshot(one)["meta"]["report_id"] == one
    assert service.get_report_snapshot(three)["meta"]["report_id"] == three
    with pytest.raises(ReportSnapshotNotFound):
        service.get_report_snapshot(two)
    assert service.get_metrics()["cache"]["stored_rows"] == 2


def test_completed_owner_between_lookup_and_registration_does_not_repeat_sql():
    repo = StubRepository([fact(1)])
    service = ReportingService(repo)
    original_flight = service._flight
    once = [True]

    def interleaved_flight(key, loader):
        if once[0]:
            once[0] = False
            # Model another request completing after this caller's cache lookup.
            original_flight(key, loader)
        return original_flight(key, loader)

    service._flight = interleaved_flight
    report(service)
    assert len(repo.calls) == 1
    once[0] = True
    report(service, refresh=True)
    assert len(repo.calls) == 2


def test_voyage_detail_uses_same_snapshot_all_pages_and_clipped_dates(mixed):
    repo = StubRepository(mixed)
    service = ReportingService(repo)
    original = report(service)
    identifier = original["meta"]["report_id"]
    first = service.get_voyage_from_report(identifier, "cua_lo", 101, page_size=2)
    second = service.get_voyage_from_report(identifier, "cua_lo", 101, page=2, page_size=2, operation_filter="with_values")
    assert first["header"] == second["header"]
    assert first["summary"] == second["summary"]
    assert first["summary"]["tonnage"] == 15
    assert first["operations"]["total_all"] == 8
    assert first["meta"]["source_read_at"] == original["meta"]["source_read_at"]
    assert [row["date"] for row in first["daily"]] == ["2026-09-11", "2026-09-12"]
    assert len(repo.calls) == 1


def test_order_uses_source_timestamp_before_id():
    earlier, later = fact(99), fact(1)
    earlier["latest_operation_at"] = datetime(2026, 9, 12, 8)
    later["latest_operation_at"] = datetime(2026, 9, 12, 9)
    service = ReportingService(StubRepository([earlier, later]))
    rows = service.drilldown(report_id(service))["operations"]["rows"]
    assert [row["id"] for row in rows] == ["1", "99"]


def test_singleflight_coalesces_concurrent_read_and_shares_failure_without_caching():
    for fail in (False, True):
        repo = StubRepository([fact(1)])
        repo.entered, repo.release, repo.failure = Event(), Event(), fail
        service = ReportingService(repo)
        entered_flight = Event()
        original_flight = service._flight

        def tracked_flight(key, loader):
            with service._lock:
                if key in service._flights:
                    entered_flight.set()
            return original_flight(key, loader)

        service._flight = tracked_flight
        with ThreadPoolExecutor(max_workers=2) as pool:
            first = pool.submit(report, service)
            assert repo.entered.wait(1)
            second = pool.submit(report, service)
            assert entered_flight.wait(1)
            repo.release.set()
            if fail:
                for future in (first, second):
                    with pytest.raises(DatabaseUnavailable):
                        future.result(timeout=2)
            else:
                first, second = first.result(timeout=2), second.result(timeout=2)
                assert first["meta"]["report_id"] == second["meta"]["report_id"]
                assert second["meta"]["cache"]["status"] == "coalesced"
        assert len(repo.calls) == 1
        assert service.get_metrics()["cache"]["inflight"] == 0
        if fail:
            repo.failure = False
            assert report(service)["overview"]["record_count"] == 1
            assert len(repo.calls) == 2


def test_concurrent_distinct_reads_and_waiters_are_bounded():
    repo = StubRepository([fact(1)])
    repo.entered, repo.release = Event(), Event()
    service = ReportingService(repo, max_inflight=1, wait_timeout=0.02)
    with ThreadPoolExecutor(max_workers=1) as pool:
        owner = pool.submit(report, service)
        assert repo.entered.wait(1)
        with pytest.raises(ReportBusy):
            service.get_report("2026-09-12", "2026-09-12", "cua_lo")
        with pytest.raises(ReportBusy):
            report(service)
        repo.release.set()
        assert owner.result(timeout=2)["overview"]["record_count"] == 1
    assert len(repo.calls) == 1


def test_whole_voyage_progress_is_independent_of_reporting_period_and_cached():
    old = fact(2, day=1, weight="90")
    old["operation_day"] = date(2020, 1, 1)
    old["latest_operation_at"] = datetime(2020, 1, 1)
    repo = StubRepository([fact(1), old])
    service = ReportingService(repo)
    period = service.get_report("2026-09-12", "2026-09-12", "cua_lo")
    progress = service.get_voyage_progress("cua_lo", 101)
    assert period["overview"]["total_tonnage"] == 10
    assert progress["summary"]["tonnage"] == 100
    assert progress["meta"]["scope"] == "whole_voyage"
    assert progress["header"]["first_operation_date"] == "2020-01-01"
    assert repo.calls[1][1] == (date(1900, 1, 1), date(2026, 9, 14), 101)
    assert len(progress["shifts"]) == 2 and "daily" not in progress
    progress["summary"]["tonnage"] = -999
    assert service.get_voyage_progress("cua_lo", 101)["summary"]["tonnage"] == 100
    assert len(repo.calls) == 2


def test_query_bounds_include_previous_period_without_dropping_nonphysical_throughput():
    repo = StubRepository([fact(1, voyage=None, source_voyage="999")])
    data = repo.read_report("2026-09-12", "2026-09-12", "all")
    query, params = repo.calls[0]
    assert params == (date(2026, 9, 11), date(2026, 9, 13)) * 2
    assert "AS source_voyage_id" in query
    assert "CASE WHEN " + repo.physical_voyage_filter in query
    assert "AND " + repo.physical_voyage_filter not in query
    assert data["report"]["overview"]["total_tonnage"] == 10
    assert data["report"]["overview"]["vessel_calls"] == 0
    assert len(data["rows"]) == 1


def test_metrics_have_bounded_labels_and_do_not_expose_facts_or_queries():
    service = ReportingService(StubRepository([fact(1)]))
    identifier = report_id(service)
    service.drilldown(identifier)
    with pytest.raises(ReportSnapshotNotFound):
        service.drilldown("synthetic-untrusted-input")
    metrics = service.get_metrics()
    assert metrics["operations"]["source_read"]["success_count"] == 1
    assert metrics["operations"]["drilldown"]["failure_count"] == 1
    assert metrics["operations"]["drilldown"]["request_count"] == 2
    serialized = json.dumps(metrics)
    for forbidden in ("SYNTHETIC", "SELECT", "synthetic-untrusted-input", "Synthetic customer", identifier):
        assert forbidden not in serialized
    assert all(values["latency_ms_total"] >= 0 for values in metrics["operations"].values())

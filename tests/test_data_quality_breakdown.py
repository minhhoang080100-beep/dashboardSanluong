"""Describe unweighed rows without changing mass, TEU or voyage membership."""

from datetime import date
from backend.repository import DashboardRepository, _normalise_fact
from test_reporting import fact


def test_missing_weight_distinguishes_zero_unknown_and_signed_quantity():
    rows = [
        fact(1, weight=None, quantity="0"),
        fact(2, weight=None, quantity=None),
        fact(3, weight=None, quantity="2", cargo="20E"),
        fact(4, weight=None, quantity="-1", cargo="20F"),
        fact(5, weight="0", quantity="0"),
        fact(6, weight="10", quantity="0"),
    ]
    repo = DashboardRepository()
    report = repo._dashboard_from_rows(rows, date(2026, 9, 1), date(2026, 9, 13), "all")
    quality = report["meta"]["data_quality"]
    assert quality["missing_weight_count"] == 4
    assert quality["empty_unweighed_count"] == 1
    assert quality["unweighed_unknown_quantity_count"] == 1
    assert quality["missing_weight_with_quantity_count"] == 2
    assert report["overview"]["record_count"] == 6
    assert report["overview"]["total_tonnage"] == 10
    assert report["overview"]["total_teu"] == 1
    assert report["overview"]["vessel_calls"] == 1
    assert report["overview"]["tonnage_status"] == "partial"
    assert report["meta"]["metric_coverage"]["tonnage"]["missing_weight_rows"] == 4
    assert rows[0]["native_weight"] is None


def test_grouped_query_counters_are_used_without_inference_from_net_total():
    row = fact(1, weight="10", quantity="0")
    row.update(record_count=5, known_weight_count=1, missing_weight_count=4,
               empty_unweighed_count=1, unweighed_unknown_quantity_count=1,
               missing_weight_with_quantity_count=2)
    row.pop("quantity")
    report = DashboardRepository()._dashboard_from_rows(
        [row], date(2026, 9, 1), date(2026, 9, 13), "all")
    quality = report["meta"]["data_quality"]
    assert [quality[field] for field in (
        "empty_unweighed_count", "unweighed_unknown_quantity_count", "missing_weight_with_quantity_count",
    )] == [1, 1, 2]
    assert report["overview"]["total_tonnage"] == 10
    assert report["overview"]["record_count"] == 5


def test_unweighed_native_units_remain_separate_from_missing_tonne_coverage():
    rows = [fact(1, weight=None, quantity="0", unit="M3"), fact(2, weight="0", quantity=None)]
    report = DashboardRepository()._dashboard_from_rows(rows, date(2026, 9, 1), date(2026, 9, 13), "all")
    assert report["meta"]["data_quality"]["empty_unweighed_count"] == 1
    assert report["meta"]["data_quality"]["missing_weight_count"] == 1
    assert report["meta"]["metric_coverage"]["tonnage"]["missing_weight_rows"] == 0
    assert report["meta"]["metric_coverage"]["tonnage"]["excluded_native_rows"] == 1
    assert report["native_units"][0]["value"] is None
    assert report["overview"]["total_tonnage"] == 0


def test_raw_snapshot_backfill_does_not_convert_null_weight_to_zero():
    row = fact(1, weight=None, quantity="0")
    _normalise_fact(row)
    assert row["empty_unweighed_count"] == 1 and row["native_weight"] is None
    assert row["tonnage"] is None
    _normalise_fact(row)
    assert row["empty_unweighed_count"] == 1
    assert row["known_tonne_count"] == 0


def test_all_source_queries_count_each_missing_quantity_state_before_grouping():
    repo = DashboardRepository()
    grouped, _ = repo._fact_query(date(2026, 9, 1), date(2026, 9, 14), "all")
    raw, _ = repo._report_query(date(2026, 9, 1), date(2026, 9, 14), "all")
    voyage, _ = repo._operation_query(date(2026, 9, 1), date(2026, 9, 13), "cua_lo", 1)
    for query in (grouped, raw, voyage):
        for suffix in ("= 0", "IS NULL", "<> 0"):
            assert f"t.weightNetSum IS NULL AND t.quantityTotalSum {suffix}" in query
        for field in ("empty_unweighed_count", "unweighed_unknown_quantity_count", "missing_weight_with_quantity_count"):
            assert field in query
    assert "SUM(CASE WHEN t.weightNetSum IS NULL AND t.quantityTotalSum = 0" in grouped

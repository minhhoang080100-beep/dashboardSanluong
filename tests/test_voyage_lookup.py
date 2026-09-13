"""Plan lookup uses the voyage catalogue, never a selected reporting period."""

from datetime import datetime

import pytest

from backend.repository import DashboardRepository


def recorded_repository(monkeypatch, rows=()):
    repo, calls = DashboardRepository(), []

    def execute(query, params):
        calls.append((query, params))
        return list(rows)

    monkeypatch.setattr(repo, "_execute_query", execute)
    return repo, calls


def test_lookup_includes_calls_without_operations_and_preserves_actual_dates(monkeypatch):
    repo, calls = recorded_repository(monkeypatch, [{
        "terminal": "cua_lo", "voyage_id": 123, "voyage_code": "CALL-123",
        "vessel_name": "PLANNED CALL", "arrival_date": None, "departure_date": None,
    }, {
        "terminal": "cua_lo", "voyage_id": 122, "voyage_code": "CALL-122",
        "vessel_name": "PAST CALL", "arrival_date": datetime(2025, 8, 1),
        "departure_date": datetime(1899, 12, 30),
    }])
    results = repo.search_voyages("cua_lo")
    assert results[0]["voyage_id"] == "123"
    assert results[0]["arrival_date"] is None
    assert results[1]["arrival_date"] == "2025-08-01T00:00:00"
    assert results[1]["departure_date"] is None
    query, params = calls[0]
    assert "TOP (?)" in query and params == (30,)
    assert "TallyShift" not in query and "shiftDate" not in query
    assert "SmartTOS_BenThuy" not in query
    assert "isVirtualVesselVoyage" in query and "isVirtualVessel" in query
    assert "v.rowDeleted" in query and "s.rowDeleted" in query


def test_lookup_escapes_wildcards_and_binds_search(monkeypatch):
    repo, calls = recorded_repository(monkeypatch)
    value = "x' OR 1=1 -- [%_]~"
    assert repo.search_voyages("ben_thuy", value, limit=10) == []
    query, params = calls[0]
    assert value not in query
    assert "SmartTOS_BenThuy.dbo.VesselVoyage" in query
    assert params == (10, 0, "%x' OR 1=1 -- ~[~%~_]~~%", "%x' OR 1=1 -- ~[~%~_]~~%")
    assert query.count("ESCAPE '~'") == 2


@pytest.mark.parametrize("search, identifier", [("123", 123), ("0000123", 123), ("2147483648", 0), ("9" * 100, 0)])
def test_lookup_id_search_is_bounded_sql_int(monkeypatch, search, identifier):
    repo, calls = recorded_repository(monkeypatch)
    repo.search_voyages("cua_lo", search)
    assert calls[0][1][1] == identifier


@pytest.mark.parametrize("kwargs", [
    {"terminal": "all"}, {"terminal": "SmartTOS.dbo"},
    {"terminal": "cua_lo", "search": "x" * 101},
    {"terminal": "cua_lo", "search": None},
    {"terminal": "cua_lo", "limit": True},
    {"terminal": "cua_lo", "limit": 0},
    {"terminal": "cua_lo", "limit": 101},
])
def test_lookup_bad_input_never_queries(monkeypatch, kwargs):
    repo, calls = recorded_repository(monkeypatch)
    with pytest.raises(ValueError):
        repo.search_voyages(**kwargs)
    assert calls == []


def test_validation_is_one_batch_and_keeps_same_id_at_two_terminals(monkeypatch):
    repo, calls = recorded_repository(monkeypatch, [
        {"terminal": "cua_lo", "voyage_id": "7"},
        {"terminal": "ben_thuy", "voyage_id": "7"},
    ])
    result = repo.validate_voyages([("cua_lo", "0007"), ("cua_lo", 7), ("cua_lo", 8), ("ben_thuy", 7)])
    assert result == {("cua_lo", "7"), ("ben_thuy", "7")}
    assert len(calls) == 1
    query, params = calls[0]
    assert params == (7, 8, 7)
    assert query.count("UNION ALL") == 1
    assert query.count("v.rowDeleted") == 2 and query.count("isVirtualVesselVoyage") == 2
    assert "TallyShift" not in query and "shiftDate" not in query


def test_validation_batch_of_500_uses_only_500_sql_parameters(monkeypatch):
    repo, calls = recorded_repository(monkeypatch)
    assert repo.validate_voyages([("cua_lo", index + 1) for index in range(500)]) == set()
    assert len(calls) == 1 and len(calls[0][1]) == 500
    assert calls[0][0].count("?") == 500


def test_empty_batch_has_no_database_dependency(monkeypatch):
    repo, calls = recorded_repository(monkeypatch)
    assert repo.validate_voyages([]) == set()
    assert calls == []


@pytest.mark.parametrize("pairs", [
    [("cua_lo", 1)] * 501, [("all", 1)], [("cua_lo", 0)],
    [("cua_lo", True)], [("cua_lo", 2.5)], [("cua_lo", "1 OR 1=1")],
    [("cua_lo", 2147483648)], [("cua_lo", "9" * 20)], [("cua_lo", "１２３")],
    [("cua_lo",)], ["cua_lo"], [(None, 1)],
])
def test_invalid_pair_fails_before_sql(monkeypatch, pairs):
    repo, calls = recorded_repository(monkeypatch)
    with pytest.raises(ValueError):
        repo.validate_voyages(pairs)
    assert calls == []

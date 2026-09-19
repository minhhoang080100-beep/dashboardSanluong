"""Execute the portable berth-selection SQL against small relational fixtures.

This verifies grouping/ties/deleted history without a live TOS connection. Live
SQL Server validation remains separate because these fixtures use SQLite.
"""
from datetime import date
import sqlite3

import pytest

from backend.berth_scope import BERTH_RULE_VERSION, initial_berth_query
from backend.repository import DashboardRepository, DatabaseQueryError, PRODUCTION_SCOPES


@pytest.fixture
def history_db():
    db = sqlite3.connect(":memory:")
    db.row_factory = sqlite3.Row
    db.executescript("""
        CREATE TABLE actual(doBerthId INTEGER PRIMARY KEY, vesselVoyageId INTEGER,
            berthId INTEGER, ATA TEXT, ATB TEXT, isArrival INTEGER, rowDeleted INTEGER);
        CREATE TABLE berths(berthId INTEGER PRIMARY KEY, berthCode TEXT);
        CREATE TABLE facts(id INTEGER PRIMARY KEY, voyage_id INTEGER, tonnes INTEGER);
    """)
    yield db
    db.close()


def arrival(db, voyage, berth, *, atb=None, ata=None, confirmed=True,
            deleted=None, identifier=None):
    identifier = identifier or db.execute("SELECT COALESCE(MAX(doBerthId),0)+1 FROM actual").fetchone()[0]
    if berth:
        db.execute("INSERT OR IGNORE INTO berths VALUES(?,?)", (berth, f"Berth {berth}"))
    db.execute("INSERT INTO actual VALUES(?,?,?,?,?,?,?)", (identifier, voyage, berth, ata, atb, confirmed, deleted))


def query(terminal="cua_lo", *, selection="all"):
    schema = "SmartTOS.dbo" if terminal == "cua_lo" else "SmartTOS_BenThuy.dbo"
    return initial_berth_query(schema, terminal, selection=selection).replace(f"{schema}.DoBerth", "actual").replace(f"{schema}.Berth", "berths")


def assignments(db, terminal="cua_lo"):
    return {r["vesselVoyageId"]: dict(r) for r in db.execute(query(terminal))}


@pytest.mark.parametrize("first,later,scope", [(13, 12, "vietsun"), (12, 13, "nghe_tinh")])
def test_first_berth_assigns_whole_voyage_despite_later_move(history_db, first, later, scope):
    # Later insertion has the smaller ID. IDs are not chronological evidence.
    arrival(history_db, 1, first, atb="2026-08-31T23:00:00", identifier=20)
    arrival(history_db, 1, later, atb="2026-09-01T12:00:00", identifier=10)
    value = assignments(history_db)[1]
    assert value == {"vesselVoyageId": 1, "initial_berth_id": first,
                     "initial_berth_code": f"Berth {first}", "initial_berth_at": "2026-08-31T23:00:00",
                     "berth_assignment_status": "assigned", "production_scope": scope}


def test_atb_precedes_ata_fallback_without_using_port_arrival_as_berthing(history_db):
    arrival(history_db, 1, 13, ata="2026-09-01T01:00:00", atb="2026-09-01T12:00:00")
    arrival(history_db, 1, 12, ata="2026-09-01T08:00:00")
    value = assignments(history_db)[1]
    assert value["initial_berth_id"] == 12
    assert value["initial_berth_at"] == "2026-09-01T08:00:00"


def test_equal_first_times_at_distinct_berths_are_ambiguous(history_db):
    arrival(history_db, 1, 13, ata="2023-04-01T00:00:00", atb="2023-04-01T00:00:00")
    arrival(history_db, 1, 15, atb="2023-04-01T00:00:00")
    value = assignments(history_db)[1]
    assert value["production_scope"] == "unclassified"
    assert value["berth_assignment_status"] == "ambiguous"
    assert all(value[key] is None for key in ("initial_berth_id", "initial_berth_code", "initial_berth_at"))


def test_repeated_same_berth_records_do_not_multiply_production(history_db):
    arrival(history_db, 1, 12, atb="2026-08-14T15:00:00")
    arrival(history_db, 1, 12, atb="2026-08-14T15:00:00")
    history_db.executemany("INSERT INTO facts VALUES(?,?,?)", [(1, 1, 100), (2, 1, 200)])
    result = history_db.execute(f"SELECT COUNT(*) AS rows, SUM(f.tonnes) AS tonnes FROM facts f LEFT JOIN ({query()}) b ON b.vesselVoyageId=f.voyage_id").fetchone()
    assert (result["rows"], result["tonnes"]) == (2, 300)
    assert assignments(history_db)[1]["berth_assignment_status"] == "assigned"


def test_reporting_period_does_not_cut_the_earlier_berth_history(history_db):
    arrival(history_db, 1, 13, atb="2026-08-01T12:00:00")
    arrival(history_db, 1, 12, atb="2026-09-01T12:00:00")
    arrival(history_db, 2, 12, atb="2026-08-01T12:00:00")
    history_db.executemany("INSERT INTO facts VALUES(?,?,?)", [(1, 1, 100), (2, 1, 200)])
    result = history_db.execute(f"SELECT DISTINCT b.* FROM facts f JOIN ({query()}) b ON b.vesselVoyageId=f.voyage_id").fetchall()
    assert len(result) == 1
    assert result[0]["vesselVoyageId"] == 1
    assert result[0]["initial_berth_at"] == "2026-08-01T12:00:00"
    assert result[0]["production_scope"] == "vietsun"


def test_whole_voyage_optimization_returns_only_requested_identity(history_db):
    arrival(history_db, 1, 13, atb="2026-08-01T12:00:00")
    arrival(history_db, 2, 12, atb="2026-08-01T12:00:00")
    result = history_db.execute(query(selection="voyage"), (1,)).fetchall()
    assert len(result) == 1
    assert result[0]["vesselVoyageId"] == 1
    assert result[0]["production_scope"] == "vietsun"


def test_voyage_predicate_parameters_match_history_and_fact_placeholder_order():
    repo = DashboardRepository()
    start, end = date(2026, 9, 1), date(2026, 9, 16)
    sql, params = repo._fact_query(start, end, "cua_lo", 8868, production_scope="vietsun")
    assert params == (8868, start, end, "vietsun", 8868)
    assert sql.count("?") == len(params)
    sql, params = repo._operation_query(start, end, "cua_lo", 8868, production_scope="vietsun")
    assert params == (8868, start, date(2026, 9, 17), 8868, "vietsun")
    assert sql.count("?") == len(params)


@pytest.mark.parametrize("builder", ["_fact_query", "_report_query", "_operation_query"])
def test_scoped_reads_recompile_the_complete_statement_without_changing_parameters(builder):
    repo = DashboardRepository()
    args = [date(2026, 7, 1), date(2026, 9, 1), "cua_lo"]
    if builder == "_operation_query":
        args.append(8868)
    sql, params = getattr(repo, builder)(*args, production_scope="vietsun")
    assert sql.rstrip().endswith("OPTION (RECOMPILE)")
    assert sql.count("OPTION (RECOMPILE)") == 1
    assert sql.count("?") == len(params)
    assert params.count("vietsun") == (0 if builder == "_report_query" else 1)


def test_planned_undated_move_does_not_override_actual_arrival(history_db):
    arrival(history_db, 1, 13, confirmed=False)
    arrival(history_db, 1, 15, atb="2025-11-29T12:00:00")
    assert assignments(history_db)[1]["initial_berth_id"] == 15


@pytest.mark.parametrize("timestamp", [None, "1899-12-30T00:00:00"])
def test_confirmed_but_undated_actual_arrival_is_not_sorted_last(history_db, timestamp):
    arrival(history_db, 1, 13, atb=timestamp)
    arrival(history_db, 1, 12, atb="2026-09-01T12:00:00")
    value = assignments(history_db)[1]
    assert value["berth_assignment_status"] == "ambiguous"
    assert value["production_scope"] == "unclassified"


def test_deleted_actual_record_cannot_choose_initial_berth(history_db):
    arrival(history_db, 1, 13, atb="2026-08-01T12:00:00", deleted=True)
    arrival(history_db, 1, 12, atb="2026-09-01T12:00:00")
    assert assignments(history_db)[1]["production_scope"] == "nghe_tinh"


@pytest.mark.parametrize("terminal,scope", [("cua_lo", "vietsun"), ("ben_thuy", "nghe_tinh")])
def test_berth_13_identity_is_scoped_to_the_source_database(history_db, terminal, scope):
    arrival(history_db, 1, 13, atb="2026-09-01T12:00:00")
    assert assignments(history_db, terminal)[1]["production_scope"] == scope


def test_absent_or_only_planned_history_is_missing_not_nghe_tinh(history_db):
    arrival(history_db, 1, 13, confirmed=False)
    history_db.executemany("INSERT INTO facts VALUES(?,?,?)", [(1, 1, 100), (2, 2, 200)])
    rows = history_db.execute(f"SELECT f.id, COALESCE(b.production_scope,'unclassified') AS scope, COALESCE(b.berth_assignment_status,'missing') AS status FROM facts f LEFT JOIN ({query()}) b ON b.vesselVoyageId=f.voyage_id ORDER BY f.id").fetchall()
    assert [(r["scope"], r["status"]) for r in rows] == [("unclassified", "missing")] * 2


def test_initial_unknown_berth_does_not_fall_forward_to_known_berth(history_db):
    arrival(history_db, 1, None, atb="2026-09-01T08:00:00")
    arrival(history_db, 1, 12, atb="2026-09-01T12:00:00")
    assert assignments(history_db)[1]["production_scope"] == "unclassified"


@pytest.mark.parametrize("builder", ["_fact_query", "_operation_query"])
@pytest.mark.parametrize("scope", list(PRODUCTION_SCOPES))
def test_all_source_read_paths_filter_parameterized_scope_before_aggregation(builder, scope):
    repo = DashboardRepository()
    args = [date(2026, 9, 1), date(2026, 9, 16), "cua_lo"]
    if builder == "_operation_query":
        args.append(8868)
    sql, params = getattr(repo, builder)(*args, production_scope=scope)
    assert sql.count(repo.production_scope_filter) == 1
    assert params.count(scope) == 1
    assert sql.count("?") == len(params)
    assert "MIN(events.event_at) OVER (PARTITION BY events.vesselVoyageId)" in sql
    assert "currentBerthId" not in sql
    assert "initial_berth_at" in sql


@pytest.mark.parametrize("scope", [None, "", "all", "Cau5", "vietsun' OR 1=1--", [], True])
def test_invalid_scope_is_rejected_before_source_access(monkeypatch, scope):
    repo = DashboardRepository()
    monkeypatch.setattr(repo, "_execute_query", lambda *args: pytest.fail("must not query"))
    for call in (
        lambda: repo.get_dashboard("2026-09-01", "2026-09-15", production_scope=scope),
        lambda: repo.read_report("2026-09-01", "2026-09-15", production_scope=scope),
        lambda: repo.get_voyage_detail("cua_lo", 1, "2026-09-01", "2026-09-15", production_scope=scope),
        lambda: repo.read_voyage_lifetime("cua_lo", 1, production_scope=scope),
    ):
        with pytest.raises(ValueError):
            call()


def test_report_data_is_scoped_but_source_watermark_remains_global(monkeypatch):
    repo = DashboardRepository()
    calls = []
    monkeypatch.setattr(repo, "_execute_query", lambda sql, params: calls.append((sql, params)) or [])
    value = repo.read_report("2026-09-01", "2026-09-15", "all", production_scope="vietsun")
    assert value["report"]["meta"]["filters"]["production_scope"] == "vietsun"
    assert value["report"]["meta"]["berth_rule_version"] == BERTH_RULE_VERSION
    assert value["rows"] == []
    assert calls[0][1] == (date(2026, 8, 17), date(2026, 9, 16), date(2026, 9, 1),
                         date(2026, 8, 17), date(2026, 9, 1)) * 2
    assert calls[0][0].count("?") == len(calls[0][1])
    assert "latest_selected_operation_at" in value["report"]["meta"]["definitions"]["sources"]


@pytest.mark.parametrize("builder", ["_fact_query", "_report_query"])
def test_watermark_does_not_join_berth_history_or_filter_production_scope(builder):
    repo = DashboardRepository()
    sql, params = getattr(repo, builder)(date(2026, 9, 1), date(2026, 9, 16), "cua_lo", production_scope="vietsun")
    parts = sql.split("UNION ALL")
    facts, watermark = parts[:2]
    if builder == "_fact_query":
        assert repo.production_scope_filter in facts
        assert ".DoBerth history" in facts
    else:
        assert repo.production_scope_filter not in facts
        assert ".DoBerth history" not in facts
        assert ".DoBerth history" in parts[2]
    assert ".DoBerth history" not in watermark
    assert repo.production_scope_filter not in watermark
    assert "MAX(t.shiftDate)" in watermark


def test_raw_report_reads_facts_and_assignments_in_one_statement_without_joining_them():
    sql, params = DashboardRepository()._report_query(date(2026, 8, 15), date(2026, 9, 18), "all")
    assert params == (date(2026, 8, 15), date(2026, 9, 18)) * 2
    assert sql.count("'berth' AS kind") == 2
    assert sql.count(".DoBerth history") == 2
    assert "berth_scope ON berth_scope.vesselVoyageId" not in sql
    assert sql.count("CAST(t.vesselVoyageId AS nvarchar(128)) AS source_voyage_id") == 2


def berth_row(terminal, voyage_id, scope="vietsun", status="assigned"):
    return {"kind": "berth", "terminal_id": terminal, "source_voyage_id": voyage_id,
            "production_scope": scope, "berth_assignment_status": status,
            "initial_berth_id": 13 if status == "assigned" else None,
            "initial_berth_code": "Berth 13" if status == "assigned" else None,
            "initial_berth_at": "2026-08-01T12:00:00" if status == "assigned" else None}


def test_raw_attribution_preserves_identity_weight_and_nullable_physical_voyage():
    facts = [
        {"kind": "fact", "id": "a", "terminal_id": "cua_lo", "source_voyage_id": "1", "vessel_id": "1", "native_weight": 100},
        {"kind": "fact", "id": "b", "terminal_id": "ben_thuy", "source_voyage_id": "1", "vessel_id": "1", "native_weight": -5},
        {"kind": "fact", "id": "c", "terminal_id": "cua_lo", "source_voyage_id": "1", "vessel_id": None, "native_weight": None},
    ]
    watermark = {"kind": "source", "terminal_id": "cua_lo", "latest_operation_at": "2026-09-17T12:00:00"}
    fetched = [*facts, watermark, berth_row("cua_lo", "1"), berth_row("ben_thuy", "1", "nghe_tinh")]
    selected = DashboardRepository._scope_report_rows(fetched, "vietsun")
    assert [r["id"] for r in selected if r["kind"] == "fact"] == ["a", "c"]
    assert [r["native_weight"] for r in selected if r["kind"] == "fact"] == [100, None]
    assert selected[-1] == watermark
    assert "production_scope" not in facts[0]  # input is not mutated
    remaining = DashboardRepository._scope_report_rows(fetched, "nghe_tinh")
    assert [(r["id"], r["native_weight"]) for r in remaining if r["kind"] == "fact"] == [("b", -5)]


def test_missing_null_and_ambiguous_dictionary_are_unclassified():
    facts = [{"kind": "fact", "id": str(i), "terminal_id": "cua_lo", "source_voyage_id": voyage}
             for i, voyage in enumerate(("missing", None, "ambiguous"))]
    fetched = [*facts, berth_row("cua_lo", None), berth_row("cua_lo", "ambiguous", "unclassified", "ambiguous")]
    assert DashboardRepository._scope_report_rows(fetched, "vietsun") == []
    selected = DashboardRepository._scope_report_rows(fetched, "unclassified")
    assert [r["id"] for r in selected] == ["0", "1", "2"]
    assert [r["berth_assignment_status"] for r in selected] == ["missing", "missing", "ambiguous"]
    assert all(r["initial_berth_id"] is None for r in selected)


def test_duplicate_assignment_identity_fails_before_aggregation():
    with pytest.raises(DatabaseQueryError):
        DashboardRepository._scope_report_rows([berth_row("cua_lo", "1"), berth_row("cua_lo", "1")], "vietsun")


def test_read_report_filters_before_aggregation_and_snapshot_rows(monkeypatch):
    repo = DashboardRepository()
    fetched = [{"kind": "fact", "id": "selected", "terminal_id": "cua_lo", "source_voyage_id": "1", "operation_day": date(2026, 9, 15)},
               {"kind": "fact", "id": "other", "terminal_id": "ben_thuy", "source_voyage_id": "1", "operation_day": date(2026, 9, 15)},
               berth_row("cua_lo", "1"), berth_row("ben_thuy", "1", "nghe_tinh")]
    monkeypatch.setattr(repo, "_execute_query", lambda *_: fetched)
    seen = []
    monkeypatch.setattr(repo, "_dashboard_from_rows", lambda rows, *_args, **_kwargs: seen.extend(rows) or {})
    result = repo.read_report("2026-09-15", "2026-09-15", production_scope="vietsun")
    assert [row["id"] for row in seen] == ["selected"]
    assert result["rows"] == seen
    assert seen[0]["production_scope"] == "vietsun"


def test_sql_identifiers_are_not_supplied_by_request_parameters():
    with pytest.raises(ValueError):
        initial_berth_query("SmartTOS.dbo; DROP TABLE anything", "cua_lo")
    with pytest.raises(ValueError):
        initial_berth_query("SmartTOS.dbo", "ben_thuy")

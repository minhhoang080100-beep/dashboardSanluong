"""Catalogue membership parity and ODBC batches, without a live TOS source."""
from datetime import date
from decimal import Decimal
import re
import sqlite3

import pytest

from backend import repository
from backend.repository import DashboardRepository, DatabaseQueryError


def sqlite_predicate(sql):
    return sql.replace("ISNULL(", "IFNULL(").replace("N'", "'").replace(" + ", " || ")


def test_materialized_catalogue_preserves_exact_membership_and_source_rows():
    repo = DashboardRepository()
    with sqlite3.connect(":memory:") as db:
        db.executescript("""
            CREATE TABLE JobMethod(jobMethodId INTEGER PRIMARY KEY,
                statisticsGroupTypeIdList TEXT, rowDeleted INTEGER);
            CREATE TABLE StatisticsGroupType(statisticsGroupTypeId INTEGER PRIMARY KEY,
                statisticsGroupTypeCode TEXT, rowDeleted INTEGER);
            CREATE TABLE TallyShift(id INTEGER PRIMARY KEY, jobMethodId INTEGER,
                cargoDirectId INTEGER, rowDeleted INTEGER, native_weight TEXT,
                quantity INTEGER);
            CREATE TEMP TABLE eligible(jobMethodId INTEGER PRIMARY KEY);
        """)
        db.executemany("INSERT INTO StatisticsGroupType VALUES(?,?,?)", [
            (3, "SANLUONG-QUACANG", None), (13, "SANLUONG-QUACANG", 0),
            (7, "SANLUONG-QUACANG", 1), (9, "OTHER", 0),
        ])
        db.executemany("INSERT INTO JobMethod VALUES(?,?,?)", [
            (101, "3", 0), (102, "13", 0), (103, "23", 0), (104, "30", 0),
            (105, "1, 3 ,8", 0), (106, None, 0), (107, "", 0),
            (108, "7", 0), (109, "9", 0), (110, "3,13,3", 0),
            # The original source rule does not filter deleted JobMethod rows.
            (111, "3", 1), (112, "3", None), (113, "03", 0),
        ])
        facts = [(identifier, identifier, 1, None, "123.456", 2)
                 for identifier in range(101, 114)]
        facts.extend([
            (201, 101, 2, 0, None, None), (202, 101, 2, 0, "-1.25", -1),
            (203, 101, 3, 0, "99", 1), (204, 101, None, 0, "99", 1),
            (205, 101, 1, 1, "99", 1), (206, None, 1, 0, "99", 1),
            (207, 999, 1, 0, "99", 1),
        ])
        db.executemany("INSERT INTO TallyShift VALUES(?,?,?,?,?,?)", facts)
        membership = sqlite_predicate(repo.job_method_membership_filter.format(schema="main"))
        db.execute(f"INSERT INTO eligible SELECT DISTINCT j.jobMethodId FROM JobMethod j WHERE {membership}")
        assert db.execute("SELECT jobMethodId FROM eligible ORDER BY jobMethodId").fetchall() == [
            (101,), (102,), (105,), (110,), (111,), (112,),
        ]
        base = "SELECT t.id,t.native_weight,t.quantity FROM TallyShift t JOIN JobMethod j ON t.jobMethodId=j.jobMethodId WHERE "
        original = db.execute(base + sqlite_predicate(repo.throughput_filter.format(schema="main"))
                              + " AND IFNULL(t.rowDeleted,0)=0 ORDER BY t.id").fetchall()
        materialized = db.execute(base + "t.cargoDirectId IN(1,2) AND EXISTS "
            "(SELECT 1 FROM eligible em WHERE em.jobMethodId=j.jobMethodId) "
            "AND IFNULL(t.rowDeleted,0)=0 ORDER BY t.id").fetchall()
        assert materialized == original
        assert [row[0] for row in materialized] == [101, 102, 105, 110, 111, 112, 201, 202]
        assert materialized[-2:] == [(201, None, None), (202, "-1.25", -1)]


@pytest.mark.parametrize("terminal,terminals", [
    ("all", ["cua_lo", "ben_thuy"]), ("cua_lo", ["cua_lo"]), ("ben_thuy", ["ben_thuy"]),
])
def test_report_populates_each_catalogue_once_and_reuses_it_for_raw_and_watermark(terminal, terminals):
    repo = DashboardRepository()
    start, end = date(2025, 4, 16), date(2026, 9, 18)
    sql, params = repo._report_query(start, end, terminal, production_scope="vietsun")
    assert sql.startswith("SET NOCOUNT ON;\n")
    assert params == (start, end) * len(terminals)
    assert sql.count("?") == len(params)
    assert sql.count("OPTION (RECOMPILE)") == 1
    assert sql.rstrip().endswith("OPTION (RECOMPILE)")
    assert sql.count("sg.statisticsGroupTypeCode = N'SANLUONG-QUACANG'") == len(terminals)
    assert sql.count("ISNULL(sg.rowDeleted, 0) = 0") == len(terminals)
    assert "j.rowDeleted" not in sql
    assert "jobMethodName LIKE" not in sql
    assert "t.weightNetSum AS native_weight" in sql
    assert "t.quantityTotalSum AS quantity" in sql
    assert "SUM(t.weightNetSum)" not in sql
    assert "COMPRESS(" not in sql
    assert "berth_scope ON berth_scope.vesselVoyageId" not in sql
    assert repo.production_scope_filter not in sql
    assert re.findall(r"INSERT INTO (\S+)", sql) == [f"@eligible_{key}(jobMethodId)" for key in terminals]
    for key in terminals:
        schema = repository.TERMINALS[key][0]
        assert sql.count(f"DECLARE @eligible_{key} TABLE(jobMethodId int NOT NULL PRIMARY KEY)") == 1
        assert sql.count(f"SELECT DISTINCT j.jobMethodId FROM {schema}.JobMethod j") == 1
        assert sql.count(f"FROM @eligible_{key} em WHERE em.jobMethodId = j.jobMethodId") == 2
        assert sql.count(f"JOIN {schema}.JobMethod j ON t.jobMethodId = j.jobMethodId") == 2
    assert sql.count("MAX(t.shiftDate)") == len(terminals)
    assert sql.count(".DoBerth history") == len(terminals)


class BatchCursor:
    def __init__(self, no_column_sets, rows=(), *, nextset_error=None, no_result=False):
        self.remaining = no_column_sets
        self.rows = list(rows)
        self.nextset_error = nextset_error
        self.no_result = no_result
        self.closed = False
        self.nextset_calls = 0
        self.executed = None

    @property
    def description(self):
        return None if self.remaining or self.no_result else [("id",), ("native_weight",)]

    def execute(self, query, params):
        self.executed = query, params

    def nextset(self):
        self.nextset_calls += 1
        if self.nextset_error:
            raise self.nextset_error
        if self.remaining:
            self.remaining -= 1
            return True
        return False

    def fetchmany(self, size):
        assert self.description is not None
        values, self.rows = self.rows[:size], self.rows[size:]
        return values

    def close(self):
        self.closed = True


class BatchConnection:
    def __init__(self, cursor):
        self.value = cursor
        self.closed = False

    def cursor(self):
        return self.value

    def close(self):
        self.closed = True


@pytest.mark.parametrize("no_column_sets", [0, 1, 4])
@pytest.mark.parametrize("rows", [[], [("0001", Decimal("12.345678")), ("0002", None)]])
def test_execute_batch_skips_only_no_column_sets_and_preserves_empty_null_decimal(monkeypatch, no_column_sets, rows):
    cursor = BatchCursor(no_column_sets, rows)
    connection = BatchConnection(cursor)
    monkeypatch.setattr(repository, "get_db_connection", lambda: connection)
    actual = DashboardRepository()._execute_query("synthetic batch", (date(2026, 1, 1),))
    assert actual == [{"id": identifier, "native_weight": weight} for identifier, weight in rows]
    assert cursor.nextset_calls == no_column_sets
    assert cursor.executed == ("synthetic batch", (date(2026, 1, 1),))
    assert cursor.closed and connection.closed


@pytest.mark.parametrize("failure", ["no_result", "nextset"])
def test_execute_batch_missing_or_failed_result_is_error_not_empty_report(monkeypatch, caplog, failure):
    cursor = BatchCursor(1, no_result=failure == "no_result",
                         nextset_error=RuntimeError("private-host secret SQL") if failure == "nextset" else None)
    connection = BatchConnection(cursor)
    monkeypatch.setattr(repository, "get_db_connection", lambda: connection)
    with pytest.raises(DatabaseQueryError) as caught:
        DashboardRepository()._execute_query("synthetic batch")
    assert "private-host" not in str(caught.value) + caplog.text
    assert "secret" not in str(caught.value) + caplog.text
    assert cursor.closed and connection.closed


def test_execute_batch_row_limit_still_applies_after_no_column_sets(monkeypatch):
    cursor = BatchCursor(2, [("1", None), ("2", None)])
    connection = BatchConnection(cursor)
    monkeypatch.setattr(repository, "get_db_connection", lambda: connection)
    monkeypatch.setattr(repository, "MAX_QUERY_ROWS", 1)
    with pytest.raises(DatabaseQueryError):
        DashboardRepository()._execute_query("synthetic batch")
    assert cursor.closed and connection.closed

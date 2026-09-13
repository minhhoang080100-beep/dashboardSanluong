"""Failure diagnostics are exercised with fake connections, never source SQL."""

import importlib

from fastapi.testclient import TestClient
import pytest

from backend import database, repository

main = importlib.import_module("backend.main")
PRIVATE_TEXT = "SERVER=private-host; UID=private-user; PWD=private-password; private_table"


class DiagnosticCursor:
    description = [("identifier",)]

    def __init__(self, phase=None, error=None, rows=None, close_error=False):
        self.phase = phase
        self.error = error
        self.rows = rows if rows is not None else [("value",)]
        self.closed = False
        self.close_error = close_error
        self.execute_calls = 0

    def execute(self, query, params=()):
        self.execute_calls += 1
        if self.phase == "execute":
            raise self.error

    def fetchmany(self, size):
        if self.phase == "fetch":
            raise self.error
        rows, self.rows = self.rows, []
        return rows

    def fetchone(self):
        if self.phase == "fetch":
            raise self.error
        return ("value",)

    def close(self):
        self.closed = True
        if self.close_error:
            raise RuntimeError(PRIVATE_TEXT)


class DiagnosticConnection:
    def __init__(self, phase=None, error=None, rows=None, close_error=False):
        self.phase = phase
        self.error = error
        self.fake_cursor = DiagnosticCursor(phase, error, rows, close_error)
        self.closed = False

    def cursor(self):
        if self.phase == "cursor":
            raise self.error
        return self.fake_cursor

    def close(self):
        self.closed = True


def assert_private_details_absent(caplog, exception=None):
    for value in ("private-host", "private-user", "private-password", "private_table", "private-parameter"):
        assert value not in caplog.text
        if exception is not None:
            assert value not in str(exception)
    assert all(record.exc_info is None for record in caplog.records)


@pytest.mark.parametrize("phase,state,message,category", [
    ("cursor", "HYT01", "Connection timeout", "timeout"),
    ("execute", "HYT00", "Query timeout expired", "timeout"),
    ("fetch", "08S01", "Communication link failure", "network"),
    ("execute", "42000", "The SELECT permission was denied", "permission"),
    ("execute", "42S02", "Invalid object name", "schema"),
    ("execute", "42S22", "Invalid column name", "schema"),
    ("execute", "40001", "Transaction was deadlocked", "deadlock"),
    ("execute", "40001", "Transaction rolled back", "transaction_conflict"),
    ("execute", "42000", "Unclassified SQL statement failure", "query"),
    ("fetch", "HY008", "Operation canceled", "cancelled"),
    ("execute", "TOKEN", "Unclassified driver failure", "unknown"),
])
def test_query_failures_are_classified_redacted_closed_and_not_retried(
        monkeypatch, caplog, phase, state, message, category):
    error = database.pyodbc.Error(state, message + "; " + PRIVATE_TEXT)
    connection = DiagnosticConnection(phase, error)
    connections = []

    def connect():
        connections.append(connection)
        return connection

    monkeypatch.setattr(repository, "get_db_connection", connect)
    monkeypatch.setattr(repository, "perf_counter", lambda: 10.0)
    monkeypatch.setattr(database, "perf_counter", lambda: 10.125)

    with pytest.raises(database.DatabaseQueryError) as caught:
        repository.DashboardRepository()._execute_query("SELECT private_table", ("private-parameter",))

    assert len(connections) == 1
    assert connection.closed
    assert connection.fake_cursor.closed is (phase != "cursor")
    assert connection.fake_cursor.execute_calls == (0 if phase == "cursor" else 1)
    assert f"operation=query phase={phase} category={category}" in caplog.text
    assert f"sqlstate={'unknown' if state == 'TOKEN' else state}" in caplog.text
    assert "elapsed_ms=125" in caplog.text
    assert message not in caplog.text
    assert "TOKEN" not in caplog.text
    assert_private_details_absent(caplog, caught.value)


def test_row_limit_is_distinct_from_driver_error_and_closes_resources(monkeypatch, caplog):
    connection = DiagnosticConnection(rows=[("one",), ("two",), ("three",)])
    monkeypatch.setattr(repository, "get_db_connection", lambda: connection)
    monkeypatch.setattr(repository, "MAX_QUERY_ROWS", 2)

    with pytest.raises(database.DatabaseQueryError) as caught:
        repository.DashboardRepository()._execute_query("SELECT private_table")

    assert "operation=query phase=row_limit category=row_limit sqlstate=unknown" in caplog.text
    assert connection.fake_cursor.execute_calls == 1
    assert connection.closed and connection.fake_cursor.closed
    assert_private_details_absent(caplog, caught.value)


@pytest.mark.parametrize("phase", ["cursor", "execute", "fetch"])
def test_health_failures_have_safe_phase_and_preserve_503(monkeypatch, caplog, phase):
    error = database.pyodbc.Error("08S01", "Communication link failure; " + PRIVATE_TEXT)
    connection = DiagnosticConnection(phase, error)
    requested = []

    def connect(name):
        requested.append(name)
        return connection

    monkeypatch.setattr(main, "get_db_connection", connect)
    monkeypatch.setattr(main, "perf_counter", lambda: 10.0)
    monkeypatch.setattr(database, "perf_counter", lambda: 10.125)
    with TestClient(main.app) as client:
        response = client.get("/api/health")

    assert requested == ["SmartTOS"]
    assert response.status_code == 503
    assert response.json()["detail"]["code"] == "DATABASE_UNAVAILABLE"
    assert response.headers["cache-control"] == "no-store"
    assert f"operation=health phase={phase} category=network sqlstate=08S01 elapsed_ms=125" in caplog.text
    assert connection.closed
    assert connection.fake_cursor.closed is (phase != "cursor")
    assert_private_details_absent(caplog)
    assert "private-" not in response.text


def test_health_second_source_failure_preserves_primary_error_when_cleanup_fails(monkeypatch, caplog):
    first = DiagnosticConnection()
    second = DiagnosticConnection("fetch", database.pyodbc.Error("HYT00", "Query timeout; " + PRIVATE_TEXT), close_error=True)
    requested = []

    def connect(name):
        requested.append(name)
        return first if name == "SmartTOS" else second

    monkeypatch.setattr(main, "get_db_connection", connect)
    with TestClient(main.app) as client:
        response = client.get("/api/health")

    assert response.status_code == 503
    assert requested == ["SmartTOS", "SmartTOS_BenThuy"]
    assert "operation=health phase=fetch category=timeout sqlstate=HYT00" in caplog.text
    assert "Database health cursor close failed." in caplog.text
    assert first.closed and second.closed
    assert first.fake_cursor.closed and second.fake_cursor.closed
    assert_private_details_absent(caplog)


def test_connection_failure_adds_elapsed_time_without_changing_tls_or_retry(monkeypatch, caplog):
    for key, value in {"DB_SERVER": "private-host", "DB_USERNAME": "private-user",
                       "DB_PASSWORD": "private-password", "DB_ENCRYPT": True,
                       "DB_TRUST_SERVER_CERTIFICATE": False}.items():
        monkeypatch.setattr(database.settings, key, value)
    clock = iter([20.0, 20.4])
    monkeypatch.setattr(database, "perf_counter", lambda: next(clock))
    calls = []

    def fail(connection_string, **kwargs):
        calls.append((connection_string, kwargs))
        raise database.pyodbc.Error("08001", "Login timeout expired; " + PRIVATE_TEXT)

    monkeypatch.setattr(database.pyodbc, "connect", fail)
    with pytest.raises(database.DatabaseUnavailable) as caught:
        database.get_db_connection()

    assert len(calls) == 1
    assert ";Encrypt=yes;TrustServerCertificate=no;" in calls[0][0]
    assert calls[0][1]["timeout"] == database.settings.DB_CONNECT_TIMEOUT_SECONDS
    assert "category=timeout sqlstate=08001 encrypt=True trust_server_certificate=False elapsed_ms=400" in caplog.text
    assert_private_details_absent(caplog, caught.value)


def test_untrusted_diagnostic_labels_are_not_logged(caplog):
    database.log_database_failure(RuntimeError(PRIVATE_TEXT), phase="private-phase",
                                  operation="private-operation", started_at=database.perf_counter())
    assert "operation=unknown phase=unknown category=unknown sqlstate=unknown" in caplog.text
    assert "private-phase" not in caplog.text
    assert "private-operation" not in caplog.text
    assert_private_details_absent(caplog)

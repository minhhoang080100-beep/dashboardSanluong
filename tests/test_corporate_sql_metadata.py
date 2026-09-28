"""Zero-row metadata probes with fake ODBC connections; never a real database."""
from datetime import date, datetime, time
from decimal import Decimal
import logging

import pytest

from backend.corporate_api import sql
from backend.corporate_api.errors import CorporateError


DRIVER_DETAIL = 'SERVER=secret-host;UID=secret-user;PWD=secret-password'


class Cursor:
    def __init__(self, description):
        self.description = description
        self.statements = []
        self.closed = False
        self.execute_error = None
        self.close_error = None

    def execute(self, statement, *params):
        self.statements.append((statement, params))
        if self.execute_error:
            raise self.execute_error
        return self

    def fetchone(self, *args):
        raise AssertionError('A schema probe must not fetch a data row')

    fetchmany = fetchall = fetchone

    def __iter__(self):
        raise AssertionError('A schema probe must not iterate data rows')

    def close(self):
        self.closed = True
        if self.close_error:
            raise self.close_error


class Connection:
    def __init__(self, cursor):
        self.test_cursor = cursor
        self.closed = False
        self.cursor_error = None
        self.close_error = None

    def cursor(self):
        if self.cursor_error:
            raise self.cursor_error
        return self.test_cursor

    def close(self):
        self.closed = True
        if self.close_error:
            raise self.close_error


@pytest.fixture
def source(monkeypatch):
    cursor = Cursor([('id', int, None, None, None, None, False)])
    connection = Connection(cursor)
    calls = []

    def connect(database):
        calls.append(database)
        return connection

    monkeypatch.setattr(sql, 'get_db_connection', connect)
    return cursor, connection, calls


def test_probe_uses_one_zero_row_statement_description_only_and_closes(source):
    cursor, connection, calls = source
    assert sql.query_source.describe_table is sql.describe_table
    result = sql.describe_table('SmartTOS', 'Equipment')
    assert result == [{'column_name': 'id', 'data_type': 'int'}]
    assert cursor.statements == [('SELECT TOP (0) * FROM [dbo].[Equipment]', ())]
    assert calls == ['SmartTOS']
    assert cursor.closed and connection.closed


@pytest.mark.parametrize('native_type,category', [
    (int, 'int'), (str, 'nvarchar'), (bool, 'bit'), (Decimal, 'decimal'), (float, 'float'),
    (datetime, 'datetime2'), (date, 'date'), (time, 'time'), (bytes, 'varbinary'), (bytearray, 'varbinary'),
])
def test_only_established_python_type_categories_are_mapped(source, native_type, category):
    cursor, connection, _ = source
    cursor.description = [('sourceValue', native_type, None, None, None, None, True)]
    assert sql.describe_table('SmartTOS_BenThuy', 'JobResource') == [
        {'column_name': 'sourceValue', 'data_type': category}]
    assert cursor.closed and connection.closed


@pytest.mark.parametrize('database,table', [
    ('master', 'Equipment'), ('smarttos', 'Equipment'),
    ('SmartTOS;DROP TABLE Equipment', 'Equipment'),
    ('SmartTOS', 'dbo.Equipment'), ('SmartTOS', '[Equipment]'),
    ('SmartTOS', 'Equipment];SELECT secret'), ('SmartTOS', 'Equipment --'),
    ('SmartTOS', 'Equipment\n'), ('SmartTOS', ''), ('SmartTOS', None),
    ('SmartTOS', 'X' * 129), ('SmartTOS', '1Equipment'),
    (None, 'Equipment'), ([], 'Equipment'), ({}, 'Equipment'), ('SmartTOS', []),
])
def test_unsafe_identifiers_are_rejected_before_connection(source, database, table):
    cursor, _, calls = source
    with pytest.raises(ValueError):
        sql.describe_table(database, table)
    assert calls == [] and cursor.statements == []


@pytest.mark.parametrize('description', [
    None, [], [('value', object)], [('value', complex)], [('value', 'nvarchar')],
    [('value', None)], [('value', [])], [('value',)], [None],
    [('', int)], [(None, int)], [('same', int), ('same', int)],
])
def test_unknown_or_malformed_description_fails_closed_and_closes(source, description):
    cursor, connection, _ = source
    cursor.description = description
    with pytest.raises(CorporateError) as raised:
        sql.describe_table('SmartTOS', 'Equipment')
    assert raised.value.status == 503 and raised.value.code == 'SOURCE_SCHEMA'
    assert cursor.closed and connection.closed


def test_query_failure_and_close_failures_never_expose_driver_details(source, caplog):
    cursor, connection, _ = source
    cursor.execute_error = RuntimeError(DRIVER_DETAIL)
    cursor.close_error = RuntimeError(DRIVER_DETAIL)
    connection.close_error = RuntimeError(DRIVER_DETAIL)
    with caplog.at_level(logging.WARNING):
        with pytest.raises(CorporateError) as raised:
            sql.describe_table('SmartTOS', 'Equipment')
    assert raised.value.status == 503 and raised.value.code == 'SOURCE_UNAVAILABLE'
    assert DRIVER_DETAIL not in str(raised.value)
    assert 'secret-host' not in caplog.text and 'secret-password' not in caplog.text
    assert cursor.closed and connection.closed


def test_connection_is_closed_when_cursor_creation_fails(source, caplog):
    cursor, connection, _ = source
    connection.cursor_error = RuntimeError(DRIVER_DETAIL)
    with pytest.raises(CorporateError) as raised:
        sql.describe_table('SmartTOS', 'Equipment')
    assert raised.value.code == 'SOURCE_UNAVAILABLE'
    assert connection.closed and not cursor.closed
    assert 'secret-password' not in caplog.text


def test_connection_failure_is_sanitized(monkeypatch, caplog):
    def unavailable(database):
        raise RuntimeError(DRIVER_DETAIL)

    monkeypatch.setattr(sql, 'get_db_connection', unavailable)
    with pytest.raises(CorporateError) as raised:
        sql.describe_table('SmartTOS', 'Equipment')
    assert raised.value.code == 'SOURCE_UNAVAILABLE'
    assert 'secret-password' not in str(raised.value) + caplog.text


def test_cleanup_failure_does_not_overwrite_valid_metadata_and_closes_both(source, caplog):
    cursor, connection, _ = source
    cursor.close_error = RuntimeError(DRIVER_DETAIL)
    connection.close_error = RuntimeError(DRIVER_DETAIL)
    assert sql.describe_table('SmartTOS', 'Equipment') == [{'column_name': 'id', 'data_type': 'int'}]
    assert cursor.closed and connection.closed
    assert 'secret-password' not in caplog.text


@pytest.mark.parametrize('phase', ['execute', 'fetch'])
def test_row_query_failure_keeps_safe_error_even_when_cleanup_fails(source, caplog, phase):
    cursor, connection, _ = source
    if phase == 'execute':
        cursor.execute_error = RuntimeError(DRIVER_DETAIL)
    else:
        def fail_fetch(*args):
            raise RuntimeError(DRIVER_DETAIL)
        cursor.fetchmany = fail_fetch
    cursor.close_error = RuntimeError(DRIVER_DETAIL)
    connection.close_error = RuntimeError(DRIVER_DETAIL)
    with pytest.raises(CorporateError) as raised:
        sql.query_source('SmartTOS', 'SELECT equipmentId FROM dbo.Equipment')
    assert raised.value.status == 503 and raised.value.code == 'SOURCE_UNAVAILABLE'
    assert 'secret-password' not in str(raised.value) + caplog.text
    assert 'operation=query' in caplog.text and f'phase={phase}' in caplog.text
    assert cursor.closed and connection.closed


def test_successful_row_query_returns_rows_despite_safe_cleanup_failures(source, caplog):
    cursor, connection, _ = source
    cursor.fetchmany = lambda limit: [(1,), (2,)]
    cursor.close_error = RuntimeError(DRIVER_DETAIL)
    connection.close_error = RuntimeError(DRIVER_DETAIL)
    result = sql.query_source('SmartTOS', 'SELECT equipmentId FROM dbo.Equipment')
    assert result == [{'id': 1}, {'id': 2}]
    assert cursor.closed and connection.closed
    assert 'secret-password' not in caplog.text

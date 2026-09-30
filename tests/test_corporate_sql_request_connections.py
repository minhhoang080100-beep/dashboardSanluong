"""Connection reuse is request-local; every query still reads fresh source rows."""
import asyncio
from concurrent.futures import ThreadPoolExecutor
from contextvars import copy_context
from threading import Barrier, get_ident

import pytest

from backend.corporate_api import sql
from backend.corporate_api.errors import CorporateError


PRIVATE_DRIVER_TEXT = 'SERVER=private-host;PWD=private-password'


class Cursor:
    def __init__(self, connection, failure):
        self.connection = connection
        self.failure = failure
        self.description = [('value', int)]
        self.closed = False
        self.fetched = False
        self.value = None

    def execute(self, statement, params=()):
        self.connection.executions.append((statement, params, self.connection.timeout))
        if self.failure == 'execute':
            raise RuntimeError(PRIVATE_DRIVER_TEXT)
        self.value = self.connection.factory.value

    def fetchmany(self, limit):
        if self.failure == 'fetch':
            raise RuntimeError(PRIVATE_DRIVER_TEXT)
        if self.fetched:
            return []
        self.fetched = True
        return [(self.value,)]

    def close(self):
        self.closed = True
        if self.failure == 'close':
            raise RuntimeError(PRIVATE_DRIVER_TEXT)


class Connection:
    def __init__(self, factory, database):
        self.factory = factory
        self.database = database
        self.owner = get_ident()
        self.timeout = factory.timeout
        self.closed = False
        self.close_count = 0
        self.cursors = []
        self.executions = []
        self.close_error = False

    def cursor(self):
        assert not self.closed
        assert self.owner == get_ident(), 'Connections must never cross worker threads'
        failure, self.factory.failure = self.factory.failure, None
        if failure == 'cursor':
            raise RuntimeError(PRIVATE_DRIVER_TEXT)
        cursor = Cursor(self, failure)
        self.cursors.append(cursor)
        return cursor

    def close(self):
        self.closed = True
        self.close_count += 1
        if self.close_error:
            raise RuntimeError(PRIVATE_DRIVER_TEXT)


class Factory:
    def __init__(self):
        self.connections = []
        self.calls = []
        self.value = 1
        self.timeout = 20
        self.failure = None

    def __call__(self, database, **kwargs):
        connection = Connection(self, database)
        self.connections.append(connection)
        self.calls.append((database, kwargs))
        return connection


@pytest.fixture
def source(monkeypatch):
    factory = Factory()
    monkeypatch.setattr(sql, 'get_db_connection', factory)
    return factory


def read(database='SmartTOS'):
    return sql.query_source(database, 'SELECT value FROM dbo.CargoOrigin')


def test_budget_reuses_only_connections_and_fetches_fresh_rows_each_time(source):
    with sql.source_read_budget(35):
        assert read() == [{'value': 1}]
        source.value = 2
        assert read() == [{'value': 2}]
        assert len(source.connections) == 1
        connection = source.connections[0]
        assert not connection.closed
        assert len(connection.executions) == 2
        assert len(connection.cursors) == 2 and all(cursor.closed for cursor in connection.cursors)
    assert connection.closed and connection.close_count == 1


def test_metadata_and_rows_share_one_connection_for_each_database(source):
    with sql.source_read_budget():
        for database in ('SmartTOS', 'SmartTOS_BenThuy'):
            assert sql.describe_table(database, 'CargoOrigin') == [{'column_name': 'value', 'data_type': 'int'}]
            assert read(database) == [{'value': 1}]
        assert len(source.connections) == 2
        assert all(len(connection.executions) == 2 for connection in source.connections)
        assert all(not connection.closed for connection in source.connections)
    assert all(connection.closed for connection in source.connections)


def test_outside_budget_preserves_one_connection_per_call(source):
    read()
    read()
    assert len(source.connections) == 2 and all(connection.closed for connection in source.connections)
    assert all(kwargs == {} for _, kwargs in source.calls)


def test_separate_requests_do_not_retain_connections(source):
    for _ in range(2):
        with sql.source_read_budget():
            read()
    assert len(source.connections) == 2 and all(connection.closed for connection in source.connections)
    assert sql._connections.get() is None and sql._remaining() is None


def test_nested_budget_reuses_connection_tightens_then_restores_outer_timeout(source, monkeypatch):
    source.timeout = 90
    now = [100.0]
    monkeypatch.setattr(sql, 'perf_counter', lambda: now[0])
    with sql.source_read_budget(35):
        read()
        connection = source.connections[0]
        assert connection.timeout == 35
        with sql.source_read_budget(5):
            read()
            assert connection.timeout == 5
        now[0] = 102.0
        with sql.source_read_budget(1000):
            read()
            assert connection.timeout == 33
        assert not connection.closed and len(source.connections) == 1
    assert connection.closed


@pytest.mark.parametrize('phase', ['cursor', 'execute', 'fetch'])
def test_failed_query_discards_unhealthy_connection_and_next_query_reconnects(source, caplog, phase):
    with sql.source_read_budget():
        read()
        first = source.connections[0]
        source.failure = phase
        with pytest.raises(CorporateError) as caught:
            read()
        assert caught.value.code == 'SOURCE_UNAVAILABLE'
        assert first.closed and first.close_count == 1
        assert read() == [{'value': 1}]
        assert len(source.connections) == 2 and not source.connections[1].closed
    assert all(connection.closed for connection in source.connections)
    assert 'private-password' not in caplog.text + str(caught.value)


def test_cursor_close_failure_discards_connection_without_changing_success(source, caplog):
    with sql.source_read_budget():
        source.failure = 'close'
        assert read() == [{'value': 1}]
        assert source.connections[0].closed
        read()
        assert len(source.connections) == 2
    assert all(connection.closed for connection in source.connections)
    assert 'private-password' not in caplog.text


def test_outer_exception_closes_every_connection_and_resets_context(source):
    with pytest.raises(RuntimeError, match='application failure'):
        with sql.source_read_budget():
            read()
            read('SmartTOS_BenThuy')
            raise RuntimeError('application failure')
    assert all(connection.closed for connection in source.connections)
    assert sql._connections.get() is None and sql._remaining() is None


def test_connection_cleanup_failure_does_not_skip_other_database(source, caplog):
    with sql.source_read_budget():
        read()
        read('SmartTOS_BenThuy')
        source.connections[0].close_error = True
    assert all(connection.closed for connection in source.connections)
    assert 'private-password' not in caplog.text
    assert sql._connections.get() is None


def test_concurrent_request_threads_do_not_share_connection(source):
    barrier = Barrier(2)
    def request():
        with sql.source_read_budget():
            read()
            barrier.wait(timeout=5)
            read()
        return get_ident()
    with ThreadPoolExecutor(max_workers=2) as executor:
        requests = [executor.submit(request) for _ in range(2)]
        owners = {request.result(timeout=10) for request in requests}
    assert len(owners) == 2 and len(source.connections) == 2
    assert {connection.owner for connection in source.connections} == owners
    assert all(connection.closed and len(connection.executions) == 2 for connection in source.connections)


@pytest.mark.parametrize('enter_child_budget', [False, True])
def test_copied_context_in_worker_never_reuses_or_closes_parent_connection(source, enter_child_budget):
    def worker():
        if enter_child_budget:
            with sql.source_read_budget():
                read()
                read()
        else:
            read()
    with sql.source_read_budget():
        read()
        parent = source.connections[0]
        context = copy_context()
        with ThreadPoolExecutor(max_workers=1) as executor:
            executor.submit(context.run, worker).result(timeout=10)
        assert not parent.closed
        read()
        assert len(source.connections) == 2
        assert source.connections[1].closed
    assert parent.closed


def test_child_async_task_does_not_borrow_parent_scope(source):
    async def child():
        with sql.source_read_budget():
            read()
            read()
    async def request():
        with sql.source_read_budget():
            read()
            parent = source.connections[0]
            await asyncio.create_task(child())
            assert not parent.closed
            read()
    asyncio.run(request())
    assert len(source.connections) == 2
    assert all(connection.closed and len(connection.executions) == 2 for connection in source.connections)


def test_expired_budget_does_not_execute_again_and_always_closes(source, monkeypatch):
    now = [100.0]
    monkeypatch.setattr(sql, 'perf_counter', lambda: now[0])
    with sql.source_read_budget(5):
        read()
        now[0] = 106.0
        with pytest.raises(CorporateError) as caught:
            read()
        assert caught.value.code == 'SOURCE_TIMEOUT'
        assert len(source.connections[0].executions) == 1
    assert source.connections[0].closed

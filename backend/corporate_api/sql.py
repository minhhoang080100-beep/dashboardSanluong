"""Bounded source reads shared by extraction and on-demand API requests."""
from contextlib import contextmanager
from contextvars import ContextVar
import asyncio
import logging
import re
from threading import get_ident
from datetime import date, datetime, time
from decimal import Decimal
from time import perf_counter

try:
    from ..database import get_db_connection, log_database_failure, DatabaseUnavailable
except ImportError:
    from database import get_db_connection, log_database_failure, DatabaseUnavailable
from .errors import CorporateError

logger = logging.getLogger(__name__)
DATABASES = frozenset({'SmartTOS', 'SmartTOS_BenThuy'})
_deadline = ContextVar('corporate_source_deadline', default=None)
_connections = ContextVar('corporate_source_connections', default=None)
_DESCRIBED_TYPES = {
    int: 'int', str: 'nvarchar', bool: 'bit', Decimal: 'decimal', float: 'float',
    datetime: 'datetime2', date: 'date', time: 'time', bytes: 'varbinary', bytearray: 'varbinary',
}


def _context_owner():
    try:
        task = asyncio.current_task()
    except RuntimeError:
        task = None
    return get_ident(), task


class _ConnectionScope:
    def __init__(self):
        self.owner = _context_owner()
        self.connections = {}
        self.active = True


def _owned_connections():
    scope = _connections.get()
    # ContextVars may be copied into worker threads or child async tasks.
    # Neither is allowed to reuse a parent's ODBC connection.
    if scope is not None and scope.active and scope.owner == _context_owner():
        return scope
    return None


@contextmanager
def source_read_budget(seconds=35):
    """Own a deadline and at most one connection per database for this request.

    Only connections are reused: every SELECT executes and fetches fresh rows.
    Nested budgets share connections while tightening the outer deadline.
    Nothing is retained after the outer budget exits, including on failures.
    """
    previous = _deadline.get()
    end = perf_counter() + seconds
    token = _deadline.set(min(previous, end) if previous is not None else end)
    scope = _owned_connections()
    connection_token = None
    if scope is None:
        scope = _ConnectionScope()
        connection_token = _connections.set(scope)
    try:
        yield
    finally:
        _deadline.reset(token)
        if connection_token is not None:
            scope.active = False
            connections = list(scope.connections.values())
            scope.connections.clear()
            _connections.reset(connection_token)
            for connection, _ in connections:
                _close_source_resource(connection)


def _remaining():
    deadline = _deadline.get()
    if deadline is None:
        return None
    remaining = deadline - perf_counter()
    if remaining < 1:
        raise CorporateError(503, 'SOURCE_TIMEOUT', 'Truy vấn SmartTOS quá thời gian. Hãy chọn kỳ ngắn hơn hoặc thử lại.', retry_after=5)
    return max(1, int(remaining))


def _connect(database):
    remaining = _remaining()
    scope = _owned_connections()
    if scope is not None and database in scope.connections:
        connection, original_timeout = scope.connections[database]
        try:
            if remaining is not None:
                connection.timeout = min(original_timeout or remaining, remaining)
            return connection
        except BaseException:
            scope.connections.pop(database, None)
            _close_source_resource(connection)
            raise
    connection = (get_db_connection(database) if remaining is None else
                  get_db_connection(database, connect_timeout_seconds=remaining))
    try:
        remaining = _remaining()
        original_timeout = connection.timeout if remaining is not None else None
        if remaining is not None:
            connection.timeout = min(original_timeout or remaining, remaining)
        if scope is not None:
            scope.connections[database] = (connection, original_timeout)
        return connection
    except BaseException:
        _close_source_resource(connection)
        raise


def _close_source_resource(resource):
    if resource is None:
        return True
    try:
        resource.close()
        return True
    except Exception:
        # Closing a failed ODBC cursor can itself include connection details.
        # Always attempt the connection close as well; never replace the result
        # or the original safe error with raw driver text.
        logger.warning('Source cleanup failed.')
        return False


def _release_connection(database, connection, *, reusable):
    if connection is None:
        return
    scope = _owned_connections()
    held = scope.connections.get(database) if scope is not None else None
    if held is not None and held[0] is connection:
        if reusable:
            return
        scope.connections.pop(database, None)
    _close_source_resource(connection)


def describe_table(database, table):
    """Read only pyodbc column metadata from a zero-row SELECT.

    Unlike broad system-catalog queries, this touches one known source table.
    The returned SQL type names represent safe Python-type categories, not a
    claim about a column's exact SQL width, precision or nullability. Unknown
    driver types fail closed instead of being guessed as strings or numbers.
    """
    if (not isinstance(database, str) or database not in DATABASES or not isinstance(table, str)
            or re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]{0,127}', table) is None):
        raise ValueError('A supported database and a single safe table identifier are required.')
    connection = cursor = None
    reusable = False
    started = perf_counter()
    try:
        connection = _connect(database)
        cursor = connection.cursor()
        cursor.execute(f'SELECT TOP (0) * FROM [dbo].[{table}]')
        _remaining()
        description = cursor.description
        if not description:
            raise CorporateError(503, 'SOURCE_SCHEMA', 'Chưa xác minh được cấu trúc bảng nguồn.')
        result, seen = [], set()
        for field in description:
            if not isinstance(field, (tuple, list)) or len(field) < 2:
                raise CorporateError(503, 'SOURCE_SCHEMA', 'Chưa xác minh được cấu trúc bảng nguồn.')
            name, native_type = field[:2]
            if (not isinstance(name, str) or not name or name in seen
                    or not isinstance(native_type, type) or native_type not in _DESCRIBED_TYPES):
                raise CorporateError(503, 'SOURCE_SCHEMA', 'Kiểu cột nguồn chưa được hỗ trợ để đối chiếu.')
            result.append({'column_name': name, 'data_type': _DESCRIBED_TYPES[native_type]})
            seen.add(name)
        reusable = True
        return result
    except CorporateError:
        raise
    except Exception as exc:
        if not isinstance(exc, DatabaseUnavailable):
            log_database_failure(exc, phase='execute', started_at=started, operation='query', log=logger)
        raise CorporateError(503, 'SOURCE_UNAVAILABLE', 'Không đọc được cấu trúc nguồn SQL. Chưa công bố dữ liệu mới.') from None
    finally:
        cursor_closed = _close_source_resource(cursor)
        _release_connection(database, connection, reusable=reusable and cursor_closed)


def query_source(database, statement, params=()):
    if database not in DATABASES or not re.match(r'^\s*(SELECT|WITH)\b', statement, re.IGNORECASE):
        raise ValueError('Only fixed source SELECT statements are allowed.')
    connection = cursor = None
    reusable = False
    started = perf_counter()
    phase = 'cursor'
    try:
        connection = _connect(database)
        cursor = connection.cursor()
        phase = 'execute'
        cursor.execute(statement, params)
        _remaining()
        phase = 'fetch'
        names = [field[0] for field in cursor.description]
        if _deadline.get() is None:
            rows = cursor.fetchmany(250_001)
        else:
            rows = []
            while len(rows) <= 250_000:
                _remaining()
                batch = cursor.fetchmany(min(1000, 250_001 - len(rows)))
                _remaining()
                rows.extend(batch)
                if len(batch) < 1000:
                    break
        if len(rows) > 250_000:
            raise CorporateError(422, 'SOURCE_ROW_LIMIT', 'Kỳ lấy dữ liệu vượt giới hạn. Hãy chia nhỏ khoảng ngày.')
        result = [dict(zip(names, row)) for row in rows]
        _remaining()
        reusable = True
        return result
    except CorporateError:
        raise
    except Exception as exc:
        if not isinstance(exc, DatabaseUnavailable):
            log_database_failure(exc, phase=phase, started_at=started, operation='query', log=logger)
        raise CorporateError(503, 'SOURCE_UNAVAILABLE', 'Không đọc được nguồn SQL. Chưa công bố dữ liệu mới.') from None
    finally:
        cursor_closed = _close_source_resource(cursor)
        _release_connection(database, connection, reusable=reusable and cursor_closed)


# An optional capability lets catalog extraction use the fast metadata path,
# while injected test/query functions can keep their existing SELECT interface.
query_source.describe_table = describe_table

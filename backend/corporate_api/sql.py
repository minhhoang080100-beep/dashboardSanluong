"""Bounded source reads shared by the explicit offline extraction command."""
import logging
import re
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
_DESCRIBED_TYPES = {
    int: 'int', str: 'nvarchar', bool: 'bit', Decimal: 'decimal', float: 'float',
    datetime: 'datetime2', date: 'date', time: 'time', bytes: 'varbinary', bytearray: 'varbinary',
}


def _close_source_resource(resource):
    if resource is None:
        return
    try:
        resource.close()
    except Exception:
        # Closing a failed ODBC cursor can itself include connection details.
        # Always attempt the connection close as well; never replace the result
        # or the original safe error with raw driver text.
        logger.warning('Source cleanup failed.')


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
    started = perf_counter()
    try:
        connection = get_db_connection(database)
        cursor = connection.cursor()
        cursor.execute(f'SELECT TOP (0) * FROM [dbo].[{table}]')
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
        return result
    except CorporateError:
        raise
    except Exception as exc:
        if not isinstance(exc, DatabaseUnavailable):
            log_database_failure(exc, phase='execute', started_at=started, operation='query', log=logger)
        raise CorporateError(503, 'SOURCE_UNAVAILABLE', 'Không đọc được cấu trúc nguồn SQL. Chưa công bố dữ liệu mới.') from None
    finally:
        _close_source_resource(cursor)
        _close_source_resource(connection)


def query_source(database, statement, params=()):
    if database not in DATABASES or not re.match(r'^\s*(SELECT|WITH)\b', statement, re.IGNORECASE):
        raise ValueError('Only fixed source SELECT statements are allowed.')
    connection = cursor = None
    started = perf_counter()
    phase = 'cursor'
    try:
        connection = get_db_connection(database)
        cursor = connection.cursor()
        phase = 'execute'
        cursor.execute(statement, params)
        phase = 'fetch'
        names = [field[0] for field in cursor.description]
        rows = cursor.fetchmany(250_001)
        if len(rows) > 250_000:
            raise CorporateError(422, 'SOURCE_ROW_LIMIT', 'Kỳ lấy dữ liệu vượt giới hạn. Hãy chia nhỏ khoảng ngày.')
        return [dict(zip(names, row)) for row in rows]
    except CorporateError:
        raise
    except Exception as exc:
        if not isinstance(exc, DatabaseUnavailable):
            log_database_failure(exc, phase=phase, started_at=started, operation='query', log=logger)
        raise CorporateError(503, 'SOURCE_UNAVAILABLE', 'Không đọc được nguồn SQL. Chưa công bố dữ liệu mới.') from None
    finally:
        _close_source_resource(cursor)
        _close_source_resource(connection)


# An optional capability lets catalog extraction use the fast metadata path,
# while injected test/query functions can keep their existing SELECT interface.
query_source.describe_table = describe_table

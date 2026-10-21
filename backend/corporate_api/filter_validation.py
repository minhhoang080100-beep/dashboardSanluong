"""Bounded native-ID membership checks for an empty filtered production result.

Facts and their source references already validate nonempty results. For an
empty result, distinguish a known ID with no activity from a nonexistent ID,
without reading full catalogs or treating name conflicts as missing IDs.
"""
from .errors import CorporateError


DATABASES = ('SmartTOS', 'SmartTOS_BenThuy')
MAX_NATIVE_ID = 9223372036854775807
_EXISTS = {
    'shipId': ('[dbo].[Vessel]', '[vesselId]',
               'ISNULL([rowDeleted],0)=0 AND ISNULL([isVirtualVessel],0)=0'),
    'handlingMethodId': ('[dbo].[JobMethod]', '[jobMethodId]', 'ISNULL([rowDeleted],0)=0'),
}


def _not_found():
    return CorporateError(422, 'FILTER_ID_NOT_FOUND',
                          'Mã tàu hoặc mã phương án dùng để lọc không tồn tại trong danh mục nguồn hợp lệ.')


def _native_id(value):
    return (isinstance(value, str) and value.isascii() and value.isdigit()
            and not value.startswith('0') and len(value) <= 19 and int(value) <= MAX_NATIVE_ID)


def validate_production_filter_ids(filters, query_fn):
    """Require every selected ID to exist in at least one fully checked source.

    At most one scalar SELECT per database, regardless of whether one or both
    filters were supplied. Both databases must be readable before reporting an
    absent ID. Values are parameters, never SQL text or public error content.
    """
    if not filters:
        return
    if not isinstance(filters, dict) or set(filters) - _EXISTS.keys():
        raise ValueError('Invalid production filter identity scope')
    if not all(_native_id(value) for value in filters.values()):
        raise _not_found()
    if query_fn is None:
        raise CorporateError(503, 'SOURCE_FILTER_VALIDATION_UNAVAILABLE',
                             'Chưa có bộ đọc để xác minh mã lọc sản lượng trong danh mục nguồn.')
    fields = [field for field in _EXISTS if field in filters]
    expressions = []
    for field in fields:
        table, key, membership = _EXISTS[field]
        expressions.append(
            f'CASE WHEN EXISTS (SELECT 1 FROM {table} WHERE {key}=CAST(? AS BIGINT) '
            f'AND {membership}) THEN 1 ELSE 0 END AS [{field}]')
    statement = 'SELECT ' + ', '.join(expressions)
    params = tuple(filters[field] for field in fields)
    found = {field: False for field in fields}
    for database in DATABASES:
        try:
            rows = query_fn(database, statement, params)
        except CorporateError as exc:
            if exc.code == 'SOURCE_TIMEOUT':
                raise CorporateError(503, 'SOURCE_TIMEOUT',
                                     'Truy vấn SmartTOS quá thời gian. Hãy thử lại.', retry_after=5) from None
            raise CorporateError(503, 'SOURCE_UNAVAILABLE',
                                 'Không đọc được đầy đủ danh mục nguồn để xác minh mã lọc.', retry_after=5) from None
        except Exception:
            raise CorporateError(503, 'SOURCE_UNAVAILABLE',
                                 'Không đọc được đầy đủ danh mục nguồn để xác minh mã lọc.', retry_after=5) from None
        if (not isinstance(rows, list) or len(rows) != 1 or not isinstance(rows[0], dict)
                or set(rows[0]) != set(fields)
                or any(type(value) not in (int, bool) or value not in (0, 1) for value in rows[0].values())):
            raise CorporateError(503, 'SOURCE_DATA_NOT_READY',
                                 'Kết quả xác minh mã lọc từ danh mục nguồn không hợp lệ.')
        for field in fields:
            found[field] = found[field] or bool(rows[0][field])
    if not all(found.values()):
        raise _not_found()

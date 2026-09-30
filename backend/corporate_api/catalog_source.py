"""SELECT-only adapters for S-domain catalogs, isolated from dashboard queries.

Cargo/JobMethod/CargoDirect column evidence: local audit 2026-09-14.
Vessel identity/name evidence: current dashboard repository. Every extraction
probes the actual schema before selecting fields. Semantic mapping (cargo types,
ISO sizes and optional customer/ship attributes) requires an explicitly
approved profile; names alone never establish those business relationships.
Origins are read from CargoOrigin; production origin relationships remain separate.
"""
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
import re

from .contracts import MODELS, IDENTITY
from .errors import CorporateError

SOURCES = {'cua_lo': 'SmartTOS', 'ben_thuy': 'SmartTOS_BenThuy'}
RESOURCES = ('shipDetails', 'customers', 'cargoType', 'cargoCategory',
             'handlingMethodList', 'class', 'origins', 'containerSize')
TABLES = ('Vessel', 'Partner', 'Cargo', 'JobMethod', 'CargoDirect', 'CargoOrigin', 'CargoGroup',
          'vwContainerSizeTypeDomestic')
MAX_MASTER_ROWS = 100000
REFERENCE_CHUNK_SIZE = 500
INTEGER_TYPES = {'int', 'bigint', 'smallint', 'tinyint'}
TEXT_TYPES = {'varchar', 'nvarchar', 'char', 'nchar'}
DATE_TYPES = {'datetime', 'datetime2', 'smalldatetime', 'date', 'datetimeoffset'}
NUMBER_TYPES = INTEGER_TYPES | {'decimal', 'numeric', 'float', 'real', 'money', 'smallmoney'}
SHIP_OPTIONAL = {'shipIMO', 'shipGroup', 'flagState', 'shipLOA', 'shipBeam',
                 'shipGRT', 'shipType', 'shipDWT', 'shipOwner'}
SHIP_NUMBERS = {'shipLOA', 'shipBeam', 'shipGRT', 'shipDWT'}
CUSTOMER_OPTIONAL = {'customerNameEN', 'customerTaxCode', 'customerPhoneNum',
                     'customerAddress', 'customerEmail', 'isCarrier', 'isAgent', 'customerStatus'}
VIETNAM_TIMEZONE = timezone(timedelta(hours=7))
# Verified native reporting codes in Cargo group 1034 (both TOS databases).
# These are local reporting size/status classes, not ISO equipment types.
NATIVE_CARGO_SIZE_CODES = {'20F': '20', '20E': '20', '20R': '20',
                         '40F': '40', '40E': '40', '40R': '40',
                         '45F': '45', '45E': '45'}
NATIVE_CONTAINER_CARGO_GROUP = 1034


class SourceProblem(ValueError):
    def __init__(self, code, message):
        self.code, self.message = code, message
        super().__init__(message)


def source_id(terminal, native_id):
    """Return the native database ID, serialized as an API string without a prefix."""
    if terminal not in SOURCES or isinstance(native_id, bool):
        raise ValueError('Invalid source identity')
    value = str(native_id)
    if not re.fullmatch(r'[1-9][0-9]{0,18}', value):
        raise ValueError('Invalid source identity')
    return value


def _reference_ids(values):
    """Validate an internal native-ID scope before it reaches SQL."""
    if (not isinstance(values, (list, tuple)) or len(values) > MAX_MASTER_ROWS
            or any(not isinstance(value, str) for value in values)):
        raise ValueError('Invalid native reference scope')
    try:
        normalized = tuple(source_id('cua_lo', value) for value in values)
    except ValueError:
        raise ValueError('Invalid native reference scope') from None
    if len(set(normalized)) != len(normalized):
        raise ValueError('Duplicate native reference identity')
    return tuple(sorted(normalized, key=int))


def _reference_scope(scope, selected):
    if scope is None:
        return None
    if not isinstance(scope, dict) or set(scope) != set(selected):
        raise ValueError('Invalid catalog reference scope')
    result = {}
    for resource, terminals in scope.items():
        if not isinstance(terminals, dict) or set(terminals) != set(SOURCES):
            raise ValueError('Invalid reference source terminals')
        result[resource] = {terminal: _reference_ids(values)
                            for terminal, values in terminals.items()}
        combined = set().union(*map(set, result[resource].values()))
        if not combined or len(combined) > MAX_MASTER_ROWS:
            raise ValueError('Invalid catalog reference scope size')
    return result


def _with_provenance(row, other):
    if '_sourceTerminals' not in row and '_sourceTerminals' not in other:
        return row
    return {**row, '_sourceTerminals': sorted(
        set(row.get('_sourceTerminals', [])) | set(other.get('_sourceTerminals', [])))}


def _merge_reference_rows(resource, left, right):
    """Resolve metadata-only differences without changing business identity.

    Allowed for private dependency checks and full S catalogs without date
    filters. Full customers must stay strict because creation-date filtering
    would lose a source variant when selecting one original metadata row.
    All business fields and lifecycle flags remain strict. Select one original
    row, retaining its entire timestamp pair and customer creation-date index.
    """
    ignored = {'createdDate', 'modifiedDate', 'reportDate', '_sourceTerminals'}
    if resource == 'customers':
        ignored.add('_createdDate')  # Derived from metadata.createdDate.

    def business(row):
        result = {key:value for key,value in row.items() if key not in ignored}
        if resource == 'customers' and isinstance(result.get('metadata'), dict):
            result['metadata'] = {key:value for key,value in result['metadata'].items()
                                  if key not in {'createdDate','modifiedDate'}}
        return result

    def date_key(value):
        if value is None:
            return (0, '')
        normalized = _timestamp(value)
        parsed = datetime.fromisoformat(normalized)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=VIETNAM_TIMEZONE)
        # Compare instants without astimezone overflowing valid year boundaries.
        local = ((parsed.toordinal()*86400 + parsed.hour*3600 + parsed.minute*60
                  + parsed.second)*1000000 + parsed.microsecond)
        offset = parsed.utcoffset()
        instant = local - ((offset.days*86400 + offset.seconds)*1000000 + offset.microseconds)
        return (instant, normalized)

    def original_key(row):
        metadata = row['metadata'] if resource == 'customers' else row
        created, modified = metadata.get('createdDate'), metadata.get('modifiedDate')
        # Validate both source timestamps even if only one determines ordering.
        created_key, modified_key = date_key(created), date_key(modified)
        return (modified_key if modified is not None else created_key,
                created_key, modified_key, row.get('reportDate') or '')

    left_key, right_key = original_key(left), original_key(right)
    if business(left) != business(right):
        return None
    chosen, other = (right, left) if right_key > left_key else (left, right)
    return _with_provenance(chosen, other)


def merge_origin_rows(rows):
    """Coalesce the same native origin across databases, preserving one source row."""
    merged = {}
    for row in rows:
        key = row['originId']
        old = merged.get(key)
        if old is not None and old['originName'] != row['originName']:
            raise SourceProblem('SOURCE_ID_CONFLICT', 'Mã nguồn gốc trùng nhưng tên khác nhau giữa các database.')
        def stamp(item):
            return (item.get('modifiedDate') or item.get('createdDate') or '',
                    item.get('createdDate') or '')
        if old is None:
            merged[key] = row
        else:
            merged[key] = _with_provenance(row, old) if stamp(row) > stamp(old) else _with_provenance(old, row)
    return list(merged.values())


def _identifier(value):
    if not isinstance(value, str) or not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]*', value):
        raise SourceProblem('SOURCE_SCHEMA', 'Tên cột nguồn không hợp lệ.')
    return '[' + value + ']'


def _text(value):
    if value is None:
        return None
    result = str(value).strip()
    return result or None


def _timestamp(value):
    if value is None:
        return None
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    # Do not invent a timezone or replace a missing source timestamp with now.
    try:
        return datetime.fromisoformat(str(value).replace('Z', '+00:00')).isoformat()
    except (TypeError, ValueError):
        raise SourceProblem('SOURCE_DATA', 'Ngày tạo/sửa nguồn không hợp lệ.') from None


def _flag(value):
    # Existing TOS semantics treat nullable rowDeleted as active.
    if value in (None, False, 0):
        return False
    if value in (True, 1):
        return True
    raise SourceProblem('SOURCE_DATA', 'Cờ trạng thái nguồn không hợp lệ.')


def _base(row, report_day):
    return {'reportDate': report_day, 'createdDate': _timestamp(row.get('createTime')),
            'modifiedDate': _timestamp(row.get('updateTime'))}


def _change_day(row):
    value = row.get('updateTime') if row.get('updateTime') is not None else row.get('createTime')
    if value is None:
        return None
    if isinstance(value, date) and not isinstance(value, datetime):
        return value.strftime('%Y%m%d')
    parsed = value if isinstance(value, datetime) else datetime.fromisoformat(_timestamp(value))
    if parsed.tzinfo is not None:
        parsed = parsed.astimezone(VIETNAM_TIMEZONE)
    return parsed.strftime('%Y%m%d')


def _empty():
    return {'rows': [], 'warnings': [], 'ready': True, 'blockers': [], 'coverage': [], 'source_coverage': []}


def _block(result, problem, terminal=None):
    result['ready'] = False
    result['blockers'].append({'code': problem.code, 'message': problem.message,
                               **({'terminal': terminal} if terminal else {})})


class _Reader:
    def __init__(self, query_fn, terminal, *, tables=None):
        self.query_fn, self.terminal = query_fn, terminal
        self.database = SOURCES[terminal]
        self.cache = {}
        selected = TABLES if tables is None else tables
        if (not isinstance(selected, (tuple, list)) or not selected
                or any(not isinstance(table, str) or table not in TABLES for table in selected)
                or len(set(selected)) != len(selected)):
            raise SourceProblem('SOURCE_SCHEMA', 'Phạm vi bảng danh mục nguồn không hợp lệ.')
        selected = tuple(selected)
        describe = getattr(query_fn, 'describe_table', None)
        if callable(describe):
            # SQL Server's broad INFORMATION_SCHEMA queries can be expensive.
            # This capability uses a fresh zero-row SELECT of each needed table
            # and returns validated driver type categories, never guessed types.
            rows = []
            for table in selected:
                try:
                    columns = describe(self.database, table)
                except CorporateError as exc:
                    safe_codes = {'SOURCE_TIMEOUT', 'SOURCE_UNAVAILABLE', 'SOURCE_SCHEMA', 'SOURCE_ROW_LIMIT'}
                    code = exc.code if exc.code in safe_codes else 'SOURCE_UNAVAILABLE'
                    raise SourceProblem(code, 'Chưa đọc được cấu trúc nguồn danh mục hợp lệ.') from None
                except Exception:
                    raise SourceProblem('SOURCE_UNAVAILABLE', 'Chưa đọc được cấu trúc nguồn danh mục.') from None
                if not isinstance(columns, list) or any(not isinstance(column, dict) for column in columns):
                    raise SourceProblem('SOURCE_SCHEMA', 'Kết quả kiểm tra cấu trúc danh mục không hợp lệ.')
                rows.extend({'table_name': table, 'column_name': column.get('column_name'),
                             'data_type': column.get('data_type')} for column in columns)
        else:
            # Injected query functions without this capability keep the original
            # parameterized metadata contract. Only allowlisted values enter it.
            placeholders = ','.join('?' for _ in selected)
            rows = self._query(f"""SELECT TABLE_NAME AS table_name,COLUMN_NAME AS column_name,
                DATA_TYPE AS data_type FROM INFORMATION_SCHEMA.COLUMNS
                WHERE TABLE_SCHEMA='dbo' AND TABLE_NAME IN ({placeholders})
                ORDER BY TABLE_NAME,ORDINAL_POSITION""", selected)
        self.schema = {}
        for row in rows:
            if (not isinstance(row.get('table_name'), str)
                    or not isinstance(row.get('column_name'), str)
                    or not row['column_name'] or not isinstance(row.get('data_type'), str)
                    or not row['data_type']):
                raise SourceProblem('SOURCE_SCHEMA', 'Kết quả kiểm tra cấu trúc danh mục không hợp lệ.')
            if row.get('table_name') in selected:
                columns = self.schema.setdefault(row['table_name'], {})
                if row['column_name'] in columns:
                    raise SourceProblem('SOURCE_SCHEMA', 'Cấu trúc danh mục có tên cột trùng.')
                columns[row['column_name']] = row['data_type'].lower()

    def _query(self, sql, params=()):
        try:
            rows = self.query_fn(self.database, sql, tuple(params))
            if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
                raise TypeError('Query result must contain rows')
            return rows
        except CorporateError as exc:
            safe_errors = {
                'SOURCE_TIMEOUT': 'Truy vấn nguồn danh mục quá thời gian.',
                'SOURCE_UNAVAILABLE': 'Chưa đọc được nguồn danh mục.',
                'SOURCE_SCHEMA': 'Chưa xác minh được cấu trúc nguồn danh mục.',
                'SOURCE_ROW_LIMIT': 'Danh mục vượt giới hạn đọc an toàn.',
            }
            code = exc.code if exc.code in safe_errors else 'SOURCE_UNAVAILABLE'
            raise SourceProblem(code, safe_errors[code]) from None
        except Exception:
            # Never leak connection details, SQL text or source values in public errors.
            raise SourceProblem('SOURCE_UNAVAILABLE', 'Chưa đọc được nguồn danh mục.') from None

    def select_rows(self, table, selected, key, predicates, *, reference_ids=None):
        """Run bounded parameterized lookups; None retains full-master behavior."""
        identities = None if reference_ids is None else _reference_ids(reference_ids)
        batches = [None] if identities is None else [
            identities[offset:offset + REFERENCE_CHUNK_SIZE]
            for offset in range(0, len(identities), REFERENCE_CHUNK_SIZE)]
        rows = []
        for batch in batches:
            conditions = list(predicates)
            if batch is not None:
                conditions.append(_identifier(key) + ' IN (' + ','.join('?' for _ in batch) + ')')
            where = ' WHERE ' + ' AND '.join(conditions) if conditions else ''
            sql = (f'SELECT TOP ({MAX_MASTER_ROWS + 1}) ' + ','.join(_identifier(c) for c in selected)
                   + f' FROM [dbo].{_identifier(table)}' + where + f' ORDER BY {_identifier(key)}')
            chunk = self._query(sql, batch or ())
            if batch is not None and any(str(row.get(key)) not in batch for row in chunk):
                raise SourceProblem('SOURCE_DATA', 'Nguồn danh mục trả về mã ngoài phạm vi yêu cầu.')
            rows.extend(chunk)
            if len(rows) > MAX_MASTER_ROWS:
                raise SourceProblem('SOURCE_LIMIT', 'Danh mục vượt giới hạn đọc an toàn; cần chia lô.')
        return rows

    def read(self, table, key, name, *, include_deleted=False, optional_map=None, resource=None,
             reference_ids=None):
        identities = None if reference_ids is None else _reference_ids(reference_ids)
        columns = self.schema.get(table, {})
        required = {key: INTEGER_TYPES, name: TEXT_TYPES, 'rowDeleted': {'bit'}}
        if table == 'Vessel':
            required['isVirtualVessel'] = {'bit'}
        for col, types in required.items():
            if columns.get(col) not in types:
                raise SourceProblem('SOURCE_SCHEMA', f'Thiếu hoặc sai kiểu cột bắt buộc của {table}.')
        selected = list(required)
        warnings = []
        if table == 'Partner' and columns.get('partnerShortName') in TEXT_TYPES:
            selected.append('partnerShortName')
        if table == 'CargoOrigin' and columns.get('cargoOriginCode') in TEXT_TYPES:
            selected.append('cargoOriginCode')
        for col in ('createTime', 'updateTime'):
            if col in columns:
                if columns[col] not in DATE_TYPES:
                    raise SourceProblem('SOURCE_SCHEMA', f'Sai kiểu cột thời gian của {table}.')
                selected.append(col)
            else:
                warnings.append(f'{table}.{col} chưa có trong nguồn; trả null.')
        if table == 'Cargo' and 'cargoParentId' in columns:
            if columns['cargoParentId'] not in INTEGER_TYPES:
                raise SourceProblem('SOURCE_SCHEMA', 'Sai kiểu cargoParentId.')
            selected.append('cargoParentId')
        if table == 'Cargo' and 'cargoCode' in columns:
            if columns['cargoCode'] not in TEXT_TYPES:
                raise SourceProblem('SOURCE_SCHEMA', 'Sai kiểu cargoCode.')
            selected.append('cargoCode')
        if table == 'Cargo' and 'cargoGroupId' in columns:
            if columns['cargoGroupId'] not in INTEGER_TYPES:
                raise SourceProblem('SOURCE_SCHEMA', 'Sai kiểu cargoGroupId.')
            selected.append('cargoGroupId')
        for target, col in (optional_map or {}).items():
            _identifier(col)
            types = NUMBER_TYPES if target in SHIP_NUMBERS else ({'bit'} if target in ('isCarrier', 'isAgent') else TEXT_TYPES)
            if columns.get(col) not in types:
                raise SourceProblem('SOURCE_SCHEMA', f'Cột đã cấu hình cho {resource}.{target} chưa được xác minh.')
            selected.append(col)
        selected = list(dict.fromkeys(selected))
        signature = (table, tuple(selected), include_deleted, identities)
        if signature not in self.cache:
            predicates = []
            if not include_deleted:
                predicates.append('ISNULL([rowDeleted],0)=0')
            if table == 'Vessel':
                predicates.append('ISNULL([isVirtualVessel],0)=0')
                predicates.append('[vesselId]>0')
            rows = self.select_rows(table, selected, key, predicates, reference_ids=identities)
            self.cache[signature] = rows
        rows = self.cache[signature]
        seen = set()
        for row in rows:
            if set(selected) - set(row):
                raise SourceProblem('SOURCE_DATA', f'Kết quả nguồn {table} thiếu cột đã yêu cầu.')
            try:
                identity = source_id(self.terminal, row.get(key))
            except ValueError:
                raise SourceProblem('SOURCE_DATA', f'Khóa nguồn {table} không hợp lệ.') from None
            native_name = _text(row.get(name))
            if resource == 'origins':
                native_name = native_name or _text(row.get('cargoOriginCode'))
            if identity in seen or (resource != 'customers' and not native_name):
                raise SourceProblem('SOURCE_DATA', f'Danh mục {table} có khóa trùng hoặc thiếu tên.')
            seen.add(identity)
        if resource == 'customers':
            missing = sum(not (_text(row.get(name)) or _text(row.get('partnerShortName'))) for row in rows)
            if missing:
                warnings.append(f'{missing} khách hàng thiếu cả tên đầy đủ và tên ngắn; customerNameVN giữ null.')
        return rows, warnings


def _approved(profile):
    return isinstance(profile, dict) and profile.get('approved') is True


def _mapping(profile, key, terminal=None):
    if not _approved(profile):
        raise SourceProblem('MAPPING_REQUIRED', 'Cần cấu hình ánh xạ nghiệp vụ đã được duyệt.')
    value = profile.get(key)
    if terminal is not None and isinstance(value, dict):
        value = value.get(terminal)
    if not isinstance(value, dict) or not value:
        raise SourceProblem('MAPPING_REQUIRED', f'Chưa có ánh xạ {key}.')
    return value


def _native(reader, resource, profile, report_day, *, reference_ids=None):
    config = {'shipDetails': ('Vessel', 'vesselId', 'vesselName'),
              'customers': ('Partner', 'partnerId', 'partnerFullName'),
              'handlingMethodList': ('JobMethod', 'jobMethodId', 'jobMethodName'),
              'class': ('CargoDirect', 'cargoDirectId', 'cargoDirectName'),
              'origins': ('CargoOrigin', 'cargoOriginId', 'cargoOriginName'),
              'cargoType': ('CargoGroup', 'cargoGroupId', 'cargoGroupName')}
    table, key, name = config[resource]
    optional = SHIP_OPTIONAL if resource == 'shipDetails' else (CUSTOMER_OPTIONAL | {'customerType'} if resource == 'customers' else set())
    field_map = {}
    if _approved(profile):
        all_maps = profile.get('source_columns', {})
        if not isinstance(all_maps, dict):
            raise SourceProblem('MAPPING_REQUIRED', 'source_columns phải là object.')
        field_map = all_maps.get(resource, {})
        if not isinstance(field_map, dict) or set(field_map) - optional:
            raise SourceProblem('MAPPING_REQUIRED', 'Ánh xạ thuộc tính danh mục không hợp lệ.')
    rows, warnings = reader.read(table, key, name, include_deleted=resource == 'customers',
                                optional_map=field_map, resource=resource, reference_ids=reference_ids)
    if resource == 'shipDetails':
        warnings.append('Phạm vi danh mục tàu: vesselId > 0, chưa xóa và không phải tàu ảo.')
    if optional - set(field_map):
        warnings.append('Chưa có ánh xạ được duyệt cho: ' + ', '.join(sorted(optional - set(field_map))) + '; trả null.')
    output = []
    for raw in rows:
        row = _base(raw, report_day)
        identity = source_id(reader.terminal, raw[key])
        if resource == 'shipDetails':
            row.update(shipId=identity, shipFullName=_text(raw[name]), **{k: None for k in SHIP_OPTIONAL})
        elif resource == 'customers':
            row = {'reportDate': report_day, 'customerCode': identity,
                   'customerNameVN': _text(raw[name]) or _text(raw.get('partnerShortName')),
                   **{k: None for k in CUSTOMER_OPTIONAL},
                   '_taxCodeMapped': 'customerTaxCode' in field_map,
                   '_customerTypeMapped': 'customerType' in field_map,
                   'metadata': {'isDeleted': _flag(raw.get('rowDeleted')),
                                'createdDate': row['createdDate'], 'modifiedDate': row['modifiedDate']},
                   # S/customer startDate/endDate filter source creation only.
                   '_createdDate': _change_day({'createTime': raw.get('createTime')})}
        elif resource == 'handlingMethodList':
            row.update(handlingMethodId=identity, handlingMethodName=_text(raw[name]))
        elif resource == 'origins':
            row.update(originId=identity, originName=_text(raw[name]) or _text(raw.get('cargoOriginCode')))
            if not _text(raw[name]):
                warnings.append('cargoOriginName trống: dùng nguyên cargoOriginCode từ nguồn làm tên hiển thị.')
        elif resource == 'cargoType':
            row.update(cargoTypeId=identity, cargoTypeName=_text(raw[name]))
        else:
            row.update(classId=identity, className=_text(raw[name]))
        for target, col in field_map.items():
            value = raw.get(col)
            if target == 'customerType':
                row['_customerType'] = _text(value)
            elif value is None:
                row[target] = None
            elif target in ('isCarrier', 'isAgent'):
                row[target] = _flag(value)
            elif target in SHIP_NUMBERS:
                try:
                    amount = Decimal(str(value))
                    if not amount.is_finite() or amount < 0:
                        raise InvalidOperation()
                    row[target] = amount
                except (ValueError, InvalidOperation):
                    raise SourceProblem('SOURCE_DATA', 'Thông số tàu không hợp lệ.') from None
            else:
                row[target] = _text(value)
        output.append(row)
    return output, warnings


def _configured_categories(reader, profile, report_day):
    type_names = _mapping(profile, 'cargo_types')
    assignments = _mapping(profile, 'cargo_type_by_cargo', reader.terminal)
    return _category_rows(reader, assignments, type_names, report_day)


def _native_categories(reader, report_day, *, reference_ids=None):
    if reader.schema.get('Cargo', {}).get('cargoGroupId') not in INTEGER_TYPES:
        raise SourceProblem('SOURCE_SCHEMA', 'Chưa xác minh quan hệ Cargo.cargoGroupId.')
    cargos, warnings = reader.read('Cargo', 'cargoId', 'cargoName', reference_ids=reference_ids)
    group_ids = None
    if reference_ids is not None:
        try:
            group_ids = sorted({source_id(reader.terminal, row.get('cargoGroupId')) for row in cargos}, key=int)
        except ValueError:
            raise SourceProblem('SOURCE_DATA', 'Mặt hàng thiếu cargoGroupId hợp lệ.') from None
    groups, group_warnings = reader.read('CargoGroup', 'cargoGroupId', 'cargoGroupName', reference_ids=group_ids)
    type_names = {source_id(reader.terminal, r['cargoGroupId']): r['cargoGroupName'] for r in groups}
    try:
        assignments = {source_id(reader.terminal, r['cargoId']): source_id(reader.terminal, r.get('cargoGroupId')) for r in cargos}
    except ValueError:
        raise SourceProblem('SOURCE_DATA', 'Mặt hàng thiếu cargoGroupId hợp lệ.') from None
    if reference_ids is None:
        return _category_rows(reader, assignments, type_names, report_day)
    # A scoped dependency extraction is a node batch, not a complete tree.
    # The caller must grow the scope with these parent/type references until
    # closure and validate the complete graph before returning production data.
    if reader.schema.get('Cargo', {}).get('cargoParentId') not in INTEGER_TYPES:
        raise SourceProblem('SOURCE_SCHEMA', 'Chưa xác minh quan hệ nhóm hàng cha cargoParentId.')
    if set(assignments.values()) - set(type_names):
        raise SourceProblem('SOURCE_REFERENCE_NOT_FOUND', 'Không tìm thấy nhóm hàng trong đúng nguồn tham chiếu.')
    output = []
    for raw in cargos:
        identity = source_id(reader.terminal, raw['cargoId'])
        parent = raw.get('cargoParentId')
        try:
            parent_id = source_id(reader.terminal, parent) if parent not in (None, 0) else None
        except ValueError:
            raise SourceProblem('SOURCE_DATA', 'Mã nhóm hàng cha nguồn không hợp lệ.') from None
        row = _base(raw, report_day)
        row.update(cargoId=identity, cargoName=_text(raw['cargoName']),
                   cargoTypeId=assignments[identity], cargoParentId=parent_id)
        output.append(row)
    parents = {row['cargoId']: row['cargoParentId'] for row in output}
    for current in parents:
        visited = set()
        while current in parents:
            if current in visited:
                raise SourceProblem('SOURCE_DATA', 'Cây danh mục hàng có vòng lặp.')
            visited.add(current)
            current = parents[current]
    return output, warnings + group_warnings


def _category_rows(reader, assignments, type_names, report_day):
    if reader.schema.get('Cargo', {}).get('cargoParentId') not in INTEGER_TYPES:
        raise SourceProblem('SOURCE_SCHEMA', 'Chưa xác minh quan hệ nhóm hàng cha cargoParentId.')
    rows, warnings = reader.read('Cargo', 'cargoId', 'cargoName')
    by_id = {str(r['cargoId']): r for r in rows}
    if set(assignments) - set(by_id):
        raise SourceProblem('MAPPING_REQUIRED', 'Danh mục hàng đã ánh xạ không còn tồn tại/hoạt động trong nguồn.')
    output = []
    for native_id, type_id in assignments.items():
        if not isinstance(type_id, str) or type_id not in type_names:
            raise SourceProblem('MAPPING_REQUIRED', 'cargoTypeId chưa có trong danh mục loại hàng đã duyệt.')
        raw = by_id[native_id]
        parent = raw.get('cargoParentId')
        if parent not in (None, 0):
            if str(parent) not in assignments or str(parent) == native_id:
                raise SourceProblem('MAPPING_REQUIRED', 'Thiếu nhóm hàng cha đã ánh xạ hoặc quan hệ cha không hợp lệ.')
        row = _base(raw, report_day)
        row.update(cargoId=source_id(reader.terminal, native_id), cargoName=_text(raw['cargoName']),
                   cargoTypeId=type_id, cargoParentId=source_id(reader.terminal, parent) if parent not in (None, 0) else None)
        output.append(row)
    # Reject longer cycles as well, not just self-parent records.
    parent_by_id = {r['cargoId']: r['cargoParentId'] for r in output}
    for current in parent_by_id:
        visited = set()
        while current:
            if current in visited:
                raise SourceProblem('SOURCE_DATA', 'Cây danh mục hàng có vòng lặp.')
            visited.add(current)
            current = parent_by_id[current]
    if len(assignments) != len(rows):
        warnings.append('Chỉ xuất danh mục hàng đã được ánh xạ trong phạm vi duyệt; không phải toàn bộ Cargo.')
    return output, warnings


def _configured_sizes(reader, profile, report_day):
    assignments = _mapping(profile, 'container_sizes_by_cargo', reader.terminal)
    rows, warnings = reader.read('Cargo', 'cargoId', 'cargoName')
    by_id = {str(r['cargoId']): r for r in rows}
    if set(assignments) - set(by_id):
        raise SourceProblem('MAPPING_REQUIRED', 'Mã hàng container đã ánh xạ không có trong nguồn đang hoạt động.')
    output = []
    fields = {'localSzTp', 'isoSzTp', 'sizeCode', 'heightCode', 'containerTypeCode'}
    for native_id, mapping in assignments.items():
        if not isinstance(mapping, dict) or set(mapping) - fields or not _text(mapping.get('localSzTp')):
            raise SourceProblem('MAPPING_REQUIRED', 'Ánh xạ kích thước container không hợp lệ.')
        raw = by_id[native_id]
        row = _base(raw, report_day)
        row.update(containerSizeId=source_id(reader.terminal, native_id),
                   **{field: _text(mapping.get(field)) for field in fields})
        output.append(row)
    warnings.append('Chỉ xuất kích cỡ đã ánh xạ; không suy ISO, loại hay chiều cao từ 20F/40E.')
    return output, warnings


def _native_cargo_sizes(reader, report_day, *, reference_ids=None):
    """Expose approved local reporting classes using their existing Cargo IDs.

    This explicit mode does not link a reporting class to an ISO/domestic size
    row. Unknown physical height/type/ISO remain null. A scoped ID is checked
    in each source that actually references it; standalone catalogs read both.
    """
    table = 'Cargo'
    columns = reader.schema.get(table, {})
    required = {'cargoId': INTEGER_TYPES, 'cargoCode': TEXT_TYPES,
                'cargoName': TEXT_TYPES, 'cargoGroupId': INTEGER_TYPES, 'rowDeleted': {'bit'}}
    if any(columns.get(key) not in types for key, types in required.items()):
        raise SourceProblem('SOURCE_SCHEMA', 'Chưa xác minh đủ cột mã container báo cáo trong Cargo.')
    selected, warnings = list(required), []
    for field in ('createTime', 'updateTime'):
        if field not in columns:
            warnings.append(f'{table}.{field} chưa có trong nguồn; trả null.')
        elif columns[field] not in DATE_TYPES:
            raise SourceProblem('SOURCE_SCHEMA', 'Sai kiểu ngày của danh mục Cargo.')
        else:
            selected.append(field)
    predicates = ['ISNULL([rowDeleted],0)=0']
    if reference_ids is None:
        codes = ','.join("N'" + code + "'" for code in NATIVE_CARGO_SIZE_CODES)
        predicates += [f'[cargoGroupId]={NATIVE_CONTAINER_CARGO_GROUP}',
                       f'[cargoCode] IN ({codes})', '[cargoName]=[cargoCode]']
    rows = reader.select_rows(table, selected, 'cargoId', predicates, reference_ids=reference_ids)
    output, seen = [], set()
    for raw in rows:
        if set(selected) - set(raw):
            raise SourceProblem('SOURCE_DATA', 'Danh mục Cargo thiếu cột nguồn đã yêu cầu.')
        if _flag(raw['rowDeleted']):
            continue
        try:
            identity = source_id(reader.terminal, raw['cargoId'])
        except ValueError:
            raise SourceProblem('SOURCE_DATA', 'Khóa Cargo nguồn không hợp lệ.') from None
        if identity in seen:
            raise SourceProblem('SOURCE_DATA', 'Danh mục mã container báo cáo có ID trùng.')
        seen.add(identity)
        code, name = _text(raw['cargoCode']), _text(raw['cargoName'])
        if (raw['cargoGroupId'] != NATIVE_CONTAINER_CARGO_GROUP
                or code not in NATIVE_CARGO_SIZE_CODES or name != code):
            raise SourceProblem('MAPPING_REQUIRED',
                'Mã Cargo được tham chiếu chưa phải mã container báo cáo đã xác minh.')
        output.append({**_base(raw, report_day), 'containerSizeId': identity,
                       'localSzTp': name, 'sizeCode': NATIVE_CARGO_SIZE_CODES[code],
                       'isoSzTp': None, 'heightCode': None, 'containerTypeCode': None})
    warnings.append('containerSizeId giữ Cargo ID của mã container báo cáo nội bộ; '
                    'chưa có ISO, mã chiều cao và loại vật lý, các trường này trả null.')
    return output, warnings


def _native_sizes(reader, report_day, *, reference_ids=None):
    """Use domestic row identity: several local codes can share an ISO type ID.

    SQL evidence 2026-09-23: type 321 is ISO 45G0 with containerSize='40'.
    Never derive length from ISO prefixes or collapse different local codes.
    """
    table = 'vwContainerSizeTypeDomestic'
    columns = reader.schema.get(table, {})
    required = {'containerSizeTypeDomesticId': INTEGER_TYPES,
                'containerSizeTypeDomesticCode': TEXT_TYPES,
                'containerSizeTypeId': INTEGER_TYPES, 'containerSizeTypeCode': TEXT_TYPES,
                'containerSize': TEXT_TYPES, 'rowDeleted': {'bit'}, 'rowInvisible': {'bit'}}
    if any(columns.get(key) not in types for key, types in required.items()):
        raise SourceProblem('SOURCE_SCHEMA', 'Chưa xác minh đủ cột danh mục kích cỡ container nguồn.')
    selected = list(required)
    warnings = []
    for field in ('createTime', 'updateTime'):
        if field in columns:
            if columns[field] not in DATE_TYPES:
                raise SourceProblem('SOURCE_SCHEMA', 'Sai kiểu ngày danh mục kích cỡ container.')
            selected.append(field)
        else:
            warnings.append(f'{table}.{field} chưa có trong nguồn; trả null.')
    rows = reader.select_rows(table, selected, 'containerSizeTypeDomesticId',
        ['ISNULL([rowDeleted],0)=0', 'ISNULL([rowInvisible],0)=0'], reference_ids=reference_ids)
    if len(rows) > MAX_MASTER_ROWS:
        raise SourceProblem('SOURCE_LIMIT', 'Danh mục kích cỡ vượt giới hạn đọc an toàn.')
    output, seen = [], set()
    for raw in rows:
        if set(selected) - set(raw):
            raise SourceProblem('SOURCE_DATA', 'Danh mục kích cỡ thiếu trường nguồn.')
        if _flag(raw['rowDeleted']) or _flag(raw['rowInvisible']):
            continue
        try:
            identity = source_id(reader.terminal, raw['containerSizeTypeDomesticId'])
        except ValueError:
            raise SourceProblem('SOURCE_DATA', 'Khóa kích cỡ container nguồn không hợp lệ.') from None
        if identity in seen or not _text(raw['containerSizeTypeDomesticCode']):
            raise SourceProblem('SOURCE_DATA', 'Khóa kích cỡ trùng hoặc thiếu mã kích cỡ nguồn.')
        seen.add(identity)
        row = _base(raw, report_day)
        row.update(containerSizeId=identity, localSzTp=_text(raw['containerSizeTypeDomesticCode']),
                   isoSzTp=_text(raw['containerSizeTypeCode']), sizeCode=_text(raw['containerSize']),
                   heightCode=None, containerTypeCode=None)
        output.append(row)
    warnings.append('Khóa containerSizeId là containerSizeTypeDomesticId; chưa suy mã chiều cao/loại từ chuỗi ISO.')
    return output, warnings


def extract_catalogs(query_fn, company_id='CNT', *, profile=None, report_date=None, resources=None,
                     reference_scope=None):
    """Return per-resource readiness, validated rows and explicit blockers.

    query_fn(database, SELECT_sql, parameter_tuple) -> list[dict].
    Native masters require both terminal sources. Empty approved semantic maps
    never mean that the company has no such data. S/customer date filters use
    metadata.createdDate only, as specified by the customer query parameter
    table. Exported timestamps preserve source values and nulls.

    reference_scope is internal only: resource -> terminal -> native ID list.
    It reads each terminal's actual used IDs, checks existence in that same
    source, and returns private source provenance. It does
    not resolve cargo parent closure; the caller must request and validate all
    ancestors before using these partial catalogs to validate production rows.
    """
    if company_id != 'CNT':
        raise ValueError('Unsupported company')
    selected = list(RESOURCES if resources is None else resources)
    if (not selected or len(set(selected)) != len(selected)
            or any(resource not in RESOURCES for resource in selected)):
        raise ValueError('Invalid catalog resource selection')
    scope = _reference_scope(reference_scope, selected)
    report_day = (report_date or datetime.now(VIETNAM_TIMEZONE).date()).strftime('%Y%m%d')
    results = {resource: _empty() for resource in selected}
    cargo_mode = (profile or {}).get('cargo_catalog_source', 'configured')
    if cargo_mode not in {'configured', 'native_groups'}:
        raise ValueError('Invalid cargo catalog source')
    size_mode = (profile or {}).get('container_size_source', 'configured')
    if size_mode not in {'configured', 'native_domestic', 'native_cargo'}:
        raise ValueError('Invalid container size source')
    if scope is not None:
        if (set(selected) & {'cargoType', 'cargoCategory'} and cargo_mode != 'native_groups'):
            raise ValueError('Native cargo catalogs required for reference scope')
        if 'containerSize' in selected and size_mode not in {'native_domestic', 'native_cargo'}:
            raise ValueError('Native container sizes required for reference scope')
    for resource, mapping_key, id_key, name_key in (
        ('cargoType', 'cargo_types', 'cargoTypeId', 'cargoTypeName'),
    ):
        if resource not in results:
            continue
        if cargo_mode == 'native_groups':
            continue
        try:
            mapping = _mapping(profile, mapping_key)
            for identity, name in mapping.items():
                if not isinstance(identity, str) or not isinstance(name, str) or not identity.strip() or not name.strip():
                    raise SourceProblem('MAPPING_REQUIRED', 'Danh mục được duyệt có mã/tên không hợp lệ.')
                results[resource]['rows'].append({'reportDate': report_day, id_key: identity,
                    name_key: name, 'createdDate': None, 'modifiedDate': None})
            results[resource]['warnings'].append('Danh mục từ cấu hình nghiệp vụ đã duyệt; chưa có timestamp nguồn.')
        except SourceProblem as exc:
            _block(results[resource], exc)
    native = ('shipDetails', 'customers', 'handlingMethodList', 'class', 'origins', 'cargoCategory', 'containerSize')
    if cargo_mode == 'native_groups':
        native += ('cargoType',)
    native = tuple(resource for resource in native if resource in results)
    table_dependencies = {
        'shipDetails': ('Vessel',), 'customers': ('Partner',),
        'handlingMethodList': ('JobMethod',), 'class': ('CargoDirect',),
        'origins': ('CargoOrigin',), 'cargoType': ('CargoGroup',),
        'cargoCategory': ('Cargo', 'CargoGroup') if cargo_mode == 'native_groups' else ('Cargo',),
        'containerSize': ('vwContainerSizeTypeDomestic',) if size_mode == 'native_domestic' else ('Cargo',),
    }
    terminals = (profile or {}).get('terminals', list(SOURCES))
    if (not isinstance(terminals, list) or not terminals
            or any(not isinstance(t, str) or t not in SOURCES for t in terminals)
            or len(set(terminals)) != len(terminals)):
        raise ValueError('Invalid source terminals')
    if scope is not None and set(terminals) != set(SOURCES):
        raise ValueError('Reference scope requires both source terminals')
    for terminal in terminals:
        terminal_native = tuple(resource for resource in native
                                if scope is None or scope[resource][terminal])
        if scope is not None:
            for resource in native:
                if resource not in terminal_native:
                    results[resource]['source_coverage'].append({'terminal': terminal, 'row_count': 0})
        if not terminal_native:
            # No dependency on this terminal's catalogs. Production extraction
            # must still verify both fact sources before constructing the scope.
            # Do not let an unused table/schema block valid source-specific IDs.
            continue
        needed = {table for resource in terminal_native for table in table_dependencies[resource]}
        selected_tables = tuple(table for table in TABLES if table in needed)
        try:
            reader = _Reader(query_fn, terminal, tables=selected_tables)
        except SourceProblem as exc:
            for resource in terminal_native:
                _block(results[resource], exc, terminal)
            continue
        for resource in terminal_native:
            try:
                reference_ids = None if scope is None else sorted(
                    scope[resource][terminal], key=int)
                if resource == 'cargoCategory':
                    rows, warnings = (_native_categories(reader, report_day, reference_ids=reference_ids) if cargo_mode == 'native_groups'
                                      else _configured_categories(reader, profile, report_day))
                elif resource == 'containerSize':
                    if size_mode == 'native_cargo':
                        rows, warnings = _native_cargo_sizes(reader, report_day, reference_ids=reference_ids)
                    else:
                        rows, warnings = (_native_sizes(reader, report_day, reference_ids=reference_ids) if size_mode == 'native_domestic'
                                          else _configured_sizes(reader, profile, report_day))
                else:
                    rows, warnings = _native(reader, resource, profile, report_day, reference_ids=reference_ids)
                if scope is not None:
                    found = {row[IDENTITY[resource]] for row in rows}
                    if set(scope[resource][terminal]) - found:
                        raise SourceProblem('SOURCE_REFERENCE_NOT_FOUND',
                            'Không tìm thấy mã danh mục trong đúng nguồn tham chiếu.')
                    rows = [{**row, '_sourceTerminals': [terminal]} for row in rows]
                results[resource]['rows'].extend(rows)
                results[resource]['warnings'].extend(warnings)
                results[resource]['source_coverage'].append({'terminal': terminal, 'row_count': len(rows)})
            except SourceProblem as exc:
                _block(results[resource], exc, terminal)
    for resource, result in results.items():
        result['warnings'] = list(dict.fromkeys(result['warnings']))
        # Native IDs may overlap between databases. Only identical catalog rows
        # can be coalesced apart from source metadata dates. Full customers are
        # strict because their public query filters by creation date; other S
        # catalogs have no date filter. Scoped dependencies are never published.
        if result['ready']:
            unique = {}
            conflict_ids = set()
            for row in result['rows']:
                key = row[IDENTITY[resource]]
                old = unique.get(key)
                comparable = {k: v for k, v in row.items() if k != '_sourceTerminals'}
                if old is not None and {k: v for k, v in old.items() if k != '_sourceTerminals'} != comparable:
                    if scope is not None or resource != 'customers':
                        try:
                            merged = _merge_reference_rows(resource, old, row)
                        except SourceProblem as exc:
                            _block(result, exc)
                            continue
                        if merged is not None:
                            unique[key] = merged
                            continue
                    conflict_ids.add(key)
                unique[key] = _with_provenance(row, old) if old is not None else row
            if conflict_ids:
                result['identity_conflicts'] = sorted(conflict_ids)[:50]
                result['identity_conflict_count'] = len(conflict_ids)
                _block(result, SourceProblem('SOURCE_ID_CONFLICT',
                    'ID gốc trùng giữa các database nhưng nội dung khác nhau; cần quy định mã từ bên nhận.'))
            if result['ready']:
                result['rows'] = list(unique.values())
        if result['ready']:
            try:
                result['rows'] = [
                    {**MODELS[resource].model_validate({k: v for k, v in row.items() if not k.startswith('_')}).model_dump(mode='python'),
                     **{k: v for k, v in row.items() if k.startswith('_')}}
                    for row in result['rows']
                ]
            except Exception:
                _block(result, SourceProblem('SOURCE_DATA', 'Dữ liệu nguồn không đáp ứng hợp đồng danh mục.'))
        if not result['ready']:
            # Company scope must never silently publish only the successful terminal.
            result['rows'] = []
    return results

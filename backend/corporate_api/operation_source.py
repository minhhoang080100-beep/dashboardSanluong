"""Bounded, SELECT-only SmartTOS adapters for B/C operations catalogs.

Native IDs and soft deletions are preserved. This adapter does not infer rental,
dangerous-goods or employment states from unrelated flags. Required fields with
no verified source mapping block the resource, including for an empty table.
Approved overrides choose columns from whitelisted tables; never SQL fragments.
"""
from datetime import date, datetime
from decimal import Decimal
import json
import re
from types import UnionType
from typing import Annotated, Literal, Union, get_args, get_origin
from pydantic import ValidationError

from .catalog_source import (SOURCES, INTEGER_TYPES, TEXT_TYPES, DATE_TYPES,
                             NUMBER_TYPES, SourceProblem, VIETNAM_TIMEZONE,
                             _timestamp, _change_day)
from .operation_contracts import OPERATION_MODELS, OPERATION_IDENTITY
from .metadata_identity import policies as metadata_policies, merge_rows
from .attribute_completion import policies as attribute_policies, complete_rows
from .organization_scope import settings as team_scope_settings, apply_scope, check_selected_ids

MAX_MASTER_ROWS = 100000
RESOURCES = tuple(OPERATION_MODELS)


def _simple(table, api_prefix, native_prefix=None):
    native_prefix = native_prefix or api_prefix
    return {'table': table, 'columns': {
        api_prefix + suffix: native_prefix + suffix for suffix in ('Id', 'Code', 'Name')}}


# Direct source semantics only. The 25 September ODBC column descriptions
# verified these tables in both databases; every extraction checks them again.
# A verified table can still lack required contract fields or business meaning.
DEFAULT_MAPPINGS = {
    'oprt.portEquipment': {'table': 'Equipment', 'columns': {
        'equipmentId': 'equipmentId', 'equipmentCode': 'equipmentCode',
        'equipmentName': 'equipmentName', 'equipmentTypeId': 'equipmentTypeId'}},
    'oprt.portEquipType': _simple('EquipmentType', 'equipmentType'),
    'oprt.portWHYard': {'table': 'Warehouse', 'columns': {
        'whYardId': 'warehouseId', 'whYardCode': 'warehouseCode',
        'whYardTypeId': 'warehouseTypeId'}},
    'oprt.portWHYardType': {'table': 'WarehouseType', 'columns': {
        'whTypeId': 'warehouseTypeId', 'whYardTypeCode': 'warehouseTypeCode',
        'whYardTypeName': 'warehouseTypeName'}},
    'oprt.berths': {'table': 'Berth', 'columns': {
        'berthId': 'berthId', 'berthCode': 'berthCode', 'berthSeq': 'berthSortOrder',
        'berthDepth': 'berthDepth', 'posFrom': 'berthFromMet', 'posTo': 'berthToMet'}},
    'oprt.jobType': _simple('JobMethodType', 'jobType', 'jobMethodType'),
    'oprt.jobMethod': _simple('JobMethod', 'jobMethod'),
    'oprt.deliveryMethod': _simple('DeliveryMethod', 'deliveryMethod'),
    # The API asks for service TYPES, referenced by PortService.portServiceTypeId.
    # PortService itself contains detailed billable service items.
    'oprt.serviceType': _simple('PortServiceType', 'serviceType', 'portServiceType'),
    'oprt.cargoItems': {'table': 'Cargo', 'columns': {
        'cargoItemId': 'cargoId', 'cargoItemCode': 'cargoCode',
        'cargoItemName': 'cargoName', 'cargoGroupId': 'cargoGroupId'}},
    'oprt.cargoGroups': _simple('CargoGroup', 'cargoGroup'),
    'oprt.unitMeasurement': _simple('BaseUnit', 'unit', 'baseUnit'),
    'oprt.cargoDirect': _simple('CargoDirect', 'cargoDirect'),
    'oprt.operationLocationType': _simple('PositionWorking', 'operationLocationType', 'positionWorking'),
    'oprt.portOpTeam': _simple('Organization', 'team', 'organization'),
    'oprt.portOpStaff': {'table': 'Employee', 'columns': {
        'staffId': 'employeeId', 'staffCode': 'employeeCode', 'staffName': 'employeeFullName',
        'teamId': 'organizationMainId'}},
    'oprt.vesselType': _simple('VesselType', 'vesselType'),
    'oprt.equipments': _simple('Equipment', 'equipment'),
    'oprt.contwhYards': {'table': 'Warehouse', 'columns': {
        'whYardId': 'warehouseId', 'whYardCode': 'warehouseCode',
        'whYardTypeId': 'warehouseTypeId'}},
    'oprt.contSizeType': {'table': 'ContainerSizeType', 'columns': {
        'contSizeTypeId': 'containerSizeTypeId', 'contSizeTypeCode': 'containerSizeTypeCode',
        'contSizeTypeName': 'containerSizeTypeDescription', 'teu': 'containerTeu'}},
}
TABLES = frozenset(item['table'] for item in DEFAULT_MAPPINGS.values()) | {
    'JobType', 'ServiceType', 'PortOpTeam', 'PortOpStaff', 'Staff', 'WorkTeam', 'Gang',
    'WarehouseArea', 'VesselType', 'vwContainerSizeTypeDomestic', 'OperationLocationType',
}
COMMON_COLUMNS = {'isDeleted': 'rowDeleted', 'createdDate': 'createTime', 'modifiedDate': 'updateTime'}


def _identifier(value):
    if not isinstance(value, str) or not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]*', value):
        raise SourceProblem('SOURCE_SCHEMA', 'Tên bảng/cột nguồn không hợp lệ.')
    return '[' + value + ']'


def _types(annotation):
    origin = get_origin(annotation)
    if origin is Annotated:
        return _types(get_args(annotation)[0])
    if origin in (UnionType, Union):
        return set().union(*(_types(arg) for arg in get_args(annotation) if arg is not type(None)))
    if origin is Literal:
        return {type(value) for value in get_args(annotation)}
    return {origin or annotation}


def _sql_types(field, target):
    types = _types(field.annotation)
    if target.endswith('Id') or target == 'whTypeId':
        return INTEGER_TYPES | TEXT_TYPES | {'uniqueidentifier'}
    if bool in types:
        return {'bit'} | INTEGER_TYPES
    if date in types or datetime in types:
        return DATE_TYPES
    if Decimal in types or int in types:
        return NUMBER_TYPES
    return TEXT_TYPES


def _mapping(resource, profile):
    original = DEFAULT_MAPPINGS[resource]
    result = {'table': original['table'], 'columns': {**COMMON_COLUMNS, **original['columns']},
              'value_maps': {}, 'transforms': {}}
    override = (profile or {}).get('operation_sources', {}).get(resource)
    if override is not None:
        if (profile or {}).get('approved') is not True:
            raise SourceProblem('MAPPING_REQUIRED', 'Ánh xạ danh mục vận hành chưa được duyệt.')
        if not isinstance(override, dict) or set(override) - {'table', 'columns', 'value_maps', 'transforms'}:
            raise SourceProblem('MAPPING_REQUIRED', 'Cấu hình ánh xạ danh mục vận hành không hợp lệ.')
        if 'table' in override:
            result['table'] = override['table']
        for key in ('columns', 'value_maps', 'transforms'):
            if key in override:
                if not isinstance(override[key], dict):
                    raise SourceProblem('MAPPING_REQUIRED', 'Cấu hình ánh xạ danh mục vận hành không hợp lệ.')
                result[key].update(override[key])
    if not isinstance(result['table'], str) or result['table'] not in TABLES:
        raise SourceProblem('SOURCE_SCHEMA', 'Bảng nguồn nằm ngoài danh sách danh mục được phép.')
    if resource == 'oprt.portOpTeam' and result['table'] == 'Gang':
        # Verified in both source DBs: Gang stores shift/voyage assignments.
        # Adding a status mapping cannot turn those transactions into teams.
        raise SourceProblem('MAPPING_REQUIRED',
            'Gang là phân công theo chuyến/ngày/ca, không phải danh mục đội. Cần ánh xạ nguồn tổ/đội đã xác minh.')
    fields = OPERATION_MODELS[resource].model_fields
    if any(set(result[key]) - set(fields) for key in ('columns', 'value_maps', 'transforms')):
        raise SourceProblem('MAPPING_REQUIRED', 'Ánh xạ có trường ngoài hợp đồng API.')
    for target, column in result['columns'].items():
        _identifier(column)
        if target in {'companyId', 'reportDate'}:
            raise SourceProblem('MAPPING_REQUIRED', 'Không được thay mã công ty hoặc ngày lấy dữ liệu bằng ánh xạ.')
    for target, mapping in result['value_maps'].items():
        if not isinstance(mapping, dict) or target not in result['columns']:
            raise SourceProblem('MAPPING_REQUIRED', 'Ánh xạ giá trị phải gắn với cột nguồn đã xác minh.')
        if target.endswith('Id') or target in {OPERATION_IDENTITY[resource], 'companyId'}:
            raise SourceProblem('MAPPING_REQUIRED', 'Không được thay ID gốc bằng ánh xạ giá trị.')
    for target, transform in result['transforms'].items():
        if (transform != 'json_array' or target not in result['columns']
                or list not in _types(fields[target].annotation)):
            raise SourceProblem('MAPPING_REQUIRED', 'Phép chuyển đổi nguồn chưa được hỗ trợ hoặc sai trường.')
    apply_scope(resource, result, profile)
    return result


class _Reader:
    def __init__(self, query_fn, terminal):
        self.query_fn, self.database, self.terminal = query_fn, SOURCES[terminal], terminal
        self.cache, self.schema = {}, {}
    def table_schema(self, table):
        # One broken table must not prevent unrelated catalogs from extracting.
        if table not in self.schema:
            describe = getattr(self.query_fn, 'describe_table', None)
            if callable(describe):
                try:
                    rows = describe(self.database, table)
                except Exception as exc:
                    code = 'SOURCE_SCHEMA' if getattr(exc, 'code', None) == 'SOURCE_SCHEMA' else 'SOURCE_UNAVAILABLE'
                    raise SourceProblem(code, 'Chưa đọc được cấu trúc bảng nguồn.') from None
                if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
                    raise SourceProblem('SOURCE_SCHEMA', 'Kết quả kiểm tra cấu trúc nguồn không hợp lệ.')
            else:
                # Captured/fake query providers retain the simple query protocol.
                rows = self.query("""SELECT c.name AS column_name,TYPE_NAME(c.user_type_id) AS data_type
                    FROM sys.columns c
                    WHERE c.object_id=OBJECT_ID(?) ORDER BY c.column_id""", ('dbo.' + table,))
            if any(not isinstance(row.get('column_name'), str) or not isinstance(row.get('data_type'), str)
                   for row in rows):
                raise SourceProblem('SOURCE_SCHEMA', 'Kết quả kiểm tra cấu trúc nguồn không hợp lệ.')
            self.schema[table] = {row['column_name']: str(row['data_type']).lower() for row in rows}
        return self.schema[table]

    def query(self, sql, params=()):
        try:
            result = self.query_fn(self.database, sql, tuple(params))
            if not isinstance(result, list) or any(not isinstance(row, dict) for row in result):
                raise TypeError('Invalid rows')
            return result
        except Exception:
            raise SourceProblem('SOURCE_UNAVAILABLE', 'Chưa đọc được nguồn danh mục vận hành.') from None

    def read(self, resource, mapping):
        table, columns = mapping['table'], mapping['columns']
        fields = OPERATION_MODELS[resource].model_fields
        missing = [name for name, field in fields.items() if field.is_required()
                   and name not in {'reportDate', 'companyId'} and name not in columns]
        if missing:
            raise SourceProblem('MAPPING_REQUIRED', 'Chưa có ánh xạ trường bắt buộc: ' + ', '.join(missing) + '.')
        schema = self.table_schema(table)
        if not schema:
            raise SourceProblem('SOURCE_SCHEMA', f'Chưa xác minh bảng danh mục {table}.')
        usable, warnings = {}, []
        for name, column in columns.items():
            if column not in schema:
                if fields[name].is_required() or name == 'isDeleted':
                    raise SourceProblem('SOURCE_SCHEMA', f'Thiếu cột nguồn {table}.{column} cho {name}.')
                warnings.append(f'{table}.{column} chưa có trong nguồn; {name} trả null.')
                continue
            allowed = _sql_types(fields[name], name)
            if name in mapping['value_maps'] or name in mapping['transforms']:
                allowed = TEXT_TYPES | NUMBER_TYPES | {'bit'}
            if schema[column] not in allowed:
                raise SourceProblem('SOURCE_SCHEMA', f'Sai kiểu cột nguồn {table}.{column} cho {name}.')
            usable[name] = column
        key = usable.get(OPERATION_IDENTITY[resource])
        selected = tuple(dict.fromkeys(usable.values()))
        scope_ids = mapping.get('team_scope', {}).get(self.terminal)
        scope_column = mapping.get('scope_column')
        if scope_ids is not None:
            if schema.get(scope_column) not in INTEGER_TYPES:
                raise SourceProblem('SOURCE_SCHEMA', 'Chưa xác minh cột liên kết tổ trong nguồn.')
            organization_schema = self.table_schema('Organization')
            if organization_schema.get('organizationId') not in INTEGER_TYPES:
                raise SourceProblem('SOURCE_SCHEMA', 'Chưa xác minh khóa danh mục tổ nguồn.')
            scope_signature = ('team_scope', scope_ids)
            if scope_signature not in self.cache:
                placeholders = ','.join('?' for _ in scope_ids)
                found = self.query(f'SELECT TOP ({len(scope_ids) + 1}) [organizationId] '
                    f'FROM [dbo].[Organization] WHERE [organizationId] IN ({placeholders}) '
                    'ORDER BY [organizationId]', scope_ids)
                check_selected_ids(found, scope_ids)
                self.cache[scope_signature] = True
        signature = (table, selected, key, scope_ids)
        if signature not in self.cache:
            sql = f'SELECT TOP ({MAX_MASTER_ROWS + 1}) ' + ','.join(_identifier(c) for c in selected)
            sql += f' FROM [dbo].{_identifier(table)}'
            if scope_ids is not None:
                sql += f' WHERE {_identifier(scope_column)} IN (' + ','.join('?' for _ in scope_ids) + ')'
            sql += f' ORDER BY {_identifier(key)}'
            rows = self.query(sql, scope_ids or ())
            if len(rows) > MAX_MASTER_ROWS:
                raise SourceProblem('SOURCE_LIMIT', 'Danh mục vượt giới hạn đọc an toàn; cần chia lô.')
            if scope_ids is not None and any(type(row.get(scope_column)) is not int
                    or row[scope_column] not in scope_ids for row in rows):
                raise SourceProblem('TEAM_SCOPE_UNRESOLVED', 'Nguồn trả bản ghi ngoài phạm vi tổ đã chọn.')
            if scope_ids is not None and resource == 'oprt.portOpTeam':
                check_selected_ids(rows, scope_ids, key=scope_column)
            self.cache[signature] = rows
        return self.cache[signature], usable, warnings


def _value(value, field, target, *, required):
    if value is None:
        if required:
            raise SourceProblem('SOURCE_DATA', f'Nguồn thiếu giá trị bắt buộc {target}.')
        return None
    types = _types(field.annotation)
    if list in types:
        if not isinstance(value, list) or any(isinstance(v, bool) or not isinstance(v, (str, int))
                                             or (isinstance(v, int) and v <= 0) or not str(v).strip() for v in value):
            raise SourceProblem('SOURCE_DATA', f'Danh sách {target} không hợp lệ.')
        return [str(v).strip() for v in value]
    if bool in types:
        if type(value) not in {int, bool} or value not in (0, 1):
            raise SourceProblem('SOURCE_DATA', f'Cờ {target} không phải 0 hoặc 1.')
        return bool(value)
    if datetime in types:
        return _timestamp(value)
    if date in types:
        parsed = _timestamp(value)
        return date.fromisoformat(parsed[:10]).isoformat()
    if target.endswith('Id') or target == 'whTypeId':
        if isinstance(value, bool):
            raise SourceProblem('SOURCE_DATA', f'ID {target} không hợp lệ.')
        if isinstance(value, (int, Decimal)):
            if value <= 0 or value != int(value):
                if not required and value == 0:
                    return None
                raise SourceProblem('SOURCE_DATA', f'ID {target} không hợp lệ.')
            return str(int(value))
        value = str(value).strip()
        if not value:
            if required:
                raise SourceProblem('SOURCE_DATA', f'Nguồn thiếu giá trị bắt buộc {target}.')
            return None
        return value
    if str in types:
        if not isinstance(value, str):
            raise SourceProblem('SOURCE_DATA', f'Giá trị {target} không phải chuỗi nguồn.')
        value = value.strip()
        if not value:
            if required:
                raise SourceProblem('SOURCE_DATA', f'Nguồn thiếu giá trị bắt buộc {target}.')
            return None
    return value


class _RowValidationProblems(SourceProblem):
    """Safe field issues and an internal identity, never the raw source record."""
    def __init__(self, issues, identity=None):
        super().__init__(issues[0]['code'], issues[0]['message'])
        self.issues, self.identity = issues, identity


def _diagnostic_identity(value):
    # Native numeric/GUID-like keys only. Do not retain free text in diagnostics.
    if isinstance(value, str) and re.fullmatch(
            r'(?:[0-9]{1,38}|[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12})', value):
        return value
    return None


def _identity_difference(left, right):
    """Classify discrepancies privately without choosing an authoritative source."""
    fields = sorted(key for key in (set(left) | set(right))
                    if not key.startswith('_') and key != 'reportDate' and left.get(key) != right.get(key))
    business = [key for key in fields if key not in {'createdDate', 'modifiedDate'}]
    if not business:
        kind = 'dates_only'
    elif all((left.get(key) is None) != (right.get(key) is None) for key in business):
        kind = 'missing_attributes'
    elif set(business) <= {'isDeleted', 'isUpdated'}:
        kind = 'lifecycle_flags'
    else:
        kind = 'business_values'
    return kind, fields


def _extract_row(raw, usable, resource, mapping, company_id, report_date):
    model = OPERATION_MODELS[resource]
    issues, invalid_fields = [], set()

    def issue(code, message, fields):
        issues.append({'code': code, 'message': message, 'fields': list(fields)})
        invalid_fields.update(fields)

    row = {'reportDate': report_date.isoformat(), 'companyId': company_id}
    for target, column in usable.items():
        if column not in raw:
            issue('SOURCE_DATA', 'Kết quả danh mục thiếu cột nguồn đã yêu cầu.', [target])
            continue
        try:
            value = raw[column]
            if value is not None and mapping['transforms'].get(target) == 'json_array':
                try:
                    value = json.loads(value)
                except (ValueError, TypeError):
                    raise SourceProblem('SOURCE_DATA', f'Danh sách JSON nguồn {target} không hợp lệ.') from None
            if target in mapping['value_maps'] and value is not None:
                values = mapping['value_maps'][target]
                lookup = str(int(value)) if isinstance(value, bool) else str(value)
                if lookup not in values:
                    raise SourceProblem('MAPPING_REQUIRED', f'Giá trị nguồn {target} chưa có trong ánh xạ đã duyệt.')
                value = values[lookup]
            row[target] = _value(value, model.model_fields[target], target,
                                 required=model.model_fields[target].is_required())
        except SourceProblem as problem:
            issue(problem.code, problem.message, [target])
        except Exception:
            issue('SOURCE_DATA', f'Không chuyển đổi được trường nguồn {target}.', [target])
    date_fields = ('createdDate', 'modifiedDate')
    if not invalid_fields.intersection(date_fields) and not any(row.get(field) for field in date_fields):
        issue('SOURCE_CHANGE_DATE_MISSING',
              'Bản ghi thiếu cả ngày tạo và ngày sửa; chưa thể lọc ngày đúng đặc tả.', date_fields)
    try:
        normalized = model.model_validate(row).model_dump(mode='json')
    except Exception as exc:
        # Pydantic error messages/input may contain names, addresses or driver values.
        # Only retain declared field names, with fixed messages.
        if isinstance(exc, ValidationError):
            for error in exc.errors(include_url=False, include_context=False, include_input=False):
                target = error['loc'][0] if error['loc'] else None
                if target in model.model_fields and target not in invalid_fields:
                    issue('SOURCE_DATA', f'Trường {target} không đáp ứng ràng buộc hợp đồng.', [target])
        else:
            issue('SOURCE_DATA', 'Dữ liệu nguồn không đáp ứng hợp đồng danh mục vận hành.', [])
    if issues:
        raise _RowValidationProblems(issues, row.get(OPERATION_IDENTITY[resource]))
    normalized['_changedDate'] = _change_day({'createTime': row.get('createdDate'), 'updateTime': row.get('modifiedDate')})
    return normalized


class _RowProblems(SourceProblem):
    """Private bounded diagnostics; only field names and safe IDs, no payloads."""
    def __init__(self, raw_count, valid_count, issue_groups, warnings):
        super().__init__(issue_groups[0]['code'], issue_groups[0]['message'])
        self.diagnostics = {'raw_row_count': raw_count, 'valid_row_count': valid_count,
            'invalid_row_count': raw_count - valid_count, 'issue_groups': issue_groups,
            'issue_count': sum(group['count'] for group in issue_groups)}
        self.warnings = warnings


def _extract(reader, resource, mapping, company_id, report_date):
    """Visit every row/field; invalid-row counts remain distinct from issue counts."""
    raw_rows, usable, warnings = reader.read(resource, mapping)
    result, seen, issue_groups = [], set(), {}
    for index, raw in enumerate(raw_rows, start=1):
        identity, issues = None, []
        try:
            normalized = _extract_row(raw, usable, resource, mapping, company_id, report_date)
            identity = normalized[OPERATION_IDENTITY[resource]]
        except _RowValidationProblems as problem:
            issues, identity = problem.issues, problem.identity
        except SourceProblem as problem:
            issues = [{'code': problem.code, 'message': problem.message, 'fields': []}]
        except Exception:
            # Driver values and validator exceptions can contain personal data.
            issues = [{'code': 'SOURCE_DATA',
                'message': 'Dữ liệu nguồn không đáp ứng hợp đồng danh mục vận hành.', 'fields': []}]
        if identity is not None:
            if identity in seen:
                issues.append({'code': 'SOURCE_DATA', 'message': 'Danh mục nguồn có ID lặp trong cùng database.',
                               'fields': [OPERATION_IDENTITY[resource]]})
            seen.add(identity)
        if not issues:
            result.append(normalized)
            continue
        for item in issues:
            key = (item['code'], item['message'], tuple(item['fields']))
            group = issue_groups.setdefault(key, {
                **item, 'count': 0, 'row_indexes': [], 'source_ids': []})
            group['count'] += 1
            if len(group['row_indexes']) < 10:
                group['row_indexes'].append(index)
            safe_id = _diagnostic_identity(identity)
            if safe_id is not None and safe_id not in group['source_ids'] and len(group['source_ids']) < 10:
                group['source_ids'].append(safe_id)
    if issue_groups:
        raise _RowProblems(len(raw_rows), len(result), list(issue_groups.values()), warnings)
    return result, warnings


def extract_operation_catalogs(query_fn, company_id='CNT', *, profile=None,
                               resources=None, report_date=None):
    """query_fn(database, SELECT_sql, parameter_tuple) -> list[dict].

    Returns per-resource rows/readiness, never a successful partial company.
    All source rows, including deleted rows, are read so receivers can sync
    tombstones. Every date filter must use createdDate OR modifiedDate.
    """
    if company_id != 'CNT':
        raise ValueError('Unsupported company')
    if profile is not None and not isinstance(profile, dict):
        raise ValueError('Invalid profile')
    if profile and not isinstance(profile.get('operation_sources', {}), dict):
        raise ValueError('Invalid operation source mappings')
    if profile and set(profile.get('operation_sources', {})) - set(RESOURCES):
        raise ValueError('Unknown operation source resource')
    date_merge_policies = metadata_policies(profile)
    completion_policies = attribute_policies(profile)
    team_scope_settings(profile)
    if resources is not None and not isinstance(resources, (list, tuple)):
        raise ValueError('Invalid operation resources')
    selected = list(RESOURCES if resources is None else resources)
    if (not selected or len(selected) > len(RESOURCES)
            or any(not isinstance(resource, str) or resource not in RESOURCES for resource in selected)
            or len(selected) != len(set(selected))):
        raise ValueError('Invalid operation resources')
    terminals = (profile or {}).get('terminals', list(SOURCES))
    if (not isinstance(terminals, list) or not terminals or len(terminals) != len(set(terminals))
            or any(terminal not in SOURCES for terminal in terminals)):
        raise ValueError('Invalid source terminals')
    report_day = datetime.now(VIETNAM_TIMEZONE).date() if report_date is None else report_date
    # A reporting day is a calendar date, not an instant whose timezone/date
    # would otherwise be silently discarded. Match the S production adapter.
    if type(report_day) is not date:
        raise ValueError('Invalid report date')
    results = {key: {'rows': [], 'ready': True, 'blockers': [], 'warnings': [],
                     'coverage': [], 'source_coverage': [], 'source_diagnostics': []} for key in selected}

    def block(key, problem, terminal=None):
        results[key]['ready'] = False
        results[key]['blockers'].append({'code': problem.code, 'message': problem.message,
                                        **({'terminal': terminal} if terminal else {})})

    mappings = {}
    for key in selected:
        try:
            mappings[key] = _mapping(key, profile)
        except SourceProblem as problem:
            block(key, problem)
    for terminal in terminals:
        reader = _Reader(query_fn, terminal)
        for key, mapping in mappings.items():
            try:
                rows, warnings = _extract(reader, key, mapping, company_id, report_day)
                results[key]['rows'].extend(rows)
                results[key]['warnings'].extend(warnings)
                results[key]['source_coverage'].append({'terminal': terminal, 'row_count': len(rows)})
                results[key]['source_diagnostics'].append({'terminal': terminal,
                    'raw_row_count': len(rows), 'valid_row_count': len(rows),
                    'invalid_row_count': 0, 'issue_groups': []})
            except _RowProblems as problem:
                results[key]['source_diagnostics'].append({'terminal': terminal, **problem.diagnostics})
                results[key]['warnings'].extend(problem.warnings)
                for issue in problem.diagnostics['issue_groups']:
                    block(key, SourceProblem(issue['code'], issue['message']), terminal)
            except SourceProblem as problem:
                block(key, problem, terminal)
                results[key]['source_diagnostics'].append({'terminal': terminal,
                    'raw_row_count': None, 'valid_row_count': None, 'invalid_row_count': None,
                    'issue_groups': [], 'source_error_code': problem.code})
    for key, result in results.items():
        unique, conflicts, date_merged, completed_ids = {}, set(), set(), set()
        differences = {}
        for row in result['rows']:
            identity = row[OPERATION_IDENTITY[key]]
            if identity in unique and unique[identity] != row:
                merged = merge_rows(key, unique[identity], row, date_merge_policies.get(key))
                if merged is not None:
                    unique[identity] = merged
                    date_merged.add(identity)
                    continue
                completion = complete_rows(key, unique[identity], row, completion_policies.get(key),
                                           date_merge_policies.get(key))
                if completion is not None:
                    unique[identity], _ = completion
                    completed_ids.add(identity)
                    if '_sourceDateVariants' in unique[identity]:
                        date_merged.add(identity)
                    continue
                conflicts.add(identity)
                kind, fields = _identity_difference(unique[identity], row)
                detail = differences.setdefault(kind, {'kind': kind, 'count': 0, 'fields': set(), 'samples': []})
                detail['count'] += 1
                detail['fields'].update(fields)
                if len(detail['samples']) < 10:
                    detail['samples'].append({'source_id': _diagnostic_identity(identity), 'fields': fields})
            unique[identity] = row
        if date_merged:
            result['metadata_merged_id_count'] = len(date_merged)
            result['metadata_merged_ids'] = [key for key in sorted(date_merged) if _diagnostic_identity(key)][:50]
        if completed_ids:
            result['attribute_completed_id_count'] = len(completed_ids)
            result['attribute_completed_ids'] = [key for key in sorted(completed_ids) if _diagnostic_identity(key)][:50]
            result['attribute_completed_fields'] = ['teu']
        if conflicts:
            result['identity_conflicts'] = [key for key in sorted(conflicts) if _diagnostic_identity(key)][:50]
            result['identity_conflict_count'] = len(conflicts)
            result['identity_differences'] = [{**item, 'fields': sorted(item['fields'])}
                                               for item in differences.values()]
            block(key, SourceProblem('SOURCE_ID_CONFLICT',
                'ID gốc trùng giữa các database nhưng nội dung khác nhau; cần quy định mã từ bên nhận.'))
        result['warnings'] = list(dict.fromkeys(result['warnings']))
        result['rows'] = list(unique.values()) if result['ready'] else []
    return results

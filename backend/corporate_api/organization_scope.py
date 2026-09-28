"""Explicit native organization scope for team and primary-membership exports.

OrganizationType is not a reliable team filter in SmartTOS. Scope is a finite
list of reviewed native IDs per database; it never selects a preferred source,
changes identity, infers employment state, or removes soft-deleted records.
"""
from .catalog_source import SOURCES, SourceProblem

MAX_TEAM_IDS = 1000
NATIVE = {
    'oprt.portOpTeam': ('Organization', 'organizationId', {
        'teamId': 'organizationId', 'teamCode': 'organizationCode', 'teamName': 'organizationName'}),
    'oprt.portOpStaff': ('Employee', 'organizationMainId', {
        'staffId': 'employeeId', 'staffCode': 'employeeCode',
        'staffName': 'employeeFullName', 'teamId': 'organizationMainId'}),
}


def selected_terminals(profile):
    """Apply the same finite source selection to extraction and publication."""
    if profile is not None and not isinstance(profile, dict):
        raise ValueError('Invalid source profile.')
    terminals = (profile or {}).get('terminals', list(SOURCES))
    if (not isinstance(terminals, list) or not terminals
            or any(not isinstance(terminal, str) or terminal not in SOURCES for terminal in terminals)
            or len(terminals) != len(set(terminals))):
        raise ValueError('Invalid source terminals.')
    return tuple(terminals)


def settings(profile):
    selected_terminals(profile)
    configured = (profile or {}).get('operation_team_scope', {})
    if not isinstance(configured, dict) or set(configured) - set(SOURCES):
        raise ValueError('Invalid operation team scope.')
    if configured and (profile or {}).get('approved') is not True:
        raise ValueError('Operation team scope requires an approved profile.')
    result = {}
    for terminal, values in configured.items():
        if (not isinstance(values, list) or not values or len(values) > MAX_TEAM_IDS
                or any(type(value) is not int or value <= 0 or value > 2147483647 for value in values)
                or len(values) != len(set(values))):
            raise ValueError('Team scope must contain unique positive native integer IDs.')
        result[terminal] = tuple(sorted(values))
    return result


def apply_scope(resource, mapping, profile):
    native = NATIVE.get(resource)
    if native is None or mapping['table'] != native[0]:
        return
    table, column, native_columns = native
    if any(mapping['columns'].get(key) != source for key, source in native_columns.items()):
        raise SourceProblem('MAPPING_REQUIRED',
            'Nguồn tổ/nhân sự phải giữ ID, mã, tên và quan hệ đơn vị chính đã xác minh.')
    if set(mapping['value_maps']) & set(native_columns) or set(mapping['transforms']) & set(native_columns):
        raise SourceProblem('MAPPING_REQUIRED', 'Không được đổi giá trị ID, mã hoặc tên tổ/nhân sự nguồn.')
    scopes = settings(profile)
    terminals = selected_terminals(profile)
    if any(terminal not in scopes for terminal in terminals):
        raise SourceProblem('TEAM_SCOPE_REQUIRED',
            'Cần danh sách ID tổ đã đối chiếu cho từng nguồn; không lọc tổ chỉ bằng loại hoặc tên.')
    mapping['team_scope'] = scopes
    mapping['scope_column'] = column


def check_selected_ids(rows, selected, key='organizationId'):
    """A missing selected organization is not a verified empty team list."""
    actual = [row.get(key) for row in rows]
    if (any(type(value) is not int for value in actual)
            or len(actual) != len(set(actual)) or set(actual) != set(selected)):
        raise SourceProblem('TEAM_SCOPE_UNRESOLVED',
            'Danh sách tổ đã chọn không khớp đầy đủ với ID đơn vị trong nguồn.')

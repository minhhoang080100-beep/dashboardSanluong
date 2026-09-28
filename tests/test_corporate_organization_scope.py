"""Finite native organization scopes; strict contracts and no employee PII reads."""
from copy import deepcopy
from datetime import date, datetime
import re

import pytest

from backend.corporate_api.organization_scope import MAX_TEAM_IDS
from backend.corporate_api.manage_exports import extract as extract_preview, publish_preview, profile_digest
from backend.corporate_api.operation_source import extract_operation_catalogs
from backend.corporate_api.reconciliation import reconciliation_report
from backend.corporate_api.store import ExportStore


TEAM, STAFF = 'oprt.portOpTeam', 'oprt.portOpStaff'
COMMON = {'rowDeleted': False, 'createTime': datetime(2026, 9, 1),
          'updateTime': None, 'nativeStatus': 1}


def organization(identity, kind=8, **values):
    return {**COMMON, 'organizationId': identity, 'organizationCode': f'ORG{identity}',
            'organizationName': f'Team {identity}', 'organizationTypeId': kind,
            'organizationParentId': 27, **values}


def employee(identity, team, **values):
    return {**COMMON, 'employeeId': identity, 'employeeCode': f'EMP{identity}',
            'employeeFullName': f'Full name {identity}', 'employeeName': f'Short {identity}',
            'organizationMainId': team, 'personalPhone': 'private-phone-must-not-read', **values}


class ScopedSource:
    def __init__(self):
        self.calls, self.describe_calls = [], []
        self.fail_database = None
        self.return_outside = False
        self.missing_main_organization = False
        self.data = {'SmartTOS': {
            'Organization': [organization(29, kind=3), organization(30),
                             organization(27, kind=1), organization(999, kind=2)],
            'Employee': [employee(1, 29), employee(2, 30), employee(3, 999), employee(4, 27)]},
            'SmartTOS_BenThuy': {
            'Organization': [organization(49, kind=3), organization(50), organization(28, kind=1)],
            'Employee': [employee(101, 49), employee(102, 50), employee(103, 28)]}}
        common = {'rowDeleted': 'bit', 'createTime': 'datetime2', 'updateTime': 'datetime2', 'nativeStatus': 'int'}
        self.schemas = {
            'Organization': {**common, 'organizationId': 'int', 'organizationCode': 'nvarchar',
                             'organizationName': 'nvarchar', 'organizationTypeId': 'int', 'organizationParentId': 'int'},
            'Employee': {**common, 'employeeId': 'int', 'employeeCode': 'nvarchar',
                         'employeeFullName': 'nvarchar', 'employeeName': 'nvarchar',
                         'organizationMainId': 'int', 'personalPhone': 'nvarchar'}}

    def describe_table(self, database, table):
        self.describe_calls.append((database, table))
        if database == self.fail_database:
            raise RuntimeError('private-schema-driver-value')
        return [{'column_name': key, 'data_type': value} for key, value in self.schemas[table].items()]

    def __call__(self, database, sql, params):
        self.calls.append((database, sql, params))
        if database == self.fail_database:
            raise RuntimeError('private-source-driver-value')
        table = re.search(r'FROM \[dbo\]\.\[(\w+)\]', sql)[1]
        column = re.search(r'WHERE \[(\w+)\] IN \((\?(?:,\?)*)\)', sql)
        assert column, 'Native roster query must have a parameterized finite scope'
        assert len(params) == column[2].count('?')
        fields = re.findall(r'\[(\w+)\]', sql.split(' FROM ')[0])
        assert 'personalPhone' not in fields and 'employeeName' not in fields
        rows = [row for row in self.data[database][table] if row[column[1]] in params]
        if self.return_outside and table == 'Employee':
            rows.append(employee(9999, 999, employeeFullName='private-outside-name'))
        if self.missing_main_organization and table == 'Organization' and len(fields) > 1:
            rows = rows[:-1]
        return [{field: row[field] for field in fields} for row in rows]


def profile(*, both=False, status=True):
    result = {'approved': True, 'terminals': ['cua_lo', 'ben_thuy'] if both else ['cua_lo'],
              'operation_team_scope': {'cua_lo': [30, 29]}, 'operation_sources': {}}
    if both:
        result['operation_team_scope']['ben_thuy'] = [49, 50]
    if status:
        result['operation_sources'] = {resource: {'columns': {'status': 'nativeStatus'}} for resource in (TEAM, STAFF)}
    return result


def extract(source, configured=None, resources=(TEAM, STAFF)):
    return extract_operation_catalogs(source, resources=resources,
        profile=configured if configured is not None else profile(), report_date=date(2026, 9, 25))


def test_native_team_and_staff_projection_uses_scope_without_type_or_department_inference():
    source = ScopedSource()
    result = extract(source)
    assert all(item['ready'] for item in result.values())
    assert {row['teamId'] for row in result[TEAM]['rows']} == {'29', '30'}
    assert {row['staffId'] for row in result[STAFF]['rows']} == {'1', '2'}
    assert {row['teamId'] for row in result[STAFF]['rows']} == {'29', '30'}
    assert result[STAFF]['rows'][0]['staffName'] == 'Full name 1'
    for _, sql, params in source.calls:
        assert params == (29, 30) and 'organizationTypeId' not in sql
        assert 'rowDeleted]=' not in sql and '*' not in sql
        assert '29' not in sql and '30' not in sql
    assert sum(sql.startswith('SELECT TOP (3) [organizationId]') for _, sql, _ in source.calls) == 1


def test_both_sources_keep_their_reviewed_scope_and_native_ids():
    source = ScopedSource()
    result = extract(source, profile(both=True))
    assert {row['teamId'] for row in result[TEAM]['rows']} == {'29', '30', '49', '50'}
    assert {row['staffId'] for row in result[STAFF]['rows']} == {'1', '2', '101', '102'}
    assert all(params == ((29, 30) if database == 'SmartTOS' else (49, 50))
               for database, _, params in source.calls)


def test_cross_source_staff_id_collision_never_chooses_a_preferred_terminal():
    source = ScopedSource()
    source.data['SmartTOS_BenThuy']['Employee'][0]['employeeId'] = 1
    result = extract(source, profile(both=True))
    assert result[TEAM]['ready']
    assert not result[STAFF]['ready'] and result[STAFF]['rows'] == []
    assert result[STAFF]['identity_conflict_count'] == 1
    assert result[STAFF]['blockers'][0]['code'] == 'SOURCE_ID_CONFLICT'


@pytest.mark.parametrize('scope', [None, [], {'unknown': [1]}, {'cua_lo': []}, {'cua_lo': [True]},
    {'cua_lo': [0]}, {'cua_lo': [-1]}, {'cua_lo': [1, 1]}, {'cua_lo': [2147483648]},
    {'cua_lo': ['29); DROP TABLE Employee;--']}, {'cua_lo': list(range(1, MAX_TEAM_IDS + 2))}])
def test_invalid_scope_rejected_before_any_source_access(scope):
    source = ScopedSource()
    configured = profile()
    configured['operation_team_scope'] = scope
    with pytest.raises(ValueError):
        extract(source, configured)
    assert not source.calls and not source.describe_calls


def test_nonempty_scope_needs_profile_approval_before_source_access():
    source = ScopedSource()
    configured = profile()
    configured['approved'] = False
    with pytest.raises(ValueError, match='approved'):
        extract(source, configured)
    assert not source.calls and not source.describe_calls


@pytest.mark.parametrize('configured', [{'approved': True, 'terminals': ['cua_lo']},
    {'approved': True, 'terminals': ['cua_lo', 'ben_thuy'], 'operation_team_scope': {'cua_lo': [29]}}])
def test_missing_scope_is_an_explicit_blocker_and_never_falls_back_to_all_rows(configured):
    source = ScopedSource()
    result = extract(source, configured)
    assert all(item['blockers'][0]['code'] == 'TEAM_SCOPE_REQUIRED' for item in result.values())
    assert not source.calls and not source.describe_calls


def test_nonexistent_scoped_team_blocks_both_resources_before_employee_query():
    source = ScopedSource()
    configured = profile()
    configured['operation_team_scope']['cua_lo'] = [29, 404]
    result = extract(source, configured)
    assert all(not item['ready'] and item['rows'] == [] for item in result.values())
    assert all(item['blockers'][0]['code'] == 'TEAM_SCOPE_UNRESOLVED' for item in result.values())
    assert not any('[Employee]' in sql for _, sql, _ in source.calls)


def test_confirmed_team_without_members_is_a_verified_empty_staff_list():
    source = ScopedSource()
    source.data['SmartTOS']['Employee'] = []
    result = extract(source)
    assert result[TEAM]['ready'] and len(result[TEAM]['rows']) == 2
    assert result[STAFF]['ready'] and result[STAFF]['rows'] == []
    assert result[STAFF]['source_diagnostics'][0]['raw_row_count'] == 0


def test_soft_deleted_records_preserved_without_inferring_business_status():
    source = ScopedSource()
    source.data['SmartTOS']['Organization'][0].update(rowDeleted=True, nativeStatus=3)
    source.data['SmartTOS']['Employee'][0].update(rowDeleted=True, nativeStatus=1)
    result = extract(source)
    assert result[TEAM]['rows'][0]['isDeleted'] == 1 and result[TEAM]['rows'][0]['status'] == 3
    assert result[STAFF]['rows'][0]['isDeleted'] == 1 and result[STAFF]['rows'][0]['status'] == 1


def test_missing_status_mapping_blocks_before_sql_even_with_valid_scope():
    source = ScopedSource()
    result = extract(source, profile(status=False))
    assert all(item['blockers'][0]['code'] == 'MAPPING_REQUIRED' for item in result.values())
    assert all('status' in item['blockers'][0]['message'] for item in result.values())
    assert not source.calls and not source.describe_calls


def test_missing_team_dates_block_delivery_even_when_staff_contract_is_complete():
    source = ScopedSource()
    source.data['SmartTOS']['Organization'][0].update(createTime=None, updateTime=None)
    configured = profile()
    datasets = extract(source, configured)
    assert not datasets[TEAM]['ready'] and datasets[STAFF]['ready']
    assert datasets[TEAM]['blockers'][0]['code'] == 'SOURCE_CHANGE_DATE_MISSING'
    report = reconciliation_report({'companyId': 'CNT', 'sourceReadAt': '2026-09-25T00:00:00+00:00',
                                    'datasets': datasets}, configured)
    assert not report['resources'][STAFF]['deliveryReady']


def test_failure_in_selected_row_blocks_whole_resource_and_never_leaks_name():
    source = ScopedSource()
    source.data['SmartTOS']['Employee'][0].update(nativeStatus=99, employeeFullName='private-invalid-name')
    result = extract(source)[STAFF]
    assert not result['ready'] and result['rows'] == []
    assert result['source_diagnostics'][0]['invalid_row_count'] == 1
    assert 'private-invalid-name' not in str(result)


def test_one_source_failure_cannot_look_like_empty_or_partial_success():
    source = ScopedSource()
    source.fail_database = 'SmartTOS_BenThuy'
    result = extract(source, profile(both=True))
    for item in result.values():
        assert not item['ready'] and item['rows'] == []
        assert item['source_diagnostics'][1]['raw_row_count'] is None
    assert 'private-' not in str(result)


def test_provider_returning_outside_scope_is_rejected_without_payload_leak():
    source = ScopedSource()
    source.return_outside = True
    result = extract(source)[STAFF]
    assert not result['ready'] and result['rows'] == []
    assert result['blockers'][0]['code'] == 'TEAM_SCOPE_UNRESOLVED'
    assert 'private-outside-name' not in str(result)


@pytest.mark.parametrize('resource,target,column', [(TEAM, 'teamId', 'organizationParentId'),
    (TEAM, 'teamName', 'organizationCode'), (STAFF, 'teamId', 'employeeId'),
    (STAFF, 'staffId', 'organizationMainId'), (STAFF, 'staffName', 'employeeName')])
def test_native_identity_relationship_and_name_overrides_cannot_bypass_scope(resource, target, column):
    source = ScopedSource()
    configured = profile()
    configured['operation_sources'][resource]['columns'][target] = column
    result = extract(source, configured, [resource])[resource]
    assert not result['ready'] and result['blockers'][0]['code'] == 'MAPPING_REQUIRED'
    assert not source.calls and not source.describe_calls


def test_team_main_query_must_still_return_all_confirmed_selected_organizations():
    source = ScopedSource()
    source.missing_main_organization = True
    result = extract(source, resources=[TEAM])[TEAM]
    assert not result['ready'] and result['rows'] == []
    assert result['blockers'][0]['code'] == 'TEAM_SCOPE_UNRESOLVED'


@pytest.mark.parametrize('resource,field,native', [(TEAM, 'teamName', 'Team 29'),
    (TEAM, 'teamCode', 'ORG29'), (STAFF, 'staffName', 'Full name 1'), (STAFF, 'staffCode', 'EMP1')])
def test_value_maps_cannot_replace_pinned_native_names_or_codes(resource, field, native):
    source = ScopedSource()
    configured = profile()
    configured['operation_sources'][resource]['value_maps'] = {field: {native: 'fabricated replacement'}}
    result = extract(source, configured, [resource])[resource]
    assert not result['ready'] and result['blockers'][0]['code'] == 'MAPPING_REQUIRED'
    assert not source.calls and not source.describe_calls


def preview(source, configured):
    return extract_preview(configured, date(2026, 9, 1), date(2026, 9, 25), [TEAM, STAFF], source)


def test_publication_of_scoped_native_roster_keeps_full_team_and_staff_fk_without_source_calls(tmp_path):
    source, configured = ScopedSource(), profile(both=True)
    draft = preview(source, configured)
    calls = deepcopy(source.calls)
    published = publish_preview(draft, configured, ExportStore(tmp_path / 'export.sqlite3'), [TEAM, STAFF])
    assert published[TEAM]['rows'] == published[STAFF]['rows'] == 4
    assert source.calls == calls


@pytest.mark.parametrize('resource', [TEAM, STAFF])
def test_publication_rejects_outside_scope_rows_atomically(tmp_path, resource):
    source, configured = ScopedSource(), profile()
    draft = preview(source, configured)
    draft['datasets'][resource]['rows'][0]['teamId'] = '999'
    store = ExportStore(tmp_path / 'export.sqlite3')
    with pytest.raises(ValueError, match='outside.*scope'):
        publish_preview(draft, configured, store, [TEAM, STAFF])
    assert store.describe() == []


def test_publication_rejects_preview_missing_one_selected_team(tmp_path):
    source, configured = ScopedSource(), profile()
    draft = preview(source, configured)
    draft['datasets'][TEAM]['rows'].pop()
    with pytest.raises(ValueError, match='every selected native team ID'):
        publish_preview(draft, configured, ExportStore(tmp_path / 'export.sqlite3'), [TEAM, STAFF])


def test_publication_checks_only_selected_terminals_when_other_scopes_are_configured(tmp_path):
    source, configured = ScopedSource(), profile(both=True)
    configured['terminals'] = ['cua_lo']
    draft = preview(source, configured)
    result = publish_preview(draft, configured, ExportStore(tmp_path / 'export.sqlite3'), [TEAM, STAFF])
    assert result[TEAM]['rows'] == result[STAFF]['rows'] == 2


def test_publication_accepts_confirmed_empty_membership_without_inventing_staff(tmp_path):
    source, configured = ScopedSource(), profile()
    source.data['SmartTOS']['Employee'] = []
    draft = preview(source, configured)
    result = publish_preview(draft, configured, ExportStore(tmp_path / 'export.sqlite3'), [TEAM, STAFF])
    assert result[TEAM]['rows'] == 2 and result[STAFF]['rows'] == 0


@pytest.mark.parametrize('resource', [TEAM, STAFF])
@pytest.mark.parametrize('empty', [False, True])
def test_publication_cannot_bypass_native_scope_by_omitting_profile_setting(tmp_path, resource, empty):
    source, configured = ScopedSource(), profile()
    draft = preview(source, configured)
    if empty:
        configured['operation_team_scope'] = {}
    else:
        del configured['operation_team_scope']
    # Simulate a locally edited preview with a matching reviewed profile digest:
    # publication must still enforce scope rather than trusting ready=true.
    draft['profileDigest'] = profile_digest(configured)
    store = ExportStore(tmp_path / 'export.sqlite3')
    with pytest.raises(ValueError) as error:
        publish_preview(draft, configured, store, [resource])
    assert error.value.code == 'TEAM_SCOPE_REQUIRED'
    assert store.describe() == []


INVALID_TERMINALS = [[], ['cua_lo', 'cua_lo'], ['unknown'], [None], [['cua_lo']],
                     'cua_lo', {'cua_lo': True}, None]


@pytest.mark.parametrize('terminals', INVALID_TERMINALS)
def test_invalid_terminal_selection_fails_before_extraction_queries(terminals):
    source, configured = ScopedSource(), profile()
    configured['terminals'] = terminals
    with pytest.raises(ValueError, match='Invalid source terminals'):
        extract(source, configured)
    assert not source.calls and not source.describe_calls


@pytest.mark.parametrize('resource', [TEAM, 'origins', 'bulkQuayVolumesCB'])
def test_extract_command_validates_terminal_selection_before_any_source(resource):
    source, configured = ScopedSource(), profile()
    configured['terminals'] = []
    with pytest.raises(ValueError, match='Invalid source terminals'):
        extract_preview(configured, date(2026, 9, 1), date(2026, 9, 25), [resource], source)
    assert not source.calls and not source.describe_calls


@pytest.mark.parametrize('terminals', INVALID_TERMINALS)
def test_invalid_terminal_selection_cannot_replace_published_roster(tmp_path, terminals):
    source, configured = ScopedSource(), profile()
    draft = preview(source, configured)
    store = ExportStore(tmp_path / 'export.sqlite3')
    publish_preview(draft, configured, store, [TEAM, STAFF])
    before = store.describe()
    configured['terminals'] = terminals
    draft['profileDigest'] = profile_digest(configured)
    if terminals == []:
        # The original bug: empty allowed-union and empty team rows agreed,
        # so a forged ready preview could erase both existing catalogs.
        for dataset in draft['datasets'].values():
            dataset['rows'] = []
    with pytest.raises(ValueError, match='Invalid source terminals'):
        publish_preview(draft, configured, store, [TEAM, STAFF])
    assert store.describe() == before
    with store.db() as db:
        assert db.execute('SELECT COUNT(*) FROM export_versions').fetchone()[0] == 2


def test_absent_terminal_setting_still_defaults_to_both_native_sources(tmp_path):
    source, configured = ScopedSource(), profile(both=True)
    del configured['terminals']
    draft = preview(source, configured)
    published = publish_preview(draft, configured, ExportStore(tmp_path / 'export.sqlite3'), [TEAM, STAFF])
    assert published[TEAM]['rows'] == published[STAFF]['rows'] == 4
    assert {database for database, _, _ in source.calls} == {'SmartTOS', 'SmartTOS_BenThuy'}


@pytest.mark.parametrize('resource,table', [(TEAM, 'Organization'), (STAFF, 'Employee')])
def test_boolean_source_status_is_not_inferred_as_working(resource, table):
    source = ScopedSource()
    source.data['SmartTOS'][table][0]['nativeStatus'] = True
    result = extract(source, resources=[resource])[resource]
    assert not result['ready'] and result['rows'] == []
    group, = result['source_diagnostics'][0]['issue_groups']
    assert group['code'] == 'SOURCE_DATA' and group['fields'] == ['status']


@pytest.mark.parametrize('resource', [TEAM, STAFF])
@pytest.mark.parametrize('status', [True, False])
def test_boolean_preview_status_rejected_atomically_without_replacing_valid_roster(tmp_path, resource, status):
    source, configured = ScopedSource(), profile()
    draft = preview(source, configured)
    store = ExportStore(tmp_path / 'export.sqlite3')
    publish_preview(draft, configured, store, [TEAM, STAFF])
    before = store.describe()
    draft['datasets'][resource]['rows'][0]['status'] = status
    with pytest.raises(ValueError, match='status'):
        publish_preview(draft, configured, store, [TEAM, STAFF])
    assert store.describe() == before
    with store.db() as db:
        assert db.execute('SELECT COUNT(*) FROM export_versions').fetchone()[0] == 2

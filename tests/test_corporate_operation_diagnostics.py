"""Private remediation diagnostics must report all issues without payload leaks."""
from datetime import date
from decimal import Decimal
import pytest

from backend.corporate_api.operation_source import extract_operation_catalogs
from test_corporate_operation_source import Query, extract, full_fixture


def test_same_row_reports_missing_code_flag_and_dates_but_counts_one_invalid_row():
    query = Query()
    row = query.data['SmartTOS']['CargoDirect'][0]
    row.update(cargoDirectCode=None, rowDeleted=4, createTime=None, updateTime=None,
               cargoDirectName='private-name-must-not-leak')
    result = extract(query)['oprt.cargoDirect']
    diagnostic = result['source_diagnostics'][0]
    assert not result['ready'] and result['rows'] == []
    assert diagnostic['invalid_row_count'] == 1
    assert diagnostic['issue_count'] == 3
    assert {tuple(x['fields']) for x in diagnostic['issue_groups']} == {
        ('cargoDirectCode',), ('isDeleted',), ('createdDate', 'modifiedDate')}
    assert all(x['source_ids'] == ['1'] for x in diagnostic['issue_groups'])
    assert 'private-name' not in str(result)


def test_multiple_contract_constraints_report_actual_fields_without_source_values():
    query, profile = full_fixture()
    resource = 'oprt.berths'
    mapping = profile['operation_sources'][resource]['columns']
    row = query.data['SmartTOS']['Berth'][0]
    row[mapping['berthSeq']] = -3
    row[mapping['berthDepth']] = Decimal('NaN')
    result = extract_operation_catalogs(query, resources=[resource], profile=profile,
                                       report_date=date(2026, 9, 25))[resource]
    diagnostic = result['source_diagnostics'][0]
    assert diagnostic['invalid_row_count'] == 1 and diagnostic['issue_count'] == 2
    assert {x['fields'][0] for x in diagnostic['issue_groups']} == {'berthSeq', 'berthDepth'}
    assert 'NaN' not in str(result)


def test_duplicate_is_found_even_when_first_record_also_has_other_errors():
    query = Query()
    row = query.data['SmartTOS']['CargoDirect'][0]
    query.data['SmartTOS']['CargoDirect'] = [dict(row, cargoDirectCode=None), dict(row)]
    result = extract(query)['oprt.cargoDirect']
    diagnostic = result['source_diagnostics'][0]
    assert diagnostic['invalid_row_count'] == 2 and diagnostic['valid_row_count'] == 0
    assert any('ID lặp' in x['message'] for x in diagnostic['issue_groups'])


def test_diagnostic_ids_and_row_samples_are_bounded():
    query = Query()
    row = query.data['SmartTOS']['CargoDirect'][0]
    query.data['SmartTOS']['CargoDirect'] = [dict(row, cargoDirectId=i, cargoDirectCode=None,
        createTime=None, updateTime=None) for i in range(1, 31)]
    diagnostic = extract(query)['oprt.cargoDirect']['source_diagnostics'][0]
    assert diagnostic['invalid_row_count'] == 30 and diagnostic['issue_count'] == 60
    assert all(x['count'] == 30 and x['source_ids'] == [str(i) for i in range(1, 11)]
               and x['row_indexes'] == list(range(1, 11)) for x in diagnostic['issue_groups'])


def test_malformed_date_is_not_mislabeled_as_absent_date():
    query = Query()
    query.data['SmartTOS']['CargoDirect'][0].update(createTime='not-a-date', updateTime=None,
                                                  cargoDirectCode=None)
    diagnostic = extract(query)['oprt.cargoDirect']['source_diagnostics'][0]
    assert diagnostic['issue_count'] == 2
    assert all(x['code'] != 'SOURCE_CHANGE_DATE_MISSING' for x in diagnostic['issue_groups'])
    assert 'not-a-date' not in str(diagnostic)


def test_missing_projection_column_reports_field_and_other_issues():
    query = Query()
    row = query.data['SmartTOS']['CargoDirect'][0]
    del row['cargoDirectCode']
    row['rowDeleted'] = 7
    diagnostic = extract(query)['oprt.cargoDirect']['source_diagnostics'][0]
    assert diagnostic['issue_count'] == 2
    assert {x['fields'][0] for x in diagnostic['issue_groups']} == {'cargoDirectCode', 'isDeleted'}


@pytest.mark.parametrize('identity', ['private free text', 'private-name', 'John.Doe'])
def test_free_text_identity_is_not_copied_to_diagnostics(identity):
    query = Query()
    query.data['SmartTOS']['CargoDirect'][0].update(cargoDirectId=identity, cargoDirectCode=None)
    result = extract(query)['oprt.cargoDirect']
    assert result['source_diagnostics'][0]['issue_groups'][0]['source_ids'] == []
    assert identity not in str(result)


def test_free_text_identity_still_detects_duplicate_without_echoing_it():
    query = Query()
    row = query.data['SmartTOS']['CargoDirect'][0]
    query.data['SmartTOS']['CargoDirect'] = [dict(row, cargoDirectId='private key', cargoDirectCode=None),
                                           dict(row, cargoDirectId='private key')]
    result = extract(query)['oprt.cargoDirect']
    diagnostic = result['source_diagnostics'][0]
    assert diagnostic['invalid_row_count'] == 2
    assert any('ID lặp' in x['message'] for x in diagnostic['issue_groups'])
    assert 'private key' not in str(result)


def test_lifecycle_only_difference_keeps_both_sources_blocked():
    query = Query()
    query.data['SmartTOS_BenThuy']['CargoDirect'][0]['rowDeleted'] = True
    result = extract(query)['oprt.cargoDirect']
    assert result['identity_differences'][0]['kind'] == 'lifecycle_flags'
    assert result['identity_differences'][0]['fields'] == ['isDeleted']
    assert result['rows'] == [] and not result['ready']


def test_identity_differences_distinguish_missing_attributes_dates_and_real_values():
    query = Query()
    base = query.data['SmartTOS']['CargoDirect'][0]
    query.data['SmartTOS']['CargoDirect'] = [dict(base, cargoDirectId=i) for i in range(1, 4)]
    query.data['SmartTOS_BenThuy']['CargoDirect'] = [
        dict(base, cargoDirectId=1, updateTime=None),
        dict(base, cargoDirectId=2, cargoDirectName=None),
        dict(base, cargoDirectId=3, cargoDirectCode='OTHER-NATIVE-CODE'),
    ]
    result = extract(query)['oprt.cargoDirect']
    assert result['identity_conflict_count'] == 3 and result['rows'] == []
    assert {x['kind']: x['fields'] for x in result['identity_differences']} == {
        'dates_only': ['modifiedDate'], 'missing_attributes': ['cargoDirectName'],
        'business_values': ['cargoDirectCode']}
    assert 'OTHER-NATIVE-CODE' not in str(result)


def test_identity_difference_samples_are_bounded():
    query = Query()
    base = query.data['SmartTOS']['CargoDirect'][0]
    query.data['SmartTOS']['CargoDirect'] = [dict(base, cargoDirectId=i) for i in range(1, 26)]
    query.data['SmartTOS_BenThuy']['CargoDirect'] = [dict(base, cargoDirectId=i, cargoDirectName=None)
                                                for i in range(1, 26)]
    result = extract(query)['oprt.cargoDirect']
    group = result['identity_differences'][0]
    assert group['count'] == 25 and len(group['samples']) == 10
    assert result['rows'] == []


def test_staff_uses_full_source_name_with_explicit_remaining_mappings():
    query, profile = full_fixture()
    mapping = profile['operation_sources']['oprt.portOpStaff']['columns']
    mapping.pop('staffName', None)
    query.schema['SmartTOS']['Staff'].update(employeeFullName='nvarchar', employeeName='nvarchar')
    query.data['SmartTOS']['Staff'][0].update(employeeFullName='Full source name', employeeName='Short name')
    result = extract(query, ['oprt.portOpStaff'], profile)['oprt.portOpStaff']
    assert result['ready'] and result['rows'][0]['staffName'] == 'Full source name'


def test_shift_assignments_cannot_become_master_teams_by_mapping_a_status():
    query, profile = full_fixture()
    profile['operation_sources']['oprt.portOpTeam']['table'] = 'Gang'
    result = extract(query, ['oprt.portOpTeam'], profile)['oprt.portOpTeam']
    assert not result['ready'] and result['rows'] == []
    assert result['blockers'][0]['code'] == 'MAPPING_REQUIRED'
    assert 'không phải danh mục đội' in result['blockers'][0]['message']
    assert not query.calls

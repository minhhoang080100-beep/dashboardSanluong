from copy import deepcopy
from datetime import date, datetime, timezone
from decimal import Decimal
import re

import pytest

from backend.corporate_api import operation_source as source
from backend.corporate_api.operation_contracts import OPERATION_MODELS, OPERATION_IDENTITY


class Query:
    def __init__(self):
        self.schema = {}
        self.data = {}
        self.calls = []
        self.fail_db = None
        self.fail_table = None
        for db in source.SOURCES.values():
            self.schema[db] = {'CargoDirect': {
                'cargoDirectId': 'int', 'cargoDirectCode': 'nvarchar', 'cargoDirectName': 'nvarchar',
                'rowDeleted': 'bit', 'createTime': 'datetime', 'updateTime': 'datetime'}}
            self.data[db] = {'CargoDirect': [{
                'cargoDirectId': 1, 'cargoDirectCode': 'X', 'cargoDirectName': 'Hàng xếp',
                'rowDeleted': False, 'createTime': datetime(2025, 1, 1), 'updateTime': datetime(2026, 9, 24)}]}

    def __call__(self, db, sql, params):
        self.calls.append((db, sql, params))
        assert sql.lstrip().startswith('SELECT')
        if db == self.fail_db:
            raise RuntimeError('secret-connection-must-not-leak')
        if 'sys.columns' in sql:
            table = params[0].split('.')[-1]
            return [{'column_name': column, 'data_type': dtype}
                    for column, dtype in self.schema[db].get(table, {}).items()]
        table = re.search(r'FROM \[dbo\]\.\[([A-Za-z_]+)\]', sql)[1]
        if table == self.fail_table:
            raise RuntimeError('secret-source-must-not-leak')
        return deepcopy(self.data[db][table])


def extract(query=None, resources=('oprt.cargoDirect',), profile=None):
    return source.extract_operation_catalogs(query or Query(), resources=resources,
        profile=profile, report_date=date(2026, 9, 25))


def test_native_values_soft_deletes_and_report_date_are_preserved():
    query = Query()
    for db in query.data:
        query.data[db]['CargoDirect'][0]['rowDeleted'] = True
    result = extract(query)['oprt.cargoDirect']
    assert result['ready']
    assert len(result['rows']) == 1
    row = result['rows'][0]
    assert row['cargoDirectId'] == '1'
    assert row['reportDate'] == '2026-09-25'
    assert row['companyId'] == 'CNT'
    assert row['isDeleted'] == 1
    assert row['isUpdated'] is None
    assert row['createdDate'].startswith('2025-01-01')
    assert row['modifiedDate'].startswith('2026-09-24')
    assert row['_changedDate'] == '20260924'
    assert all('rowDeleted],0)=0' not in sql for _, sql, _ in query.calls)
    assert all('*' not in sql for _, sql, _ in query.calls)
    assert len(result['source_coverage']) == 2


def test_conflicts_never_pick_one_terminal_or_change_native_id():
    query = Query()
    query.data['SmartTOS_BenThuy']['CargoDirect'][0]['cargoDirectName'] = 'Different'
    result = extract(query)['oprt.cargoDirect']
    assert not result['ready'] and result['rows'] == []
    assert result['identity_conflict_count'] == 1
    assert result['identity_conflicts'] == ['1']
    assert result['blockers'][0]['code'] == 'SOURCE_ID_CONFLICT'
    scoped = extract(query, profile={'terminals': ['ben_thuy']})['oprt.cargoDirect']
    assert scoped['ready'] and scoped['rows'][0]['cargoDirectId'] == '1'


def test_failed_source_never_publishes_successful_terminal_rows_or_details():
    query = Query()
    query.fail_db = 'SmartTOS_BenThuy'
    result = extract(query)['oprt.cargoDirect']
    assert not result['ready'] and result['rows'] == []
    assert result['blockers'][0]['code'] == 'SOURCE_UNAVAILABLE'
    assert 'secret-' not in str(result)


def test_schema_type_guard_and_missing_required_column():
    query = Query()
    query.schema['SmartTOS']['CargoDirect']['cargoDirectCode'] = 'int'
    result = extract(query)['oprt.cargoDirect']
    assert not result['ready']
    assert result['blockers'][0]['code'] == 'SOURCE_SCHEMA'
    del query.schema['SmartTOS']['CargoDirect']['cargoDirectId']
    assert not extract(query)['oprt.cargoDirect']['ready']


def test_malformed_metadata_fails_closed_without_uncaught_key_error():
    result = extract(lambda db, sql, params: [{'column_name': 'nativeId'}])['oprt.cargoDirect']
    assert not result['ready']
    assert result['blockers'][0]['code'] == 'SOURCE_SCHEMA'


def test_missing_optional_name_schema_returns_null_with_warning():
    query = Query()
    del query.schema['SmartTOS']['CargoDirect']['cargoDirectName']
    result = extract(query, profile={'terminals': ['cua_lo']})['oprt.cargoDirect']
    assert result['ready']
    assert result['rows'][0]['cargoDirectName'] is None
    assert result['warnings']


@pytest.mark.parametrize('field,value', [('cargoDirectId', True), ('cargoDirectId', 0),
    ('cargoDirectCode', ' '), ('cargoDirectCode', None), ('rowDeleted', 2)])
def test_invalid_source_values_fail_closed(field, value):
    query = Query()
    query.data['SmartTOS']['CargoDirect'][0][field] = value
    assert not extract(query)['oprt.cargoDirect']['ready']


def test_duplicate_native_id_in_same_database_is_source_error():
    query = Query()
    query.data['SmartTOS']['CargoDirect'] *= 2
    result = extract(query)['oprt.cargoDirect']
    assert not result['ready'] and result['blockers'][0]['code'] == 'SOURCE_DATA'


def test_missing_both_change_dates_blocks_required_date_filter():
    query = Query()
    query.data['SmartTOS']['CargoDirect'][0].update(createTime=None, updateTime=None)
    result = extract(query)['oprt.cargoDirect']
    assert not result['ready']
    assert result['blockers'][0]['code'] == 'SOURCE_CHANGE_DATE_MISSING'


def test_signed_berth_depth_and_warehouse_type_keep_source_values():
    query = Query()
    for db in query.schema:
        query.schema[db]['Berth'] = {
            'berthId': 'int', 'berthCode': 'nvarchar', 'berthDepth': 'decimal',
            'rowDeleted': 'bit', 'createTime': 'datetime'}
        query.data[db]['Berth'] = [{'berthId': 17, 'berthCode': 'C5',
            'berthDepth': Decimal('-13.000'), 'rowDeleted': False, 'createTime': datetime(2025, 1, 1)}]
        query.schema[db]['Warehouse'] = {
            'warehouseId': 'int', 'warehouseCode': 'nvarchar', 'warehouseTypeId': 'int',
            'rowDeleted': 'bit', 'createTime': 'datetime'}
        query.data[db]['Warehouse'] = [{'warehouseId': 21, 'warehouseCode': 'K1',
            'warehouseTypeId': 7, 'rowDeleted': False, 'createTime': datetime(2025, 1, 1)}]
    results = extract(query, resources=['oprt.berths', 'oprt.portWHYard', 'oprt.contwhYards'])
    assert all(result['ready'] for result in results.values())
    assert results['oprt.berths']['rows'][0]['berthDepth'] == -13.0
    assert results['oprt.portWHYard']['rows'][0]['whYardTypeId'] == '7'
    assert results['oprt.contwhYards']['rows'][0]['whYardTypeId'] == '7'


def test_all_rows_are_audited_but_mixed_valid_data_is_never_published():
    query = Query()
    base = query.data['SmartTOS']['CargoDirect'][0]
    query.data['SmartTOS']['CargoDirect'] = [
        dict(base, cargoDirectId=1),
        dict(base, cargoDirectId=2, rowDeleted=2, cargoDirectName='private-source-name'),
        dict(base, cargoDirectId=3, createTime=None, updateTime=None),
        dict(base, cargoDirectId=4),
        dict(base, cargoDirectId=5, rowDeleted=2),
    ]
    result = extract(query)['oprt.cargoDirect']
    assert not result['ready'] and result['rows'] == []
    diagnostic = next(d for d in result['source_diagnostics'] if d['terminal'] == 'cua_lo')
    assert (diagnostic['raw_row_count'], diagnostic['valid_row_count'], diagnostic['invalid_row_count']) == (5, 2, 3)
    assert [(g['code'], g['count'], g['row_indexes']) for g in diagnostic['issue_groups']] == [
        ('SOURCE_DATA', 2, [2, 5]), ('SOURCE_CHANGE_DATE_MISSING', 1, [3])]
    assert [b['code'] for b in result['blockers']] == ['SOURCE_DATA', 'SOURCE_CHANGE_DATE_MISSING']
    assert result['source_coverage'] == [{'terminal': 'ben_thuy', 'row_count': 1}]
    assert 'private-source-name' not in str(result)


def test_diagnostic_samples_are_bounded_but_counts_cover_all_invalid_rows():
    query = Query()
    base = query.data['SmartTOS']['CargoDirect'][0]
    query.data['SmartTOS']['CargoDirect'] = [dict(base, cargoDirectId=i, rowDeleted=3) for i in range(1, 26)]
    result = extract(query)['oprt.cargoDirect']
    diagnostic = result['source_diagnostics'][0]
    assert (diagnostic['raw_row_count'], diagnostic['valid_row_count'], diagnostic['invalid_row_count']) == (25, 0, 25)
    assert diagnostic['issue_groups'][0]['count'] == 25
    assert diagnostic['issue_groups'][0]['row_indexes'] == list(range(1, 11))
    assert len(result['blockers']) == 1 and result['rows'] == []


def test_diagnostics_group_source_data_by_message_as_well_as_code():
    query = Query()
    base = query.data['SmartTOS']['CargoDirect'][0]
    query.data['SmartTOS']['CargoDirect'] = [
        dict(base, cargoDirectId=1, rowDeleted=3),
        dict(base, cargoDirectId=2, cargoDirectCode=None),
        dict(base, cargoDirectId=3), dict(base, cargoDirectId=3),
    ]
    result = extract(query)['oprt.cargoDirect']
    diagnostic = result['source_diagnostics'][0]
    assert diagnostic['valid_row_count'] == 1 and diagnostic['invalid_row_count'] == 3
    assert len(diagnostic['issue_groups']) == 3
    assert {g['code'] for g in diagnostic['issue_groups']} == {'SOURCE_DATA'}
    assert len({g['message'] for g in diagnostic['issue_groups']}) == 3
    assert result['rows'] == []


def test_unexpected_row_exception_is_sanitized_and_later_rows_still_audited():
    class BrokenDriverValue:
        def __str__(self):
            raise ValueError('private-driver-secret')
    query = Query()
    base = query.data['SmartTOS']['CargoDirect'][0]
    query.data['SmartTOS']['CargoDirect'] = [
        dict(base, cargoDirectId=BrokenDriverValue()), dict(base, cargoDirectId=2),
    ]
    result = extract(query)['oprt.cargoDirect']
    diagnostic = result['source_diagnostics'][0]
    assert diagnostic['valid_row_count'] == 1 and diagnostic['invalid_row_count'] == 1
    assert diagnostic['issue_groups'][0]['code'] == 'SOURCE_DATA'
    assert 'private-driver-secret' not in str(result)
    assert result['rows'] == []


def test_unavailable_source_counts_stay_unknown_but_successful_empty_source_is_zero():
    query = Query()
    query.data['SmartTOS']['CargoDirect'] = []
    query.fail_db = 'SmartTOS_BenThuy'
    result = extract(query)['oprt.cargoDirect']
    left, right = result['source_diagnostics']
    assert (left['raw_row_count'], left['valid_row_count'], left['invalid_row_count']) == (0, 0, 0)
    assert (right['raw_row_count'], right['valid_row_count'], right['invalid_row_count']) == (None, None, None)
    assert right['source_error_code'] == 'SOURCE_UNAVAILABLE'
    assert not result['ready'] and result['rows'] == []


def test_change_day_is_vietnam_day_but_source_timestamps_kept():
    query = Query()
    query.data['SmartTOS']['CargoDirect'][0]['updateTime'] = datetime(2026, 9, 23, 23, tzinfo=timezone.utc)
    row = extract(query, profile={'terminals': ['cua_lo']})['oprt.cargoDirect']['rows'][0]
    assert row['_changedDate'] == '20260924'
    assert row['modifiedDate'] == '2026-09-23T23:00:00Z'


def test_read_cap_blocks_instead_of_returning_first_page(monkeypatch):
    monkeypatch.setattr(source, 'MAX_MASTER_ROWS', 1)
    query = Query()
    query.data['SmartTOS']['CargoDirect'] *= 2
    result = extract(query)['oprt.cargoDirect']
    assert result['blockers'][0]['code'] == 'SOURCE_LIMIT'
    assert any('TOP (2)' in sql for _, sql, _ in query.calls)


@pytest.mark.parametrize('override', [
    {'table': 'CargoDirect; DROP TABLE X'}, {'columns': {'cargoDirectName': 'name];SELECT'}},
    {'columns': {'password': 'password'}}, {'value_maps': {'cargoDirectId': {'1': '2'}}},
    {'columns': {'companyId': 'company'}}])
def test_unsafe_or_identity_changing_override_is_rejected(override):
    profile = {'approved': True, 'operation_sources': {'oprt.cargoDirect': override}}
    result = extract(profile=profile)['oprt.cargoDirect']
    assert not result['ready'] and result['rows'] == []


def test_overrides_need_approval_and_schema_evidence():
    profile = {'operation_sources': {'oprt.cargoDirect': {'columns': {'cargoDirectName': 'nativeName'}}}}
    assert extract(profile=profile)['oprt.cargoDirect']['blockers'][0]['code'] == 'MAPPING_REQUIRED'
    profile['approved'] = True
    result = extract(profile=profile)['oprt.cargoDirect']
    assert result['ready']  # An optional absent mapped column remains null.
    assert result['rows'][0]['cargoDirectName'] is None


def test_dangerous_goods_is_not_fabricated_from_missing_schema():
    query = Query()
    query.schema['SmartTOS']['Cargo'] = {'cargoId': 'int'}
    query.data['SmartTOS']['Cargo'] = []
    result = extract(query, ['oprt.cargoItems'], {'terminals': ['cua_lo']})['oprt.cargoItems']
    assert not result['ready'] and result['rows'] == []
    assert result['blockers'][0]['code'] == 'MAPPING_REQUIRED'
    assert 'dangerousGoodsCheck' in result['blockers'][0]['message']


def full_fixture():
    """Explicit fake approved mappings test contracts without assuming live fields."""
    query = Query()
    profile = {'approved': True, 'terminals': ['cua_lo'], 'operation_sources': {}}
    for resource, model in OPERATION_MODELS.items():
        table = source.DEFAULT_MAPPINGS[resource]['table']
        if resource == 'oprt.portOpTeam':
            # A verified external team master is required: Gang is transactional.
            table = 'WorkTeam'
        elif resource == 'oprt.portOpStaff':
            # This synthetic approved master is separate from the native
            # Employee -> Organization scope checked by dedicated tests.
            table = 'Staff'
        columns, row, schema = {}, {}, {}
        for target, field in model.model_fields.items():
            if target in {'reportDate', 'companyId'}:
                continue
            if (not field.is_required() and target not in source.COMMON_COLUMNS
                    and target not in source.DEFAULT_MAPPINGS[resource]['columns']):
                continue
            column = resource.split('.')[-1] + '_' + target
            columns[target] = column
            types = source._types(field.annotation)
            if target.endswith('Id'):
                value, dtype = 1, 'int'
            elif bool in types:
                value, dtype = False, 'bit'
            elif datetime in types:
                value, dtype = datetime(2026, 9, 20), 'datetime'
            elif date in types:
                value, dtype = date(2024, 2, 1), 'date'
            elif int in types:
                value, dtype = 1, 'int'
            elif Decimal in types:
                value, dtype = Decimal('2.25'), 'decimal'
            else:
                value, dtype = 'Native value', 'nvarchar'
            row[column], schema[column] = value, dtype
        query.schema['SmartTOS'].setdefault(table, {}).update(schema)
        if table not in query.data['SmartTOS']:
            query.data['SmartTOS'][table] = [row]
        else:
            query.data['SmartTOS'][table][0].update(row)
        profile['operation_sources'][resource] = {'table': table, 'columns': columns}
    return query, profile


def test_all_twenty_resources_extract_with_explicit_approved_complete_source_mappings():
    query, profile = full_fixture()
    results = extract(query, source.RESOURCES, profile)
    failed = {key: item['blockers'] for key, item in results.items() if not item['ready']}
    assert not failed, failed
    assert len(results) == 20
    assert all(len(result['rows']) == 1 for result in results.values())
    for key, result in results.items():
        row = result['rows'][0]
        assert row[OPERATION_IDENTITY[key]] == '1'
        assert set(row) == set(OPERATION_MODELS[key].model_fields) | {'_changedDate'}


def test_required_enum_needs_explicit_values_and_unknown_value_blocks():
    query, profile = full_fixture()
    mapping = profile['operation_sources']['oprt.portOpTeam']
    column = mapping['columns']['status']
    query.schema['SmartTOS']['WorkTeam'][column] = 'nvarchar'
    query.data['SmartTOS']['WorkTeam'][0][column] = 'ACTIVE'
    mapping['value_maps'] = {'status': {'ACTIVE': 1}}
    assert extract(query, ['oprt.portOpTeam'], profile)['oprt.portOpTeam']['ready']
    query.data['SmartTOS']['WorkTeam'][0][column] = 'UNKNOWN'
    result = extract(query, ['oprt.portOpTeam'], profile)['oprt.portOpTeam']
    assert not result['ready'] and result['blockers'][0]['code'] == 'MAPPING_REQUIRED'


def test_approved_json_list_transform_preserves_native_ids():
    query, profile = full_fixture()
    mapping = profile['operation_sources']['oprt.portEquipment']
    mapping['columns']['operationLocationTypeId'] = 'verifiedLocations'
    mapping['transforms'] = {'operationLocationTypeId': 'json_array'}
    query.schema['SmartTOS']['Equipment']['verifiedLocations'] = 'nvarchar'
    query.data['SmartTOS']['Equipment'][0]['verifiedLocations'] = '[1,"2"]'
    result = extract(query, ['oprt.portEquipment'], profile)['oprt.portEquipment']
    assert result['ready']
    assert result['rows'][0]['operationLocationTypeId'] == ['1', '2']
    query.data['SmartTOS']['Equipment'][0]['verifiedLocations'] = '1,2'
    assert not extract(query, ['oprt.portEquipment'], profile)['oprt.portEquipment']['ready']


def test_one_unavailable_table_does_not_block_unrelated_catalog():
    query, profile = full_fixture()
    query.fail_table = 'BaseUnit'
    result = extract(query, ['oprt.unitMeasurement', 'oprt.cargoDirect'], profile)
    assert not result['oprt.unitMeasurement']['ready']
    assert result['oprt.cargoDirect']['ready']


@pytest.mark.parametrize('kwargs', [{'company_id': 'OTHER'}, {'resources': []},
    {'resources': ['bad']}, {'profile': {'terminals': []}},
    {'profile': {'operation_sources': {'oprt.cargoGroup': {}}}},
    {'profile': {'terminals': ['cua_lo', 'cua_lo']}}])
def test_invalid_scope_rejected_before_any_read(kwargs):
    query = Query()
    with pytest.raises(ValueError):
        source.extract_operation_catalogs(query, **kwargs)
    assert not query.calls

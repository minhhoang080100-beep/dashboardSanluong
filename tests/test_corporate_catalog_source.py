from copy import deepcopy
from datetime import date, datetime, timezone
import re

import pytest

from backend.corporate_api.catalog_source import extract_catalogs, source_id


def fixture_source():
    schema = {
        'Vessel': {'vesselId': 'int', 'vesselName': 'nvarchar', 'rowDeleted': 'bit', 'isVirtualVessel': 'bit',
                   'createTime': 'datetime', 'updateTime': 'datetime', 'imoVerified': 'nvarchar'},
        'Partner': {'partnerId': 'int', 'partnerFullName': 'nvarchar', 'rowDeleted': 'bit',
                    'createTime': 'datetime', 'updateTime': 'datetime', 'carrierVerified': 'bit'},
        'Cargo': {'cargoId': 'int', 'cargoName': 'nvarchar', 'cargoCode': 'nvarchar',
                  'cargoParentId': 'int', 'rowDeleted': 'bit', 'createTime': 'datetime', 'updateTime': 'datetime'},
        'JobMethod': {'jobMethodId': 'int', 'jobMethodName': 'nvarchar', 'rowDeleted': 'bit',
                      'createTime': 'datetime', 'updateTime': 'datetime'},
        'CargoDirect': {'cargoDirectId': 'int', 'cargoDirectName': 'nvarchar', 'rowDeleted': 'bit',
                        'createTime': 'datetime', 'updateTime': 'datetime'},
        'CargoOrigin': {'cargoOriginId': 'int', 'cargoOriginName': 'nvarchar', 'rowDeleted': 'bit',
                        'createTime': 'datetime', 'updateTime': 'datetime'},
    }
    timestamp = datetime(2026, 9, 1, 12)
    data = {
        'Vessel': [{'vesselId': 10, 'vesselName': 'Source vessel', 'rowDeleted': False,
                    'isVirtualVessel': False, 'createTime': timestamp, 'updateTime': None,
                    'imoVerified': '1234567'}],
        'Partner': [{'partnerId': 20, 'partnerFullName': 'Source customer', 'rowDeleted': True,
                     'createTime': timestamp, 'updateTime': None, 'carrierVerified': True}],
        'Cargo': [{'cargoId': 30, 'cargoName': 'Bulk commodity', 'cargoCode': 'BULK',
                   'cargoParentId': 0, 'rowDeleted': None, 'createTime': timestamp, 'updateTime': None},
                  {'cargoId': 31, 'cargoName': '20F', 'cargoCode': '20F',
                   'cargoParentId': 0, 'rowDeleted': False, 'createTime': timestamp, 'updateTime': None}],
        'JobMethod': [{'jobMethodId': 40, 'jobMethodName': 'Source method', 'rowDeleted': False,
                       'createTime': timestamp, 'updateTime': None}],
        'CargoDirect': [{'cargoDirectId': 1, 'cargoDirectName': 'Loading', 'rowDeleted': None,
                         'createTime': timestamp, 'updateTime': None}],
        'CargoOrigin': [{'cargoOriginId': 1, 'cargoOriginName': 'Native origin', 'rowDeleted': None,
                         'createTime': timestamp, 'updateTime': None}],
    }
    return schema, data


class FakeQuery:
    def __init__(self):
        schema, data = fixture_source()
        self.schemas = {db: deepcopy(schema) for db in ('SmartTOS', 'SmartTOS_BenThuy')}
        self.data = {db: deepcopy(data) for db in self.schemas}
        self.calls = []
        self.fail_db = None

    def __call__(self, db, sql, params):
        self.calls.append((db, sql, params))
        assert sql.lstrip().startswith('SELECT')
        assert db in self.schemas
        if db == self.fail_db:
            raise RuntimeError('must-not-leak-password-or-server')
        if 'INFORMATION_SCHEMA' in sql:
            return [{'table_name': table, 'column_name': name, 'data_type': dtype}
                    for table, columns in self.schemas[db].items() for name, dtype in columns.items()]
        table = re.search(r'FROM \[dbo\]\.\[([A-Za-z]+)\]', sql)[1]
        return deepcopy(self.data[db][table])


def approved_profile():
    return {
        'approved': True,
        'cargo_types': {'CNT-BULK': 'Bulk approved'},
        'cargo_type_by_cargo': {'cua_lo': {'30': 'CNT-BULK'}, 'ben_thuy': {'30': 'CNT-BULK'}},
        'origins': {'CNT-DOMESTIC': 'Domestic approved'},
        'container_sizes_by_cargo': {
            'cua_lo': {'31': {'localSzTp': '20F', 'sizeCode': '20'}},
            'ben_thuy': {'31': {'localSzTp': '20F', 'sizeCode': '20'}},
        },
    }


def extract(query=None, profile=None):
    return extract_catalogs(query or FakeQuery(), profile=profile, report_date=date(2026, 9, 21))


@pytest.mark.parametrize('metadata', [
    {'table_name': 'CargoDirect'},
    {'table_name': 'CargoDirect', 'column_name': None, 'data_type': 'int'},
    {'table_name': 'CargoDirect', 'column_name': 'cargoDirectId', 'data_type': None},
])
def test_malformed_catalog_metadata_blocks_without_uncaught_exception(metadata):
    result = extract(lambda *args: [metadata], approved_profile())
    for resource in ('shipDetails', 'customers', 'cargoCategory', 'handlingMethodList',
                     'class', 'origins', 'containerSize'):
        assert not result[resource]['ready'] and result[resource]['rows'] == []
        assert result[resource]['blockers'][0]['code'] == 'SOURCE_SCHEMA'


@pytest.mark.parametrize('field', ['createTime', 'updateTime', 'imoVerified'])
def test_omitted_selected_column_is_not_silently_treated_as_source_null(field):
    query = FakeQuery()
    del query.data['SmartTOS']['Vessel'][0][field]
    config = approved_profile()
    config['source_columns'] = {'shipDetails': {'shipIMO': 'imoVerified'}}
    result = extract(query, config)
    assert not result['shipDetails']['ready'] and result['shipDetails']['rows'] == []
    assert result['shipDetails']['blockers'][0]['code'] == 'SOURCE_DATA'
    assert result['class']['ready']  # Unrelated catalogs remain available.


def test_native_catalogs_live_schema_guard_and_no_semantic_guessing():
    query = FakeQuery()
    result = extract(query)
    assert result['shipDetails']['ready'] is True
    assert [r['shipId'] for r in result['shipDetails']['rows']] == ['10']
    assert all(r['shipIMO'] is None for r in result['shipDetails']['rows'])
    assert result['customers']['rows'][0]['metadata']['isDeleted'] is True
    assert result['customers']['rows'][0]['metadata']['modifiedDate'] is None
    assert result['customers']['rows'][0]['metadata']['createdDate'] == '2026-09-01T12:00:00'
    assert result['customers']['rows'][0]['_createdDate'] == '20260901'
    assert result['customers']['rows'][0]['isCarrier'] is None
    for resource in ('cargoType', 'cargoCategory', 'containerSize'):
        assert not result[resource]['ready']
        assert result[resource]['rows'] == []
        assert result[resource]['blockers'][0]['code'] == 'MAPPING_REQUIRED'
    for db in query.schemas:
        first = next(c for c in query.calls if c[0] == db)
        assert 'INFORMATION_SCHEMA' in first[1]
    vessel_sql = next(sql for _, sql, _ in query.calls if 'FROM [dbo].[Vessel]' in sql)
    assert 'ISNULL([isVirtualVessel],0)=0' in vessel_sql
    assert '[vesselId]>0' in vessel_sql


@pytest.mark.parametrize('terminal,identity', [('wrong', 1), ('cua_lo', None), ('cua_lo', 0),
                                              ('ben_thuy', True), ('cua_lo', '1; DROP'), ('cua_lo', '01')])
def test_source_identity_rejects_invalid_and_ambiguous_values(terminal, identity):
    with pytest.raises(ValueError):
        source_id(terminal, identity)


def test_native_ids_preserved_without_prefix():
    assert source_id('cua_lo', 1) == '1'
    assert source_id('ben_thuy', 1) == '1'


def test_origins_come_from_native_table_and_ignore_invented_profile_labels():
    query = FakeQuery()
    result = extract(query, approved_profile())['origins']
    assert result['ready']
    assert len(result['rows']) == 1
    assert result['rows'][0]['originId'] == '1'
    assert result['rows'][0]['originName'] == 'Native origin'
    assert result['rows'][0]['createdDate'] == '2026-09-01T12:00:00'
    assert all('ISNULL([rowDeleted],0)=0' in sql for _, sql, _ in query.calls if 'FROM [dbo].[CargoOrigin]' in sql)


def test_origin_null_name_uses_native_code_and_latest_actual_record():
    query = FakeQuery()
    query.schemas['SmartTOS_BenThuy']['CargoOrigin']['cargoOriginCode'] = 'nvarchar'
    query.data['SmartTOS_BenThuy']['CargoOrigin'][0].update(cargoOriginName=None, cargoOriginCode='Native origin')
    query.data['SmartTOS']['CargoOrigin'][0]['updateTime'] = datetime(2026, 9, 22, 12)
    result = extract(query)['origins']
    assert result['ready'] and len(result['rows']) == 1
    assert result['rows'][0]['modifiedDate'] == '2026-09-22T12:00:00'
    assert any('cargoOriginCode' in warning for warning in result['warnings'])


def test_conflicting_origin_names_are_not_arbitrarily_chosen():
    query = FakeQuery()
    query.data['SmartTOS_BenThuy']['CargoOrigin'][0]['cargoOriginName'] = 'Different origin'
    result = extract(query)['origins']
    assert not result['ready'] and not result['rows']
    assert result['blockers'][0]['code'] == 'SOURCE_ID_CONFLICT'


def test_missing_origin_table_blocks_only_origin_catalog():
    query = FakeQuery()
    del query.schemas['SmartTOS_BenThuy']['CargoOrigin']
    result = extract(query)
    assert not result['origins']['ready'] and not result['origins']['rows']
    assert result['origins']['blockers'][0]['code'] == 'SOURCE_SCHEMA'
    assert result['shipDetails']['ready']


def native_cargo_query():
    query = FakeQuery()
    for db in query.schemas:
        query.schemas[db]['CargoGroup'] = {'cargoGroupId': 'int', 'cargoGroupName': 'nvarchar',
                                         'rowDeleted': 'bit', 'createTime': 'datetime', 'updateTime': 'datetime'}
        query.schemas[db]['Cargo']['cargoGroupId'] = 'int'
        query.data[db]['CargoGroup'] = [{'cargoGroupId': 55, 'cargoGroupName': 'Native group',
            'rowDeleted': False, 'createTime': datetime(2020, 1, 1), 'updateTime': None}]
        for row in query.data[db]['Cargo']:
            row['cargoGroupId'] = 55
    return query


def test_native_cargo_catalog_uses_all_source_rows_and_real_group_id():
    query = native_cargo_query()
    result = extract(query, {**approved_profile(), 'cargo_catalog_source': 'native_groups'})
    assert result['cargoType']['ready'] and result['cargoCategory']['ready']
    assert result['cargoType']['rows'][0]['cargoTypeId'] == '55'
    assert result['cargoType']['rows'][0]['createdDate'] == '2020-01-01T00:00:00'
    assert {r['cargoId'] for r in result['cargoCategory']['rows']} == {'30', '31'}
    assert {r['cargoTypeId'] for r in result['cargoCategory']['rows']} == {'55'}


def test_native_cargo_missing_group_cannot_be_exported_as_valid_reference():
    query = native_cargo_query()
    query.data['SmartTOS']['Cargo'][0]['cargoGroupId'] = 999
    result = extract(query, {'cargo_catalog_source': 'native_groups'})
    assert result['cargoType']['ready']
    assert not result['cargoCategory']['ready']
    assert result['cargoCategory']['rows'] == []


def test_native_cargo_parent_relationship_preserved():
    query = native_cargo_query()
    for db in query.data:
        query.data[db]['Cargo'][1]['cargoParentId'] = 30
    result = extract(query, {'cargo_catalog_source': 'native_groups'})['cargoCategory']
    assert result['ready']
    assert next(r for r in result['rows'] if r['cargoId'] == '31')['cargoParentId'] == '30'


def test_native_cargo_requires_verified_relationship_column():
    query = native_cargo_query()
    del query.schemas['SmartTOS']['Cargo']['cargoGroupId']
    result = extract(query, {'cargo_catalog_source': 'native_groups'})['cargoCategory']
    assert not result['ready']
    assert result['blockers'][0]['code'] == 'SOURCE_SCHEMA'


def test_source_schema_failure_does_not_issue_invalid_column_select():
    query = FakeQuery()
    del query.schemas['SmartTOS']['Partner']['partnerFullName']
    result = extract(query)
    assert not result['customers']['ready']
    assert result['customers']['rows'] == []
    assert result['customers']['blockers'][0]['code'] == 'SOURCE_SCHEMA'
    assert not any(db == 'SmartTOS' and 'FROM [dbo].[Partner]' in sql for db, sql, _ in query.calls)
    assert result['handlingMethodList']['ready']


def test_unavailable_terminal_never_becomes_partial_company_success():
    query = FakeQuery()
    query.fail_db = 'SmartTOS_BenThuy'
    result = extract(query)
    assert not result['shipDetails']['ready']
    assert result['shipDetails']['rows'] == []
    assert result['shipDetails']['blockers'][0]['code'] == 'SOURCE_UNAVAILABLE'
    assert 'must-not-leak' not in str(result)


def test_approved_mapping_exports_selected_cargo_without_inventing_iso():
    result = extract(profile=approved_profile())
    assert all(r['ready'] for r in result.values())
    assert [r['cargoId'] for r in result['cargoCategory']['rows']] == ['30']
    assert all(r['isoSzTp'] is None and r['containerTypeCode'] is None for r in result['containerSize']['rows'])
    assert result['containerSize']['rows'][0]['localSzTp'] == '20F'


def test_unapproved_profile_does_not_override_source_unknowns():
    profile = approved_profile()
    profile['approved'] = False
    profile['source_columns'] = {'shipDetails': {'shipIMO': 'imoVerified'}}
    result = extract(profile=profile)
    assert result['shipDetails']['rows'][0]['shipIMO'] is None
    assert not result['cargoCategory']['ready']


def test_approved_optional_source_columns_still_require_metadata_types():
    query = FakeQuery()
    profile = approved_profile()
    profile['source_columns'] = {'shipDetails': {'shipIMO': 'imoVerified'},
                                 'customers': {'isCarrier': 'carrierVerified'}}
    result = extract(query, profile)
    assert result['shipDetails']['rows'][0]['shipIMO'] == '1234567'
    assert result['customers']['rows'][0]['isCarrier'] is True
    query.schemas['SmartTOS']['Partner']['carrierVerified'] = 'nvarchar'
    result = extract(query, profile)
    assert result['customers']['blockers'][0]['code'] == 'SOURCE_SCHEMA'


@pytest.mark.parametrize('bad_col', ['name; SELECT', 'unverified_column'])
def test_bad_optional_column_mapping_is_rejected(bad_col):
    profile = approved_profile()
    profile['source_columns'] = {'shipDetails': {'shipIMO': bad_col}}
    result = extract(profile=profile)
    assert not result['shipDetails']['ready']
    assert result['shipDetails']['blockers'][0]['code'] == 'SOURCE_SCHEMA'


def test_missing_or_self_referential_parent_blocks_category_not_entire_dataset():
    query = FakeQuery()
    query.data['SmartTOS']['Cargo'][0]['cargoParentId'] = 31
    result = extract(query, approved_profile())
    assert result['cargoCategory']['blockers'][0]['code'] == 'MAPPING_REQUIRED'
    assert result['containerSize']['ready']
    query.data['SmartTOS']['Cargo'][0]['cargoParentId'] = 30
    result = extract(query, approved_profile())
    assert not result['cargoCategory']['ready']


def test_cyclic_category_hierarchy_is_rejected():
    query = FakeQuery()
    query.data['SmartTOS']['Cargo'][0]['cargoParentId'] = 31
    query.data['SmartTOS']['Cargo'][1]['cargoParentId'] = 30
    profile = approved_profile()
    profile['cargo_type_by_cargo']['cua_lo']['31'] = 'CNT-BULK'
    result = extract(query, profile)
    assert result['cargoCategory']['blockers'][0]['code'] == 'SOURCE_DATA'


def test_duplicate_source_ids_and_blank_names_cannot_pass():
    query = FakeQuery()
    query.data['SmartTOS']['JobMethod'] *= 2
    query.data['SmartTOS_BenThuy']['Vessel'][0]['vesselName'] = ' '
    result = extract(query)
    assert not result['handlingMethodList']['ready']
    assert not result['shipDetails']['ready']
    assert result['customers']['ready']


def test_nullable_missing_timestamp_stays_null_with_warning():
    query = FakeQuery()
    del query.schemas['SmartTOS']['CargoDirect']['createTime']
    del query.data['SmartTOS']['CargoDirect'][0]['createTime']
    result = extract(query, {'terminals': ['cua_lo']})
    assert result['class']['ready']
    assert result['class']['rows'][0]['createdDate'] is None
    assert any('createTime' in warning for warning in result['class']['warnings'])


def test_unsupported_company_fails_before_reading_source():
    query = FakeQuery()
    with pytest.raises(ValueError):
        extract_catalogs(query, company_id='CHP')
    assert not query.calls


def test_missing_deleted_value_cannot_be_presented_as_active():
    query = FakeQuery()
    del query.data['SmartTOS']['Partner'][0]['rowDeleted']
    result = extract(query)
    assert result['customers']['blockers'][0]['code'] == 'SOURCE_DATA'


def test_unknown_parent_schema_cannot_be_flattened_as_root_categories():
    query = FakeQuery()
    del query.schemas['SmartTOS']['Cargo']['cargoParentId']
    result = extract(query, approved_profile())
    assert result['cargoCategory']['blockers'][0]['code'] == 'SOURCE_SCHEMA'
    assert result['containerSize']['ready']


def test_customer_type_is_approved_internal_filter_only():
    query = FakeQuery()
    for db in query.schemas:
        query.schemas[db]['Partner']['verifiedType'] = 'nvarchar'
        query.data[db]['Partner'][0]['verifiedType'] = 'LOGISTICS'
    profile = approved_profile()
    profile['source_columns'] = {'customers': {'customerType': 'verifiedType'}}
    result = extract(query, profile)
    row = result['customers']['rows'][0]
    assert row['_customerType'] == 'LOGISTICS'
    assert 'customerType' not in row
    assert '_customerType' not in extract(query)['customers']['rows'][0]


def test_customer_created_day_ignores_update_and_converts_aware_time_to_vietnam_day():
    query = FakeQuery()
    query.data['SmartTOS']['Partner'][0].update(
        createTime=datetime(2026, 9, 20, 23, tzinfo=timezone.utc),
        updateTime=datetime(2026, 9, 25, 23, tzinfo=timezone.utc))
    result = extract(query, {'terminals': ['cua_lo']})
    assert result['customers']['rows'][0]['_createdDate'] == '20260921'


def test_customer_missing_created_date_remains_unknown_even_with_modified_date():
    query = FakeQuery()
    query.data['SmartTOS']['Partner'][0]['createTime'] = None
    query.data['SmartTOS']['Partner'][0]['updateTime'] = datetime(2026, 9, 25)
    result = extract(query, {'terminals': ['cua_lo']})
    assert result['customers']['ready']
    assert result['customers']['rows'][0]['_createdDate'] is None


@pytest.mark.parametrize('short_name,expected', [('Tên viết tắt', 'Tên viết tắt'), (None, None)])
def test_real_partner_full_name_falls_back_to_short_name_without_invention(short_name, expected):
    query = FakeQuery()
    query.schemas['SmartTOS']['Partner']['partnerShortName'] = 'nvarchar'
    query.data['SmartTOS']['Partner'][0]['partnerFullName'] = None
    query.data['SmartTOS']['Partner'][0]['partnerShortName'] = short_name
    result = extract(query, {'terminals': ['cua_lo']})['customers']
    assert result['ready']
    assert result['rows'][0]['customerNameVN'] == expected
    assert result['rows'][0]['customerCode'] == '20'


def test_conflicting_native_ids_from_two_databases_are_not_silently_merged():
    query = FakeQuery()
    query.data['SmartTOS_BenThuy']['Vessel'][0]['vesselName'] = 'Different vessel'
    result = extract(query)['shipDetails']
    assert not result['ready']
    assert result['rows'] == []
    assert result['blockers'][0]['code'] == 'SOURCE_ID_CONFLICT'
    query.calls.clear()
    scoped = extract(query, {'terminals': ['ben_thuy']})['shipDetails']
    assert scoped['ready']
    assert scoped['rows'][0]['shipId'] == '10'
    assert scoped['rows'][0]['shipFullName'] == 'Different vessel'
    assert all(db == 'SmartTOS_BenThuy' for db, _, _ in query.calls)

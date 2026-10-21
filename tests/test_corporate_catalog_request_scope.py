"""Request filters must not hide identity ambiguity or unrelated catalog availability."""
from copy import deepcopy
from datetime import date, datetime, timezone

import pytest

from backend.corporate_api.catalog_source import extract_catalogs
from backend.corporate_api.contracts import Query
from backend.corporate_api.errors import CorporateError
from backend.corporate_api.live import LiveReader
from backend.corporate_api.manage_exports import extract, publish_preview
from backend.corporate_api.store import ExportStore
from test_corporate_reference_scope import ScopedQuery, NATIVE_PROFILE, add_unrelated_vessel_conflict
from test_corporate_catalog_source import FakeQuery, approved_profile


def reader(source, profile=None):
    def adapter(config, start, end, resources, **options):
        return extract(config, start, end, resources, source, **options)
    return LiveReader(profile=profile or NATIVE_PROFILE, extract_fn=adapter)


def error(code, action, status=503):
    with pytest.raises(CorporateError) as caught:
        action()
    assert caught.value.code == code and caught.value.status == status
    return caught.value


def test_detail_reads_requested_id_in_both_databases_ignoring_unrelated_conflicts():
    source = ScopedQuery()
    add_unrelated_vessel_conflict(source)
    result = reader(source).read('shipDetails', Query(companyId='CNT'), identity='10')
    assert [row['shipId'] for row in result['data']] == ['10']
    calls = [(db, params) for db, sql, params in source.calls if 'FROM [dbo].[Vessel]' in sql]
    assert calls == [('SmartTOS', ('10',)), ('SmartTOS_BenThuy', ('10',))]
    assert all(not key.startswith('_') for row in result['data'] for key in row)
    error('SOURCE_ID_CONFLICT', lambda: reader(source).read('shipDetails', Query(companyId='CNT')))


def test_requested_id_conflict_remains_blocked_and_does_not_select_one_database():
    source = ScopedQuery()
    source.data['SmartTOS_BenThuy']['Vessel'][0]['vesselName'] = 'Different vessel'
    error('SOURCE_ID_CONFLICT', lambda: reader(source).read('shipDetails', Query(companyId='CNT'), identity='10'))


def test_detail_found_in_only_one_healthy_database_is_valid():
    source = ScopedQuery()
    source.data['SmartTOS_BenThuy']['Vessel'] = []
    assert reader(source).read('shipDetails', Query(companyId='CNT'), identity='10')['data'][0]['shipId'] == '10'
    assert {db for db, _, _ in source.calls} == {'SmartTOS', 'SmartTOS_BenThuy'}


@pytest.mark.parametrize('database', ['SmartTOS', 'SmartTOS_BenThuy'])
def test_detail_never_uses_partial_company_when_either_source_is_unavailable(database):
    source = ScopedQuery()
    source.fail_db = database
    error('SOURCE_UNAVAILABLE', lambda: reader(source).read('shipDetails', Query(companyId='CNT'), identity='10'))


def test_unknown_detail_id_returns_404_after_reading_both_sources():
    source = ScopedQuery()
    error('NOT_FOUND', lambda: reader(source).read('shipDetails', Query(companyId='CNT'), identity='999'), 404)
    assert [(db, params) for db, sql, params in source.calls if 'FROM [dbo].[Vessel]' in sql] == [
        ('SmartTOS', ('999',)), ('SmartTOS_BenThuy', ('999',))]


@pytest.mark.parametrize('identity', ['9223372036854775808', '9999999999999999999'])
def test_detail_beyond_sql_bigint_range_cannot_trigger_sql_overflow(identity):
    source = ScopedQuery()
    error('NOT_FOUND', lambda: reader(source).read('shipDetails', Query(companyId='CNT'), identity=identity), 404)
    assert {db for db, _, _ in source.calls} == {'SmartTOS', 'SmartTOS_BenThuy'}
    assert not any('FROM [dbo].[Vessel]' in sql for _, sql, _ in source.calls)


@pytest.mark.parametrize('identity', ['3000000000', '9223372036854775807'])
def test_fast_integer_category_does_not_truncate_real_bigint_identity(identity):
    from test_corporate_catalog_metadata import DescribedQuery
    source = DescribedQuery()
    for database in source.data:
        source.schemas[database]['Vessel']['vesselId'] = 'int'  # Driver category, not actual SQL width.
        source.data[database]['Vessel'][0]['vesselId'] = int(identity)
    result = reader(source).read('shipDetails', Query(companyId='CNT'), identity=identity)
    assert [row['shipId'] for row in result['data']] == [identity]
    assert source.descriptions == [('SmartTOS', 'Vessel'), ('SmartTOS_BenThuy', 'Vessel')]
    for _, sql, params in source.calls:
        assert params == (identity,)
        assert '[vesselId] IN (CAST(? AS BIGINT))' in sql
        assert 'CAST([vesselId]' not in sql


@pytest.mark.parametrize('column_type,identity', [('tinyint', '256'), ('smallint', '32768'), ('int', '2147483648')])
def test_unknown_detail_uses_bigint_parameter_without_casting_narrow_source_column(column_type, identity):
    class NarrowKeyQuery(ScopedQuery):
        def __call__(self, database, sql, params):
            if 'FROM [dbo].[Vessel]' in sql:
                # An untyped string comparison would convert the requested ID
                # to the narrow key type and overflow on SQL Server.
                assert '[vesselId] IN (CAST(? AS BIGINT))' in sql
                assert 'CAST([vesselId]' not in sql
            return super().__call__(database, sql, params)
    source = NarrowKeyQuery()
    for database in source.schemas:
        source.schemas[database]['Vessel']['vesselId'] = column_type
    error('NOT_FOUND', lambda: reader(source).read('shipDetails', Query(companyId='CNT'), identity=identity), 404)
    assert [(database, params) for database, sql, params in source.calls if 'FROM [dbo].[Vessel]' in sql] == [
        ('SmartTOS', (identity,)), ('SmartTOS_BenThuy', (identity,))]


@pytest.mark.parametrize('identity', ["1 OR 1=1", '0', '-1', '01', '１２', '1' * 20])
def test_invalid_native_detail_id_is_404_without_source_sql(identity):
    source = ScopedQuery()
    error('NOT_FOUND', lambda: reader(source).read('shipDetails', Query(companyId='CNT'), identity=identity), 404)
    assert source.calls == []


def customer_profile(source):
    for db in source.schemas:
        source.schemas[db]['Partner'].update(verifiedTax='nvarchar', verifiedType='nvarchar')
        source.data[db]['Partner'][0].update(verifiedTax='TAX-20', verifiedType='LOGISTICS')
    return {**NATIVE_PROFILE, 'source_columns': {'customers': {
        'customerTaxCode': 'verifiedTax', 'customerType': 'verifiedType'}}}


def add_unrelated_customer(source):
    for db in source.data:
        source.data[db]['Partner'].append({**source.data[db]['Partner'][0], 'partnerId': 21,
            'partnerFullName': db + ' unrelated customer', 'verifiedTax': 'TAX-21', 'verifiedType': 'OTHER',
            'createTime': datetime(2025, 1, 1)})


@pytest.mark.parametrize('filters', [
    {'customerTaxCode': 'TAX-20'}, {'customerType': 'LOGISTICS'},
    {'startDate': '20260901', 'endDate': '20260930'},
])
def test_customer_filters_ignore_unrelated_id_conflicts_but_keep_all_selected_id_variants(filters):
    source = ScopedQuery()
    profile = customer_profile(source)
    add_unrelated_customer(source)
    result = reader(source, profile).read('customers', Query(companyId='CNT', **filters))
    assert [row['customerCode'] for row in result['data']] == ['20']
    # Even when only one database matches the filter, the same ID's other
    # business variant must be examined before returning a public identity.
    source.data['SmartTOS_BenThuy']['Partner'][0].update(
        verifiedTax='OTHER-TAX', verifiedType='OTHER', createTime=datetime(2025, 1, 1))
    error('SOURCE_ID_CONFLICT', lambda: reader(source, profile).read('customers', Query(companyId='CNT', **filters)))


def test_customer_detail_metadata_dates_remain_strict_without_approved_merge():
    source = ScopedQuery()
    source.data['SmartTOS_BenThuy']['Partner'][0]['createTime'] = datetime(2024, 1, 1)
    error('SOURCE_ID_CONFLICT', lambda: reader(source).read('customers', Query(companyId='CNT'), identity='20'))


@pytest.mark.parametrize('filters', [{'customerTaxCode': 'TAX-20'}, {'customerType': 'LOGISTICS'}])
def test_customer_unknown_filter_mapping_never_becomes_empty_success(filters):
    source = ScopedQuery()
    error('FILTER_NOT_READY', lambda: reader(source).read('customers', Query(companyId='CNT', **filters)))


def test_customer_unknown_creation_date_cannot_be_filtered_out_silently():
    source = ScopedQuery()
    profile = customer_profile(source)
    add_unrelated_customer(source)
    source.data['SmartTOS_BenThuy']['Partner'][1]['createTime'] = None
    error('FILTER_NOT_READY', lambda: reader(source, profile).read('customers', Query(
        companyId='CNT', startDate='20260901', endDate='20260930')))


def test_container_size_filter_checks_actual_id_not_unrelated_size_conflict():
    source = ScopedQuery()
    source.data['SmartTOS_BenThuy']['vwContainerSizeTypeDomestic'][1]['containerSizeTypeDomesticCode'] = 'DIFFERENT'
    result = reader(source).read('containerSize', Query(companyId='CNT', containerSizeId='617'))
    assert [row['containerSizeId'] for row in result['data']] == ['617']
    error('SOURCE_ID_CONFLICT', lambda: reader(source).read('containerSize', Query(companyId='CNT', containerSizeId='775')))
    assert reader(source).read('containerSize', Query(companyId='CNT', containerSizeId='missing'))['data'] == []


def test_native_cargo_size_filter_preserves_public_catalog_membership_rules():
    source = ScopedQuery()
    profile = {**NATIVE_PROFILE, 'container_size_source': 'native_cargo'}
    for db in source.data:
        source.data[db]['Cargo'][1]['cargoGroupId'] = 1034
    assert reader(source, profile).read('containerSize', Query(companyId='CNT', containerSizeId='31'))['data'][0]['localSzTp'] == '20F'
    # Native ID 30 exists, but denotes a bulk commodity outside the container
    # size catalog. The list filter must return zero matches, not a broken FK.
    assert reader(source, profile).read('containerSize', Query(companyId='CNT', containerSizeId='30'))['data'] == []


def test_category_detail_checks_only_actual_group_dependency_not_unrelated_group_conflict():
    source = ScopedQuery()
    for db in source.data:
        source.data[db]['CargoGroup'].append({**source.data[db]['CargoGroup'][0],
            'cargoGroupId': 99, 'cargoGroupName': db + ' unrelated group'})
    result = reader(source).read('cargoCategory', Query(companyId='CNT'), identity='30')
    assert result['data'][0]['cargoId'] == '30'
    group_calls = [(db, params) for db, sql, params in source.calls if 'FROM [dbo].[CargoGroup]' in sql]
    assert all(params == ('1',) for _, params in group_calls)


def test_category_filter_keeps_other_group_variant_of_same_cargo_id():
    source = ScopedQuery()
    source.data['SmartTOS_BenThuy']['CargoGroup'].append({**source.data['SmartTOS_BenThuy']['CargoGroup'][0],
        'cargoGroupId': 2, 'cargoGroupName': 'Other group'})
    source.data['SmartTOS_BenThuy']['Cargo'][0]['cargoGroupId'] = 2
    error('SOURCE_ID_CONFLICT', lambda: reader(source).read('cargoCategory', Query(companyId='CNT', cargoTypeId='1')))


def test_category_parent_closure_does_not_add_ancestors_to_detail_result():
    source = ScopedQuery()
    for db in source.data:
        source.data[db]['Cargo'][0]['cargoParentId'] = 31
    result = reader(source).read('cargoCategory', Query(companyId='CNT'), identity='30')
    assert [row['cargoId'] for row in result['data']] == ['30']
    assert result['data'][0]['cargoParentId'] == '31'


def test_category_group_filter_does_not_leak_validation_ancestors_from_another_group():
    source = ScopedQuery()
    for db in source.data:
        source.data[db]['Cargo'][0]['cargoParentId'] = 31
        source.data[db]['Cargo'][1]['cargoGroupId'] = 2
        source.data[db]['CargoGroup'].append({**source.data[db]['CargoGroup'][0],
            'cargoGroupId': 2, 'cargoGroupName': 'Parent group'})
    result = reader(source).read('cargoCategory', Query(companyId='CNT', cargoTypeId='1'))
    assert [row['cargoId'] for row in result['data']] == ['30']
    assert result['pagination']['total'] == 1
    assert result['data'][0]['cargoParentId'] == '31'
    assert any(params == ('1', '2') for _, sql, params in source.calls if 'FROM [dbo].[CargoGroup]' in sql)


def test_category_reference_cannot_be_satisfied_from_other_database():
    source = ScopedQuery()
    source.data['SmartTOS']['CargoGroup'] = []
    error('SOURCE_REFERENCE_NOT_FOUND', lambda: reader(source).read('cargoCategory', Query(companyId='CNT'), identity='30'))


@pytest.mark.parametrize('needle,ids', [('nỘi', ['1']), ('ĐỊA', ['1']), ('%', ['2']), ('_', ['2']), ('missing', [])])
def test_origin_filter_uses_literal_unicode_case_insensitive_substring(needle, ids):
    source = ScopedQuery()
    for db in source.data:
        source.data[db]['CargoOrigin'][0]['cargoOriginName'] = 'Nội địa'
        source.data[db]['CargoOrigin'].append({**source.data[db]['CargoOrigin'][0],
            'cargoOriginId': 2, 'cargoOriginName': 'Ngoại_%'})
    result = reader(source).read('origins', Query(companyId='CNT', origin=needle))
    assert [row['originId'] for row in result['data']] == ids
    assert result['pagination']['total'] == len(ids)


def test_origin_filter_cannot_hide_conflicting_same_id_name():
    source = ScopedQuery()
    source.data['SmartTOS']['CargoOrigin'][0]['cargoOriginName'] = 'Nội địa'
    source.data['SmartTOS_BenThuy']['CargoOrigin'][0]['cargoOriginName'] = 'Khác'
    error('SOURCE_ID_CONFLICT', lambda: reader(source).read('origins', Query(companyId='CNT', origin='Nội')))


def test_filtered_catalog_preview_cannot_replace_persistent_complete_catalog(tmp_path):
    source = ScopedQuery()
    preview = extract(NATIVE_PROFILE, date(2026, 9, 1), date(2026, 9, 1), ['shipDetails'], source,
                      catalog_filters={'shipDetails': {'identity': '10'}})
    store = ExportStore(tmp_path / 'catalog-test.sqlite3')
    with pytest.raises(ValueError, match='persistent complete catalogs'):
        publish_preview(preview, NATIVE_PROFILE, store, ['shipDetails'], replace_all=True)
    assert store.describe() == []


def test_approved_configured_type_list_and_detail_keep_the_same_original_string_id():
    source = FakeQuery()
    profile = {**approved_profile(), 'terminals': ['cua_lo', 'ben_thuy']}
    live = reader(source, profile)
    listed = live.read('cargoType', Query(companyId='CNT'))['data']
    assert [row['cargoTypeId'] for row in listed] == ['CNT-BULK']
    assert live.read('cargoType', Query(companyId='CNT'), identity='CNT-BULK')['data'] == listed
    error('NOT_FOUND', lambda: live.read('cargoType', Query(companyId='CNT'), identity='CNT-UNKNOWN'), 404)
    assert source.calls == []  # This approved catalog has no SQL dependency.


def test_configured_category_detail_keeps_native_cargo_id_and_configured_group_reference():
    source = FakeQuery()
    profile = {**approved_profile(), 'terminals': ['cua_lo', 'ben_thuy']}
    live = reader(source, profile)
    listed = live.read('cargoCategory', Query(companyId='CNT'))['data']
    assert [row['cargoId'] for row in listed] == ['30']
    detailed = live.read('cargoCategory', Query(companyId='CNT'), identity='30')['data']
    assert detailed == listed and detailed[0]['cargoTypeId'] == 'CNT-BULK'
    filtered = live.read('cargoCategory', Query(companyId='CNT', cargoTypeId='CNT-BULK'))['data']
    assert filtered == listed
    error('NOT_FOUND', lambda: live.read('cargoCategory', Query(companyId='CNT'), identity='31'), 404)


def test_internal_catalog_filter_accepts_approved_configured_type_id_without_native_coercion():
    source = FakeQuery()
    profile = {**approved_profile(), 'terminals': ['cua_lo', 'ben_thuy']}
    result = extract_catalogs(source, profile=profile, resources=['cargoType'],
                              catalog_filters={'cargoType': {'identity': 'CNT-BULK'}})['cargoType']
    assert result['ready'] and [row['cargoTypeId'] for row in result['rows']] == ['CNT-BULK']
    assert source.calls == []

"""Actual production dependencies are bounded, source-aware catalog lookups."""
from copy import deepcopy
from datetime import datetime
import re

import pytest

from backend.corporate_api.catalog_source import extract_catalogs, _Reader
from test_corporate_catalog_source import FakeQuery


NATIVE_PROFILE = {'approved': True, 'terminals': ['cua_lo', 'ben_thuy'],
                  'cargo_catalog_source': 'native_groups',
                  'container_size_source': 'native_domestic'}


class ScopedQuery(FakeQuery):
    """Honor parameterized ID filters instead of accidentally testing full reads."""
    def __init__(self):
        super().__init__()
        for db in self.schemas:
            self.schemas[db]['Cargo']['cargoGroupId'] = 'int'
            self.schemas[db]['CargoGroup'] = {
                'cargoGroupId': 'int', 'cargoGroupName': 'nvarchar', 'rowDeleted': 'bit',
                'createTime': 'datetime', 'updateTime': 'datetime'}
            self.data[db]['CargoGroup'] = [{
                'cargoGroupId': 1, 'cargoGroupName': 'Native group', 'rowDeleted': False,
                'createTime': None, 'updateTime': None}]
            for row in self.data[db]['Cargo']:
                row['cargoGroupId'] = 1
            self.schemas[db]['vwContainerSizeTypeDomestic'] = {
                'containerSizeTypeDomesticId': 'int', 'containerSizeTypeDomesticCode': 'nvarchar',
                'containerSizeTypeId': 'int', 'containerSizeTypeCode': 'nvarchar',
                'containerSize': 'nvarchar', 'rowDeleted': 'bit', 'rowInvisible': 'bit',
                'createTime': 'datetime', 'updateTime': 'datetime'}
            self.data[db]['vwContainerSizeTypeDomestic'] = [
                {'containerSizeTypeDomesticId': identity, 'containerSizeTypeDomesticCode': code,
                 'containerSizeTypeId': 321, 'containerSizeTypeCode': '45G0',
                 'containerSize': '40', 'rowDeleted': False, 'rowInvisible': False,
                 'createTime': None, 'updateTime': None}
                for identity, code in [(617, '45G0'), (775, 'HC40')]]

    def __call__(self, db, sql, params):
        rows = super().__call__(db, sql, params)
        if 'INFORMATION_SCHEMA' in sql:
            return [row for row in rows if row['table_name'] in params]
        match = re.search(r'\[([A-Za-z]+)\] IN \((?:\?,?)+\)', sql)
        if match:
            assert sql.count('?') == len(params)
            rows = [row for row in rows if str(row[match[1]]) in params]
        else:
            assert params == ()
        if 'ISNULL([rowDeleted],0)=0' in sql:
            rows = [row for row in rows if not row['rowDeleted']]
        if 'ISNULL([rowInvisible],0)=0' in sql:
            rows = [row for row in rows if not row['rowInvisible']]
        if 'ISNULL([isVirtualVessel],0)=0' in sql:
            rows = [row for row in rows if not row['isVirtualVessel']]
        return rows


def scoped(query, resource='shipDetails', cua_lo=('10',), ben_thuy=()):
    return extract_catalogs(query, profile=NATIVE_PROFILE, resources=[resource],
        reference_scope={resource: {'cua_lo': list(cua_lo), 'ben_thuy': list(ben_thuy)}})[resource]


def add_unrelated_vessel_conflict(query):
    for db in query.data:
        row = {**query.data[db]['Vessel'][0], 'vesselId': 99, 'vesselName': db + ' different vessel'}
        query.data[db]['Vessel'].append(row)


def test_only_used_ids_are_read_in_the_actual_referencing_source():
    query = ScopedQuery()
    add_unrelated_vessel_conflict(query)
    result = scoped(query)
    assert result['ready'] and len(result['rows']) == 1
    assert result['rows'][0]['shipId'] == '10'
    assert result['rows'][0]['_sourceTerminals'] == ['cua_lo']
    calls = [(db, sql, params) for db, sql, params in query.calls if 'INFORMATION_SCHEMA' not in sql]
    assert len(calls) == 1
    assert {db for db, _, _ in query.calls} == {'SmartTOS'}
    for _, sql, params in calls:
        assert '[vesselId] IN (?)' in sql and params == ('10',)
        assert '99' not in sql and '10' not in sql.replace('100001', '')


def test_unused_other_terminal_identity_does_not_block_actual_source():
    query = ScopedQuery()
    query.data['SmartTOS_BenThuy']['Vessel'][0]['vesselName'] = 'Different actual vessel'
    result = scoped(query)
    assert result['ready'] and len(result['rows']) == 1
    assert result['rows'][0]['_sourceTerminals'] == ['cua_lo']
    assert {db for db, _, _ in query.calls} == {'SmartTOS'}


def test_native_id_used_by_both_terminals_blocks_if_business_identity_differs():
    query = ScopedQuery()
    query.data['SmartTOS_BenThuy']['Vessel'][0]['vesselName'] = 'Different actual vessel'
    result = scoped(query, ben_thuy=('10',))
    assert not result['ready'] and result['rows'] == []
    assert result['blockers'][0]['code'] == 'SOURCE_ID_CONFLICT'


@pytest.mark.parametrize('required_terminal', ['cua_lo', 'ben_thuy'])
def test_reference_must_exist_in_actual_referencing_terminal(required_terminal):
    query = ScopedQuery()
    query.data[{'cua_lo': 'SmartTOS', 'ben_thuy': 'SmartTOS_BenThuy'}[required_terminal]]['Vessel'] = []
    result = scoped(query, cua_lo=('10',) if required_terminal == 'cua_lo' else (),
                    ben_thuy=('10',) if required_terminal == 'ben_thuy' else ())
    assert not result['ready'] and result['rows'] == []
    assert result['blockers'][0]['code'] == 'SOURCE_REFERENCE_NOT_FOUND'
    assert result['blockers'][0]['terminal'] == required_terminal


def test_unused_terminal_absence_is_allowed_with_exact_private_provenance():
    query = ScopedQuery()
    query.data['SmartTOS_BenThuy']['Vessel'] = []
    result = scoped(query)
    assert result['ready'] and result['rows'][0]['_sourceTerminals'] == ['cua_lo']


def test_unused_terminal_catalog_metadata_failure_is_not_queried():
    query = ScopedQuery()
    query.fail_db = 'SmartTOS_BenThuy'
    result = scoped(query)
    assert result['ready'] and result['rows'][0]['_sourceTerminals'] == ['cua_lo']
    assert {db for db, _, _ in query.calls} == {'SmartTOS'}


def test_each_terminal_only_probes_tables_of_its_actual_dependencies():
    query = ScopedQuery()
    # Neither table belongs to that terminal's actual dependency scope.
    del query.schemas['SmartTOS']['CargoDirect']
    del query.schemas['SmartTOS_BenThuy']['Vessel']
    results = extract_catalogs(query, profile=NATIVE_PROFILE,
        resources=['shipDetails', 'class'], reference_scope={
            'shipDetails': {'cua_lo': ['10'], 'ben_thuy': []},
            'class': {'cua_lo': [], 'ben_thuy': ['1']}})
    assert all(result['ready'] for result in results.values())
    assert results['shipDetails']['rows'][0]['_sourceTerminals'] == ['cua_lo']
    assert results['class']['rows'][0]['_sourceTerminals'] == ['ben_thuy']
    metadata_calls = [(db, params) for db, sql, params in query.calls if 'INFORMATION_SCHEMA' in sql]
    assert metadata_calls == [('SmartTOS', ('Vessel',)), ('SmartTOS_BenThuy', ('CargoDirect',))]


def test_unscoped_standalone_catalog_retains_full_conflict_check():
    query = ScopedQuery()
    add_unrelated_vessel_conflict(query)
    result = extract_catalogs(query, resources=['shipDetails'])['shipDetails']
    assert not result['ready'] and result['blockers'][0]['code'] == 'SOURCE_ID_CONFLICT'
    query = ScopedQuery()
    result = extract_catalogs(query, resources=['shipDetails'])['shipDetails']
    assert result['ready'] and '_sourceTerminals' not in result['rows'][0]


@pytest.mark.parametrize('resource,table', [
    ('shipDetails', 'Vessel'), ('handlingMethodList', 'JobMethod'),
    ('class', 'CargoDirect'), ('origins', 'CargoOrigin'),
    ('cargoType', 'CargoGroup'), ('cargoCategory', 'Cargo'),
    ('containerSize', 'vwContainerSizeTypeDomestic'),
])
def test_full_catalog_without_date_filter_keeps_latest_whole_original_metadata(resource, table):
    from backend.corporate_api.contracts import Query, IDENTITY
    from backend.corporate_api.errors import CorporateError
    # This merge policy is safe only for resources with no public date filter.
    with pytest.raises(CorporateError) as caught:
        Query(companyId='CNT', startDate='20260901', endDate='20260930').check_resource(resource)
    assert caught.value.code == 'UNSUPPORTED_FILTER'
    query = ScopedQuery()
    query.data['SmartTOS_BenThuy'][table][0].update(
        createTime=datetime(2025, 1, 1), updateTime=datetime(2026, 9, 20))
    results = extract_catalogs(query, profile=NATIVE_PROFILE, resources=[resource])
    result = results[resource]
    assert result['ready']
    row = result['rows'][0]
    assert row['createdDate'] == '2025-01-01T00:00:00'
    assert row['modifiedDate'] == '2026-09-20T00:00:00'
    assert '_sourceTerminals' not in row
    assert len({value[IDENTITY[resource]] for value in result['rows']}) == len(result['rows'])


def test_full_customer_catalog_remains_strict_because_creation_date_is_filterable():
    from backend.corporate_api.contracts import Query
    Query(companyId='CNT', startDate='20260901', endDate='20260930').check_resource('customers')
    query = ScopedQuery()
    query.data['SmartTOS_BenThuy']['Partner'][0].update(
        createTime=datetime(2025, 1, 1), updateTime=datetime(2026, 9, 20))
    result = extract_catalogs(query, resources=['customers'])['customers']
    assert not result['ready'] and result['rows'] == []
    assert result['blockers'][0]['code'] == 'SOURCE_ID_CONFLICT'
    private = scoped(query, 'customers', cua_lo=('20',), ben_thuy=('20',))
    assert private['ready'] and len(private['rows']) == 1
    assert private['rows'][0]['metadata']['createdDate'] == '2025-01-01T00:00:00'
    assert private['rows'][0]['_createdDate'] == '20250101'
    assert private['rows'][0]['_sourceTerminals'] == ['ben_thuy', 'cua_lo']


@pytest.mark.parametrize('scope', [
    {}, {'class': {'cua_lo': ['10'], 'ben_thuy': []}},
    {'shipDetails': {'cua_lo': ['10']}},
    {'shipDetails': {'cua_lo': ['10'], 'ben_thuy': [], 'unknown': []}},
    {'shipDetails': {'cua_lo': ['10', '10'], 'ben_thuy': []}},
    {'shipDetails': {'cua_lo': [10], 'ben_thuy': []}},
    {'shipDetails': {'cua_lo': [True], 'ben_thuy': []}},
    {'shipDetails': {'cua_lo': ['0'], 'ben_thuy': []}},
    {'shipDetails': {'cua_lo': ['01'], 'ben_thuy': []}},
    {'shipDetails': {'cua_lo': ['10); DROP TABLE secret;--'], 'ben_thuy': []}},
    {'shipDetails': {'cua_lo': [], 'ben_thuy': []}},
    {'shipDetails': {'cua_lo': None, 'ben_thuy': []}},
    {'shipDetails': {'cua_lo': '10', 'ben_thuy': []}},
])
def test_invalid_reference_scope_rejected_before_any_sql(scope):
    query = ScopedQuery()
    with pytest.raises(ValueError) as caught:
        extract_catalogs(query, resources=['shipDetails'], reference_scope=scope)
    assert query.calls == [] and 'secret' not in str(caught.value)


def test_reference_scope_rejects_one_terminal_profile_before_sql():
    query = ScopedQuery()
    with pytest.raises(ValueError, match='both source terminals'):
        extract_catalogs(query, profile={'terminals': ['cua_lo']}, resources=['shipDetails'],
            reference_scope={'shipDetails': {'cua_lo': ['10'], 'ben_thuy': []}})
    assert query.calls == []


@pytest.mark.parametrize('resource', ['cargoType', 'cargoCategory', 'containerSize'])
def test_scoped_lookup_cannot_replace_native_identity_with_configured_catalog(resource):
    query = ScopedQuery()
    with pytest.raises(ValueError, match='Native'):
        extract_catalogs(query, resources=[resource],
            reference_scope={resource: {'cua_lo': ['1'], 'ben_thuy': []}})
    assert query.calls == []


def test_large_reference_scope_uses_bounded_parameter_chunks():
    query = ScopedQuery()
    ids = [str(value) for value in range(1, 502)]
    for db in query.data:
        original = query.data[db]['Vessel'][0]
        query.data[db]['Vessel'] = [{**original, 'vesselId': int(value)} for value in ids]
    result = scoped(query, cua_lo=ids)
    assert result['ready'] and len(result['rows']) == 501
    calls = [params for _, sql, params in query.calls if 'INFORMATION_SCHEMA' not in sql]
    assert [len(params) for params in calls] == [500, 1]
    assert all(len(params) <= 500 for params in calls)


def test_request_local_reader_cache_keeps_different_reference_scopes_separate():
    query = ScopedQuery()
    add_unrelated_vessel_conflict(query)
    reader = _Reader(query, 'cua_lo', tables=['Vessel'])
    first, _ = reader.read('Vessel', 'vesselId', 'vesselName', reference_ids=['10'])
    second, _ = reader.read('Vessel', 'vesselId', 'vesselName', reference_ids=['99'])
    assert [row['vesselId'] for row in first] == [10]
    assert [row['vesselId'] for row in second] == [99]
    assert len(query.calls) == 3


def test_category_batch_filters_cargos_and_only_their_native_groups():
    query = ScopedQuery()
    for db in query.data:
        query.data[db]['Cargo'][1]['cargoParentId'] = 30
        query.data[db]['Cargo'].append({**query.data[db]['Cargo'][0], 'cargoId': 99,
                                      'cargoName': None, 'cargoGroupId': 999})
        query.data[db]['CargoGroup'].append({**query.data[db]['CargoGroup'][0],
                                            'cargoGroupId': 99, 'cargoGroupName': None})
    result = scoped(query, 'cargoCategory', cua_lo=('31',))
    assert result['ready'] and len(result['rows']) == 1
    assert result['rows'][0]['cargoId'] == '31'
    assert result['rows'][0]['cargoParentId'] == '30'
    assert result['rows'][0]['cargoTypeId'] == '1'
    assert result['rows'][0]['_sourceTerminals'] == ['cua_lo']
    queries = [(sql, params) for _, sql, params in query.calls if 'INFORMATION_SCHEMA' not in sql]
    assert [params for sql, params in queries if 'FROM [dbo].[Cargo]' in sql] == [('31',)]
    assert [params for sql, params in queries if 'FROM [dbo].[CargoGroup]' in sql] == [('1',)]
    closed = scoped(query, 'cargoCategory', cua_lo=('31', '30'))
    assert closed['ready'] and {row['cargoId'] for row in closed['rows']} == {'30', '31'}


def test_category_group_must_exist_in_same_source():
    query = ScopedQuery()
    query.data['SmartTOS']['CargoGroup'] = []
    result = scoped(query, 'cargoCategory', cua_lo=('31',))
    assert not result['ready'] and result['rows'] == []
    assert result['blockers'][0]['code'] == 'SOURCE_REFERENCE_NOT_FOUND'


@pytest.mark.parametrize('parent', [31, -2, True])
def test_category_invalid_or_self_parent_fails_closed(parent):
    query = ScopedQuery()
    query.data['SmartTOS']['Cargo'][1]['cargoParentId'] = parent
    result = scoped(query, 'cargoCategory', cua_lo=('31',))
    assert not result['ready'] and result['rows'] == []
    assert result['blockers'][0]['code'] == 'SOURCE_DATA'


def test_visible_category_cycle_is_rejected():
    query = ScopedQuery()
    query.data['SmartTOS']['Cargo'][0]['cargoParentId'] = 31
    query.data['SmartTOS']['Cargo'][1]['cargoParentId'] = 30
    result = scoped(query, 'cargoCategory', cua_lo=('30', '31'))
    assert not result['ready'] and result['blockers'][0]['code'] == 'SOURCE_DATA'


def test_native_size_view_uses_scope_and_keeps_domestic_identity():
    query = ScopedQuery()
    query.data['SmartTOS_BenThuy']['vwContainerSizeTypeDomestic'][1]['containerSizeTypeDomesticCode'] = 'DIFFERENT'
    result = scoped(query, 'containerSize', cua_lo=('617',))
    assert result['ready'] and [row['containerSizeId'] for row in result['rows']] == ['617']
    assert result['rows'][0]['isoSzTp'] == '45G0'
    calls = [(sql, params) for _, sql, params in query.calls if 'INFORMATION_SCHEMA' not in sql]
    assert all('[containerSizeTypeDomesticId] IN (?)' in sql and params == ('617',) for sql, params in calls)


def test_used_native_size_collision_remains_blocking():
    query = ScopedQuery()
    query.data['SmartTOS_BenThuy']['vwContainerSizeTypeDomestic'][0]['containerSizeTypeDomesticCode'] = 'DIFFERENT'
    result = scoped(query, 'containerSize', cua_lo=('617',), ben_thuy=('617',))
    assert not result['ready'] and result['blockers'][0]['code'] == 'SOURCE_ID_CONFLICT'


def test_origin_existing_latest_whole_row_policy_keeps_provenance():
    query = ScopedQuery()
    query.data['SmartTOS_BenThuy']['CargoOrigin'][0]['updateTime'] = datetime(2026, 9, 2)
    result = scoped(query, 'origins', cua_lo=('1',), ben_thuy=('1',))
    assert result['ready'] and len(result['rows']) == 1
    assert result['rows'][0]['modifiedDate'] == '2026-09-02T00:00:00'
    assert result['rows'][0]['_sourceTerminals'] == ['ben_thuy', 'cua_lo']
    query.data['SmartTOS_BenThuy']['CargoOrigin'][0]['cargoOriginName'] = 'Different origin'
    result = scoped(query, 'origins', cua_lo=('1',), ben_thuy=('1',))
    assert not result['ready'] and result['blockers'][0]['code'] == 'SOURCE_ID_CONFLICT'


def test_duplicate_used_source_identity_is_not_merged_silently():
    query = ScopedQuery()
    query.data['SmartTOS']['Vessel'].append(deepcopy(query.data['SmartTOS']['Vessel'][0]))
    result = scoped(query)
    assert not result['ready'] and result['rows'] == []
    assert result['blockers'][0]['code'] == 'SOURCE_DATA'


def test_one_source_failure_never_returns_partial_reference_catalog_or_error_details():
    query = ScopedQuery()
    query.fail_db = 'SmartTOS_BenThuy'
    result = scoped(query, ben_thuy=('10',))
    assert not result['ready'] and result['rows'] == []
    assert result['blockers'][0]['code'] == 'SOURCE_UNAVAILABLE'
    assert 'must-not-leak' not in str(result)


def test_scoped_customer_still_includes_deleted_native_reference():
    result = scoped(ScopedQuery(), 'customers', cua_lo=('20',))
    assert result['ready'] and result['rows'][0]['customerCode'] == '20'
    assert result['rows'][0]['metadata']['isDeleted'] is True
    assert result['rows'][0]['_sourceTerminals'] == ['cua_lo']


class ProductionAndCatalogQuery(ScopedQuery):
    """Real production/catalog adapters over one deterministic SQL boundary."""
    include_ben_thuy_facts = False

    def __call__(self, database, sql, params):
        from backend.corporate_api.source import SCHEMA_SQL, REQUIRED_COLUMNS
        from test_corporate_source import fact, berth_rows_for
        from decimal import Decimal
        if sql == SCHEMA_SQL:
            self.calls.append((database, sql, params))
            return [{'table_name': table, 'column_name': column}
                    for table, columns in REQUIRED_COLUMNS.items() for column in columns]
        if ('t.tallyShiftId AS source_id' in sql
                or 'berth_scope.vesselVoyageId AS voyage_id' in sql):
            self.calls.append((database, sql, params))
            rows = [fact(cargo_id=30, cargo_name='Bulk commodity', cargo_group_id=1,
                         method_id=40, ship_id=10, native_weight=Decimal('12.75'),
                         quantity_unit_code='TAN')] if database == 'SmartTOS' or self.include_ben_thuy_facts else []
            return berth_rows_for(rows) if 'berth_scope.vesselVoyageId AS voyage_id' in sql else rows
        return super().__call__(database, sql, params)


def integrated_reader(query):
    from backend.corporate_api.live import LiveReader
    from backend.corporate_api.manage_exports import extract
    from test_corporate_end_to_end import full_profile
    profile = {**full_profile(), **NATIVE_PROFILE}
    def adapter(profile, start, end, resources, **kwargs):
        return extract(profile, start, end, resources, query, **kwargs)
    return LiveReader(profile=profile, extract_fn=adapter)


def production_query():
    from backend.corporate_api.contracts import Query
    return Query(companyId='CNT', startDate='20260916', endDate='20260916')


def test_real_extract_to_live_reader_ignores_unrelated_catalog_collision():
    query = ProductionAndCatalogQuery()
    add_unrelated_vessel_conflict(query)
    reader = integrated_reader(query)
    result = reader.read('bulkQuayVolumesCB', production_query())
    assert result['code'] == '1' and len(result['data']) == 1
    assert result['data'][0]['shipId'] == '10'
    assert result['data'][0]['bulkWeight'] == 12.75
    assert not any(key.startswith('_') for row in result['data'] for key in row)
    assert 'snapshotId' not in result['pagination']
    used_ship_calls = [(sql, params) for _, sql, params in query.calls if 'FROM [dbo].[Vessel]' in sql]
    assert len(used_ship_calls) == 1 and all(params == ('10',) for _, params in used_ship_calls)


@pytest.mark.parametrize('problem,code', [
    ('used_id_collision', 'SOURCE_ID_CONFLICT'),
    ('missing_actual_source', 'SOURCE_REFERENCE_NOT_FOUND'),
])
def test_real_extract_to_live_reader_keeps_used_reference_failures_blocking(problem, code):
    from backend.corporate_api.errors import CorporateError
    query = ProductionAndCatalogQuery()
    if problem == 'used_id_collision':
        query.include_ben_thuy_facts = True
        query.data['SmartTOS_BenThuy']['Vessel'][0]['vesselName'] = 'Different used identity'
    else:
        query.data['SmartTOS']['Vessel'] = []
    with pytest.raises(CorporateError) as caught:
        integrated_reader(query).read('bulkQuayVolumesCB', production_query())
    assert caught.value.code == code


def test_real_extract_to_live_reader_resolves_category_ancestors_without_unrelated_rows():
    query = ProductionAndCatalogQuery()
    for db in query.data:
        query.data[db]['Cargo'][0]['cargoParentId'] = 31
        query.data[db]['Cargo'].append({**query.data[db]['Cargo'][0], 'cargoId': 99,
                                      'cargoParentId': 0, 'cargoName': db + ' unused conflict'})
    result = integrated_reader(query).read('bulkQuayVolumesCB', production_query())
    assert len(result['data']) == 1
    used_cargo_calls = [params for _, sql, params in query.calls if 'FROM [dbo].[Cargo]' in sql]
    assert used_cargo_calls == [('30',), ('30', '31')]


@pytest.mark.parametrize('source_code,expected', [
    ('SOURCE_TIMEOUT', 'SOURCE_TIMEOUT'), ('SOURCE_UNAVAILABLE', 'SOURCE_UNAVAILABLE'),
    ('SOURCE_SCHEMA', 'SOURCE_SCHEMA'), ('SOURCE_ROW_LIMIT', 'SOURCE_ROW_LIMIT'),
    ('UNTRUSTED_SOURCE_CODE', 'SOURCE_UNAVAILABLE'),
])
def test_catalog_known_source_error_category_is_preserved_without_driver_details(source_code, expected):
    from backend.corporate_api.errors import CorporateError
    def fail(*args):
        raise CorporateError(503, source_code, 'secret-server-and-password')
    result = scoped(fail)
    assert not result['ready'] and result['rows'] == []
    assert {row['code'] for row in result['blockers']} == {expected}
    assert 'secret-server' not in str(result)

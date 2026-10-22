"""Opt-in terminal identities preserve both native catalogs without source reads.

All query functions below are synthetic SQL adapters. Raw native IDs remain the
only values bound to source SQL; public prefixes select a terminal explicitly.
"""
from copy import deepcopy

import pytest

from backend.corporate_api.catalog_source import extract_catalogs
from backend.corporate_api.contracts import IDENTITY
from test_corporate_native_cargo_sizes import CargoQuery
from test_corporate_reference_scope import ScopedQuery, NATIVE_PROFILE


PROFILE = {**NATIVE_PROFILE, 'source_identity_mode': 'terminal_prefixed_v1'}
CASES = [
    ('shipDetails', '10'), ('customers', '20'), ('cargoType', '1'),
    ('cargoCategory', '30'), ('handlingMethodList', '40'), ('class', '1'),
    ('origins', '1'), ('containerSize', '617'),
]


@pytest.fixture(autouse=True)
def no_real_sql(monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail('Real SQL is forbidden in terminal-identity tests.')
    monkeypatch.setattr('pyodbc.connect', forbidden)


@pytest.mark.parametrize('resource,native_id', CASES)
def test_all_s_catalogs_preserve_same_numeric_id_separately_in_each_terminal(resource, native_id):
    query = ScopedQuery()
    before = deepcopy(query.data)
    result = extract_catalogs(query, resources=[resource], profile=PROFILE)[resource]
    assert result['ready'], result['blockers']
    by_id = {row[IDENTITY[resource]]: row for row in result['rows']}
    assert 'CL:' + native_id in by_id and 'BT:' + native_id in by_id
    for prefix, terminal in [('CL', 'cua_lo'), ('BT', 'ben_thuy')]:
        row = by_id[prefix + ':' + native_id]
        assert row['_sourceTerminals'] == [terminal]
        assert ':' not in row['reportDate']
    assert query.data == before
    assert {database for database, _, _ in query.calls} == {'SmartTOS', 'SmartTOS_BenThuy'}


def test_real_business_and_lifecycle_differences_are_kept_not_arbitrarily_selected():
    query = ScopedQuery()
    query.data['SmartTOS']['Partner'][0]['rowDeleted'] = False
    query.data['SmartTOS_BenThuy']['Partner'][0]['rowDeleted'] = True
    query.data['SmartTOS_BenThuy']['JobMethod'][0]['jobMethodName'] = 'Different synthetic operation'
    result = extract_catalogs(query, resources=['customers', 'handlingMethodList'], profile=PROFILE)
    assert all(value['ready'] for value in result.values())
    customers = {row['customerCode']: row for row in result['customers']['rows']}
    assert customers['CL:20']['metadata']['isDeleted'] == 0
    assert customers['BT:20']['metadata']['isDeleted'] == 1
    methods = {row['handlingMethodId']: row for row in result['handlingMethodList']['rows']}
    assert methods['CL:40']['handlingMethodName'] == 'Source method'
    assert methods['BT:40']['handlingMethodName'] == 'Different synthetic operation'
    # The absent policy retains the old strict contract.
    native = extract_catalogs(query, resources=['customers', 'handlingMethodList'], profile=NATIVE_PROFILE)
    assert all(not value['ready'] and value['rows'] == [] for value in native.values())
    assert all(value['blockers'][0]['code'] == 'SOURCE_ID_CONFLICT' for value in native.values())


def test_cargo_category_type_and_parent_references_keep_their_own_source():
    query = ScopedQuery()
    for database in query.data:
        query.data[database]['Cargo'][0]['cargoParentId'] = 31
    result = extract_catalogs(query, resources=['cargoCategory', 'cargoType'], profile=PROFILE)
    assert all(value['ready'] for value in result.values())
    rows = {row['cargoId']: row for row in result['cargoCategory']['rows']}
    for prefix in ('CL', 'BT'):
        assert rows[prefix + ':30']['cargoParentId'] == prefix + ':31'
        assert rows[prefix + ':30']['cargoTypeId'] == prefix + ':1'
        assert rows[prefix + ':31']['cargoParentId'] is None
        assert rows[prefix + ':30']['cargoName'] == 'Bulk commodity'


def test_native_reporting_container_codes_remain_values_not_namespaced_identifiers():
    query = CargoQuery()
    profile = {**PROFILE, 'container_size_source': 'native_cargo'}
    result = extract_catalogs(query, resources=['containerSize'], profile=profile)['containerSize']
    assert result['ready'], result['blockers']
    assert {row['containerSizeId'] for row in result['rows']} == {
        prefix + ':' + str(native_id) for prefix in ('CL', 'BT') for native_id in range(2125, 2133)}
    for row in result['rows']:
        assert ':' not in row['localSzTp'] and row['sizeCode'] in ('20', '40', '45')
        assert row['isoSzTp'] is None and row['containerTypeCode'] is None


@pytest.mark.parametrize('native_id,expected', [('31', ['BT:31']), ('30', [])])
def test_prefixed_native_size_filter_preserves_catalog_membership(native_id, expected):
    query = ScopedQuery()
    query.fail_db = 'SmartTOS'
    query.data['SmartTOS_BenThuy']['Cargo'][1]['cargoGroupId'] = 1034
    result = extract_catalogs(query, resources=['containerSize'],
        profile={**PROFILE, 'container_size_source': 'native_cargo'},
        catalog_filters={'containerSize': {'containerSizeId': 'BT:' + native_id}})['containerSize']
    assert result['ready'], result['blockers']
    assert [row['containerSizeId'] for row in result['rows']] == expected
    assert {database for database, _, _ in query.calls} == {'SmartTOS_BenThuy'}
    assert any(params == (native_id,) for _, _, params in query.calls)


@pytest.mark.parametrize('resource,native_id', CASES)
@pytest.mark.parametrize('prefix,terminal,database', [('CL', 'cua_lo', 'SmartTOS'), ('BT', 'ben_thuy', 'SmartTOS_BenThuy')])
def test_public_id_decodes_before_sql_and_never_reads_the_other_terminal(resource, native_id, prefix, terminal, database):
    query = ScopedQuery()
    query.fail_db = 'SmartTOS_BenThuy' if database == 'SmartTOS' else 'SmartTOS'
    field = 'containerSizeId' if resource == 'containerSize' else 'identity'
    result = extract_catalogs(query, resources=[resource], profile=PROFILE,
        catalog_filters={resource: {field: prefix + ':' + native_id}})[resource]
    assert result['ready'], result['blockers']
    assert [row[IDENTITY[resource]] for row in result['rows']] == [prefix + ':' + native_id]
    assert {db for db, _, _ in query.calls} == {database}
    source_calls = [(sql, params) for _, sql, params in query.calls if 'INFORMATION_SCHEMA' not in sql]
    assert source_calls and all(all(':' not in str(value) for value in params) for _, params in source_calls)
    assert any(params == (native_id,) for _, params in source_calls)
    assert result['rows'][0]['_sourceTerminals'] == [terminal]


def test_category_type_filter_selects_its_source_and_preserves_public_fk():
    query = ScopedQuery()
    query.fail_db = 'SmartTOS'
    result = extract_catalogs(query, resources=['cargoCategory'], profile=PROFILE,
        catalog_filters={'cargoCategory': {'cargoTypeId': 'BT:1'}})['cargoCategory']
    assert result['ready'], result['blockers']
    assert {row['cargoId'] for row in result['rows']} == {'BT:30', 'BT:31'}
    assert all(row['cargoTypeId'] == 'BT:1' for row in result['rows'])
    assert {database for database, _, _ in query.calls} == {'SmartTOS_BenThuy'}


def test_mutually_exclusive_source_filters_have_no_matches_and_do_not_read_sql():
    query = ScopedQuery()
    result = extract_catalogs(query, resources=['cargoCategory'], profile=PROFILE,
        catalog_filters={'cargoCategory': {'identity': 'CL:30', 'cargoTypeId': 'BT:1'}})['cargoCategory']
    assert result['ready'] and result['rows'] == []
    assert query.calls == []


@pytest.mark.parametrize('resource,field', [('shipDetails', 'identity'), ('containerSize', 'containerSizeId'), ('cargoCategory', 'cargoTypeId')])
@pytest.mark.parametrize('invalid', ['10', 'cl:10', 'OTHER:10', 'CL:01', 'BT:0', 'CL:10 OR 1=1'])
def test_invalid_prefixed_identity_fails_before_schema_or_source_reads(resource, field, invalid):
    query = ScopedQuery()
    with pytest.raises(ValueError):
        extract_catalogs(query, resources=[resource], profile=PROFILE,
            catalog_filters={resource: {field: invalid}})
    assert query.calls == []


def test_private_reference_scope_stays_native_and_returns_prefixed_source_rows():
    query = ScopedQuery()
    result = extract_catalogs(query, resources=['cargoCategory'], profile=PROFILE,
        reference_scope={'cargoCategory': {'cua_lo': ['30'], 'ben_thuy': ['30']}})['cargoCategory']
    assert result['ready'], result['blockers']
    assert {row['cargoId'] for row in result['rows']} == {'CL:30', 'BT:30'}
    assert all(row['cargoTypeId'] in {'CL:1', 'BT:1'} for row in result['rows'])
    assert all(':' not in str(value) for _, sql, params in query.calls
        if 'INFORMATION_SCHEMA' not in sql for value in params)


def test_missing_private_reference_cannot_be_satisfied_by_the_other_source():
    query = ScopedQuery()
    query.data['SmartTOS']['Cargo'] = []
    result = extract_catalogs(query, resources=['cargoCategory'], profile=PROFILE,
        reference_scope={'cargoCategory': {'cua_lo': ['30'], 'ben_thuy': ['30']}})['cargoCategory']
    assert not result['ready'] and result['rows'] == []
    assert any(problem['code'] == 'SOURCE_REFERENCE_NOT_FOUND' for problem in result['blockers'])


def test_duplicate_native_id_within_one_database_remains_a_source_error():
    query = ScopedQuery()
    query.data['SmartTOS']['JobMethod'].append(deepcopy(query.data['SmartTOS']['JobMethod'][0]))
    result = extract_catalogs(query, resources=['handlingMethodList'], profile=PROFILE)['handlingMethodList']
    assert not result['ready'] and result['rows'] == []
    assert result['blockers'][0]['code'] == 'SOURCE_DATA'


@pytest.mark.parametrize('database', ['SmartTOS', 'SmartTOS_BenThuy'])
def test_full_prefixed_catalog_requires_both_terminal_reads(database):
    query = ScopedQuery()
    query.fail_db = database
    result = extract_catalogs(query, resources=['shipDetails'], profile=PROFILE)['shipDetails']
    assert not result['ready'] and result['rows'] == []
    assert result['blockers'][0]['code'] == 'SOURCE_UNAVAILABLE'


@pytest.mark.parametrize('override', [
    {'source_identity_mode': 'unapproved-synthetic-mode'},
    {'cargo_catalog_source': 'configured'}, {'container_size_source': 'configured'},
])
def test_incompatible_or_unknown_identity_configuration_fails_before_reads(override):
    query = ScopedQuery()
    with pytest.raises(ValueError):
        extract_catalogs(query, resources=['shipDetails'], profile={**PROFILE, **override})
    assert query.calls == []

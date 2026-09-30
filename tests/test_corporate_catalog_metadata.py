"""Catalog schema probes use only required tables without scanning SQL metadata."""
from copy import deepcopy

import pytest

from backend.corporate_api.catalog_source import extract_catalogs, _Reader, SourceProblem
from backend.corporate_api.errors import CorporateError
from test_corporate_reference_scope import ScopedQuery, NATIVE_PROFILE


class DescribedQuery(ScopedQuery):
    def __init__(self):
        super().__init__()
        self.descriptions = []
        self.metadata_error = None
        self.metadata_override = None
        self.use_override = False

    def describe_table(self, database, table):
        self.descriptions.append((database, table))
        if self.metadata_error:
            raise self.metadata_error
        if self.use_override:
            return deepcopy(self.metadata_override)
        return [{'column_name': column, 'data_type': datatype}
                for column, datatype in self.schemas[database][table].items()]

    def __call__(self, database, statement, params):
        assert 'INFORMATION_SCHEMA' not in statement, 'Fast capability must avoid broad metadata SQL'
        return super().__call__(database, statement, params)


@pytest.mark.parametrize('resource,tables', [
    ('shipDetails', ['Vessel']), ('customers', ['Partner']),
    ('handlingMethodList', ['JobMethod']), ('class', ['CargoDirect']),
    ('origins', ['CargoOrigin']), ('cargoType', ['CargoGroup']),
    ('cargoCategory', ['Cargo', 'CargoGroup']),
    ('containerSize', ['vwContainerSizeTypeDomestic']),
])
def test_only_selected_catalog_dependencies_use_zero_row_metadata_capability(resource, tables):
    source = DescribedQuery()
    result = extract_catalogs(source, resources=[resource], profile=NATIVE_PROFILE)
    assert result[resource]['ready']
    assert source.descriptions == [(database, table)
        for database in ('SmartTOS', 'SmartTOS_BenThuy') for table in tables]
    assert all('INFORMATION_SCHEMA' not in sql for _, sql, _ in source.calls)


def test_combined_catalogs_probe_shared_table_once_per_source():
    source = DescribedQuery()
    result = extract_catalogs(source, resources=['cargoType', 'cargoCategory'], profile=NATIVE_PROFILE)
    assert all(dataset['ready'] for dataset in result.values())
    assert source.descriptions == [
        ('SmartTOS', 'Cargo'), ('SmartTOS', 'CargoGroup'),
        ('SmartTOS_BenThuy', 'Cargo'), ('SmartTOS_BenThuy', 'CargoGroup')]


def test_fast_metadata_does_not_change_catalog_payload_or_validation():
    fast = extract_catalogs(DescribedQuery(), resources=['origins', 'class'], profile=NATIVE_PROFILE)
    fallback = extract_catalogs(ScopedQuery(), resources=['origins', 'class'], profile=NATIVE_PROFILE)
    assert fast == fallback


def test_each_extraction_probes_fresh_metadata_without_retaining_a_schema_cache():
    source = DescribedQuery()
    assert extract_catalogs(source, resources=['origins'])['origins']['ready']
    source.schemas['SmartTOS']['CargoOrigin']['cargoOriginId'] = 'nvarchar'
    assert not extract_catalogs(source, resources=['origins'])['origins']['ready']
    assert source.descriptions == [('SmartTOS', 'CargoOrigin'), ('SmartTOS_BenThuy', 'CargoOrigin')] * 2


@pytest.mark.parametrize('capability', [None, False, 'not callable', {}])
def test_missing_or_noncallable_capability_keeps_parameterized_metadata_fallback(capability):
    source = ScopedQuery()
    source.describe_table = capability
    result = extract_catalogs(source, resources=['origins'])['origins']
    assert result['ready']
    metadata = [(sql, params) for _, sql, params in source.calls if 'INFORMATION_SCHEMA' in sql]
    assert len(metadata) == 2
    assert all(params == ('CargoOrigin',) and sql.count('?') == 1 for sql, params in metadata)


@pytest.mark.parametrize('metadata', [
    None, {}, 'columns', [None], [{'column_name': 'id'}],
    [{'column_name': None, 'data_type': 'int'}],
    [{'column_name': '', 'data_type': 'int'}],
    [{'column_name': 'id', 'data_type': None}],
    [{'column_name': 'id', 'data_type': ''}],
    [{'column_name': 'id', 'data_type': []}],
    [{'column_name': 'id', 'data_type': 'int'}, {'column_name': 'id', 'data_type': 'nvarchar'}],
])
def test_malformed_fast_metadata_fails_closed_before_data_query(metadata):
    source = DescribedQuery()
    source.use_override = True
    source.metadata_override = metadata
    result = extract_catalogs(source, resources=['origins'])['origins']
    assert not result['ready'] and result['rows'] == []
    assert {item['code'] for item in result['blockers']} == {'SOURCE_SCHEMA'}
    assert source.calls == []


@pytest.mark.parametrize('column,wrong_type', [
    ('cargoOriginId', 'nvarchar'), ('cargoOriginName', 'int'),
    ('rowDeleted', 'int'), ('createTime', 'nvarchar'), ('updateTime', 'varbinary'),
])
def test_fast_driver_type_categories_still_enforce_source_column_contract(column, wrong_type):
    source = DescribedQuery()
    for database in source.schemas:
        source.schemas[database]['CargoOrigin'][column] = wrong_type
    result = extract_catalogs(source, resources=['origins'])['origins']
    assert not result['ready'] and result['rows'] == []
    assert {item['code'] for item in result['blockers']} == {'SOURCE_SCHEMA'}
    assert source.calls == []


def test_datetime2_driver_category_is_accepted_without_changing_source_timestamp():
    source = DescribedQuery()
    for database in source.schemas:
        source.schemas[database]['CargoOrigin']['createTime'] = 'datetime2'
        source.schemas[database]['CargoOrigin']['updateTime'] = 'datetime2'
    result = extract_catalogs(source, resources=['origins'])['origins']
    assert result['ready']
    assert result['rows'][0]['createdDate'] == '2026-09-01T12:00:00'


@pytest.mark.parametrize('code,expected', [
    ('SOURCE_TIMEOUT', 'SOURCE_TIMEOUT'), ('SOURCE_UNAVAILABLE', 'SOURCE_UNAVAILABLE'),
    ('SOURCE_SCHEMA', 'SOURCE_SCHEMA'), ('SOURCE_ROW_LIMIT', 'SOURCE_ROW_LIMIT'),
    ('PRIVATE_UNKNOWN_CODE', 'SOURCE_UNAVAILABLE'),
])
def test_fast_metadata_preserves_only_safe_typed_error_categories(code, expected):
    source = DescribedQuery()
    source.metadata_error = CorporateError(503, code, 'private-host-and-password')
    result = extract_catalogs(source, resources=['origins'])['origins']
    assert not result['ready'] and {item['code'] for item in result['blockers']} == {expected}
    assert source.calls == [] and 'private-host' not in str(result)


def test_generic_fast_metadata_error_has_no_source_details():
    source = DescribedQuery()
    source.metadata_error = RuntimeError('private-host-and-password')
    result = extract_catalogs(source, resources=['origins'])['origins']
    assert not result['ready'] and result['blockers'][0]['code'] == 'SOURCE_UNAVAILABLE'
    assert source.calls == [] and 'private-host' not in str(result)


def test_table_allowlist_is_checked_before_capability_calls():
    source = DescribedQuery()
    with pytest.raises(SourceProblem) as caught:
        _Reader(source, 'cua_lo', tables=['CargoOrigin; DROP TABLE private'])
    assert caught.value.code == 'SOURCE_SCHEMA'
    assert source.descriptions == [] and source.calls == []


def test_configured_catalog_without_native_dependency_never_probes_metadata():
    source = DescribedQuery()
    result = extract_catalogs(source, resources=['cargoType'],
        profile={'approved': True, 'cargo_types': {'NATIVE-TEST': 'Synthetic approved type'}})
    assert result['cargoType']['ready']
    assert source.descriptions == [] and source.calls == []

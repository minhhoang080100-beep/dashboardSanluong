"""Opt-in public identities keep production and source filters consistent.

The shared SQLite adapter executes the generated source filters without making
SQL Server connections. Source projection and berth fixtures remain synthetic.
"""
from copy import deepcopy
from datetime import date
from decimal import Decimal

import pytest

from backend.corporate_api import source as module
from backend.corporate_api.source import ProductionSource, SCHEMA_SQL, SourceError
from backend.corporate_api.source_identity import MODE
from test_corporate_production_filter_pushdown import DAY, RelationalSource
from test_corporate_source import profile


TERMINALS = [('cua_lo', 'SmartTOS', 'CL'), ('ben_thuy', 'SmartTOS_BenThuy', 'BT')]
RESOURCES = ['contQuayVolumesCB', 'contGateVolumesCB',
             'bulkQuayVolumesCB', 'bulkGateVolumesCB']
FOREIGN_IDS = {
    'contQuayVolumesCB': {'handlingMethodId': '10', 'shipId': '9',
                         'classId': '1', 'containerSizeId': '1'},
    'contGateVolumesCB': {'handlingMethodId': '20', 'containerSizeId': '1'},
    'bulkQuayVolumesCB': {'handlingMethodId': '10', 'shipId': '9',
                         'shipClassId': '1', 'cargoTypeId': '55', 'cargoCategoryId': '2'},
    'bulkGateVolumesCB': {'handlingMethodId': '20', 'cargoTypeId': '55',
                         'cargoCategoryId': '2', 'customerCode': '7'},
}


def qualified_profile():
    config = profile()
    config.update(source_identity_mode=MODE, cargo_catalog_source='native_groups',
                  container_size_source='native_cargo')
    config['cargo_kind_by_cargo']['ben_thuy'] = deepcopy(config['cargo_kind_by_cargo']['cua_lo'])
    # Native catalogs are the authority; no unrelated static ID list is needed.
    config.pop('cargo_types')
    config.pop('cargo_type_by_cargo')
    config.pop('container_sizes_by_cargo')
    return config


def resource_fact(resource):
    return {
        'cargo_id': 1 if resource.startswith('cont') else 2,
        'cargo_name': '40F' if resource.startswith('cont') else 'Da trang',
        'cargo_group_id': 54 if resource.startswith('cont') else 55,
        'quantity_unit_code': 'CONT' if resource.startswith('cont') else 'TAN',
        'method_id': 10 if 'Quay' in resource else 20,
        'quay_eligible': 1 if 'Quay' in resource else 0,
    }


@pytest.fixture
def query():
    adapter = RelationalSource()
    yield adapter
    adapter.close()


@pytest.mark.parametrize('resource', RESOURCES)
def test_each_production_api_qualifies_every_fk_without_changing_measures(query, resource):
    for _, database, _ in TERMINALS:
        query.add(database, source_id=1, **resource_fact(resource))
        query.add(database, source_id=2, native_weight=Decimal('4.5'), quantity=2,
                  **resource_fact(resource))
    config = qualified_profile()
    qualified = ProductionSource(query).extract(DAY, DAY, config, resources=[resource])[resource]
    native_config = {key: value for key, value in config.items() if key != 'source_identity_mode'}
    native = ProductionSource(query).extract(DAY, DAY, native_config, resources=[resource])[resource]

    assert qualified['ready'], qualified['blockers']
    assert native['ready'], native['blockers']
    assert qualified['source_row_count'] == native['source_row_count'] == 4
    # Equal dimensions in one terminal still aggregate. Equal native IDs in
    # different terminals now identify separate rows, with unchanged totals.
    assert len(qualified['rows']) == 2
    assert len(native['rows']) == 1
    measures = ['containerWeight', 'containerTEU'] if resource.startswith('cont') else ['bulkWeight']
    for measure in measures:
        assert sum(row[measure] for row in qualified['rows']) == native['rows'][0][measure]
    assert sum(row[measures[0]] for row in qualified['rows']) == Decimal('69.250')

    for terminal, _, prefix in TERMINALS:
        row, = [row for row in qualified['rows'] if row['_sourceTerminals'] == [terminal]]
        expected = {'reportDate': '20260921', 'companyId': 'CNT', 'finishDate': '20260916',
                    '_sourceTerminals': [terminal]}
        expected.update({key: f'{prefix}:{value}' for key, value in FOREIGN_IDS[resource].items()})
        if resource.startswith('cont'):
            expected.update(originId=None, containerOperatorId=None,
                            containerWeight=Decimal('34.625'), containerTEU=Decimal('6'))
            if 'Quay' in resource:
                expected['shipOperatorId'] = None
        else:
            expected.update(bulkOriginId=None, bulkWeight=Decimal('34.625'))
            if 'Quay' in resource:
                expected['shipAgentId'] = None
        assert row == expected


FILTER_CASES = [(resource, fields) for resource in RESOURCES
                for fields in (('handlingMethodId',), ('shipId',), ('shipId', 'handlingMethodId'))
                if 'Quay' in resource or fields == ('handlingMethodId',)]


@pytest.mark.parametrize('terminal,database,prefix', TERMINALS)
@pytest.mark.parametrize('resource,fields', FILTER_CASES)
def test_qualified_filter_reads_only_encoded_terminal_with_native_bound_ids(
        query, monkeypatch, terminal, database, prefix, resource, fields):
    monkeypatch.setattr(module, 'MAX_SOURCE_ROWS', 2)
    monkeypatch.setattr(module, 'MAX_BERTH_ROWS', 2)
    changes = resource_fact(resource)
    for _, source_database, _ in TERMINALS:
        # Unrelated rows precede the requested fact and exceed the cap unless
        # the actual SQL filters are applied before the bounded source read.
        for key in range(1, 4):
            decoy = dict(changes)
            if 'handlingMethodId' in fields:
                decoy['method_id'] = 999
            if 'shipId' in fields:
                decoy['ship_id'] = 99
            query.add(source_database, source_id=key, **decoy)
        query.add(source_database, source_id=4, **changes)
    filters = {field: f'{prefix}:{FOREIGN_IDS[resource][field]}' for field in fields}
    result = ProductionSource(query).extract(DAY, DAY, qualified_profile(),
        resources=[resource], production_filters=filters)[resource]

    assert result['ready'], result['blockers']
    assert result['source_row_count'] == 1
    row, = result['rows']
    assert row['_sourceTerminals'] == [terminal]
    assert row['handlingMethodId'] == f'{prefix}:{changes["method_id"]}'
    assert {source_database for source_database, _, _ in query.calls} == {database}
    expected_ids = tuple(FOREIGN_IDS[resource][field]
                         for field in ('shipId', 'handlingMethodId') if field in filters)
    source_calls = [(sql, params) for _, sql, params in query.calls if sql != SCHEMA_SQL]
    assert source_calls
    for sql, params in source_calls:
        assert params == (DAY, date(2026, 9, 17), *expected_ids)
        assert sql.count('?') == len(params)
        if 'shipId' in filters:
            assert 'vesselId=CAST(? AS BIGINT)' in sql
        if 'handlingMethodId' in filters:
            assert 't.jobMethodId=CAST(? AS BIGINT)' in sql


@pytest.mark.parametrize('resource', ['contQuayVolumesCB', 'bulkQuayVolumesCB'])
@pytest.mark.parametrize('ship_prefix,method_prefix', [('CL', 'BT'), ('BT', 'CL')])
def test_filters_from_different_terminals_cannot_match_one_fact(
        query, resource, ship_prefix, method_prefix):
    for _, database, _ in TERMINALS:
        query.add(database, **resource_fact(resource))
    result = ProductionSource(query).extract(DAY, DAY, qualified_profile(), resources=[resource],
        production_filters={'shipId': f'{ship_prefix}:9',
                            'handlingMethodId': f'{method_prefix}:10'})[resource]
    assert result['ready'], result['blockers']
    assert result['rows'] == []
    assert result['source_row_count'] == 0
    assert result['coverage'] == [['20260916', '20260916']]
    assert query.calls == []


@pytest.mark.parametrize('field', ['shipId', 'handlingMethodId'])
@pytest.mark.parametrize('identity', [
    '9', 'CL:09', 'CL:0', 'CL:-9', 'CL:+9', 'CL:9.0', 'CL:9e0',
    'CL:９', 'cl:9', 'XX:9', 'CL:BT:9', 'CL:9:1', ' CL:9', 'CL:9 ',
    'CL:9 OR 1=1', 'CL:9223372036854775808', 'BT:9999999999999999999',
    'BT:10000000000000000000', '', 9, None,
])
def test_invalid_qualified_filter_is_rejected_before_any_source_query(query, field, identity):
    with pytest.raises(SourceError) as caught:
        ProductionSource(query).extract(DAY, DAY, qualified_profile(),
            resources=['contQuayVolumesCB'], production_filters={field: identity})
    assert caught.value.code == 'SOURCE_FILTER'
    assert query.calls == []


@pytest.mark.parametrize('overrides', [
    {'source_identity_mode': 'unknown'},
    {'source_identity_mode': None},
    {'cargo_catalog_source': 'configured'},
    {'container_size_source': 'configured'},
])
def test_incompatible_identity_profile_is_rejected_before_source_queries(query, overrides):
    config = qualified_profile()
    config.update(overrides)
    with pytest.raises(SourceError) as caught:
        ProductionSource(query).extract(DAY, DAY, config, resources=['contQuayVolumesCB'])
    assert caught.value.code == 'SOURCE_PROFILE'
    assert query.calls == []

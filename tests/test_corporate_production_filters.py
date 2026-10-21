"""Explicit production filters constrain validation and reference dependencies."""
from copy import deepcopy
from datetime import date
from decimal import Decimal

import pytest

from backend.corporate_api.contracts import Query as PublicQuery
from backend.corporate_api.errors import CorporateError
from backend.corporate_api.live import LiveReader
from backend.corporate_api.manage_exports import extract, publish_preview
from backend.corporate_api.memory_store import MemoryExportStore
from backend.corporate_api.source import ProductionSource, RESOURCES, SourceError
from backend.corporate_api.store import ExportStore
from test_corporate_end_to_end import full_profile
from test_corporate_live_reference_sources import ProductionSourceQuery, source_fact_calls
from test_corporate_reference_scope import NATIVE_PROFILE
from test_corporate_source import Query as SourceQuery, fact, profile


DAY = date(2026, 9, 16)


def source_result(rows, resource, filters=None, config=None):
    config = deepcopy(config or profile())
    config['quay_method_ids']['cua_lo'] = [10, 11]
    config['gate_method_ids']['cua_lo'] = [20, 21]
    return ProductionSource(SourceQuery(rows)).extract(DAY, DAY, config,
        resources=[resource], production_filters=filters)[resource]


@pytest.mark.parametrize('resource', RESOURCES)
def test_method_filter_is_applied_before_unknown_cargo_and_weight_validation(resource):
    quay = 'Quay' in resource
    method = 10 if quay else 20
    cargo = 1 if resource.startswith('cont') else 2
    rows = [fact(cargo_id=cargo, method_id=method, quay_eligible=int(quay)),
            fact(source_id=2, cargo_id=999, method_id=method + 1,
                 native_weight=None, quay_eligible=int(quay))]
    unfiltered = source_result(rows, resource)
    assert not unfiltered['ready'] and 'CARGO_KIND_UNMAPPED' in unfiltered['blockers']
    filtered = source_result(rows, resource, {'handlingMethodId': str(method)})
    assert filtered['ready'] and len(filtered['rows']) == 1
    assert filtered['rows'][0]['handlingMethodId'] == str(method)
    assert filtered['source_row_count'] == 1


@pytest.mark.parametrize('resource', ['contQuayVolumesCB', 'bulkQuayVolumesCB'])
def test_ship_filter_does_not_hide_an_invalid_matching_row(resource):
    cargo = 1 if resource.startswith('cont') else 2
    rows = [fact(cargo_id=cargo, native_weight=None),
            fact(source_id=2, cargo_id=999, ship_id=99)]
    result = source_result(rows, resource, {'shipId': '9'})
    assert not result['ready'] and result['blockers'] == ['WEIGHT_OR_UNIT_UNAVAILABLE']


def test_filters_combine_with_and_and_leave_unknown_unfiltered_rows_blocking():
    rows = [fact(), fact(source_id=2, method_id=11, ship_id=9, cargo_id=999),
            fact(source_id=3, method_id=10, ship_id=99, cargo_id=999)]
    both = source_result(rows, 'contQuayVolumesCB', {'shipId': '9', 'handlingMethodId': '10'})
    assert both['ready'] and len(both['rows']) == 1
    for filters in ({'shipId': '9'}, {'handlingMethodId': '10'}, {}):
        assert 'CARGO_KIND_UNMAPPED' in source_result(rows, 'contQuayVolumesCB', filters)['blockers']


@pytest.mark.parametrize('value', ['09', '9.0', '9e0', 'CNT-CL-9', '9 OR 1=1'])
def test_filter_uses_exact_public_native_id_without_numeric_aliases(value):
    result = source_result([fact()], 'contQuayVolumesCB', {'shipId': value})
    assert result['ready'] and result['rows'] == []


@pytest.mark.parametrize('filters', [[], '9', {'shipId': 9}, {'shipId': None},
                                    {'shipId': True}, {'shipId': ''}, {'shipId': ' 9'},
                                    {'cargoTypeId': '1'}, {'unknown': '1'}])
def test_invalid_filter_options_fail_before_source_reads(filters):
    query = SourceQuery([fact()])
    with pytest.raises(SourceError) as caught:
        ProductionSource(query).extract(DAY, DAY, profile(),
            resources=['contQuayVolumesCB'], production_filters=filters)
    assert caught.value.code == 'SOURCE_FILTER' and query.calls == []


@pytest.mark.parametrize('resource', ['contGateVolumesCB', 'bulkGateVolumesCB'])
def test_ship_filter_is_not_supported_for_gate_resources(resource):
    with pytest.raises(SourceError):
        source_result([fact()], resource, {'shipId': '9'})


@pytest.mark.parametrize('scope', ['vietsun', 'unclassified'])
def test_matching_ship_filter_cannot_bypass_nghe_tinh_first_berth_scope(scope):
    config = profile()
    config['production_scope'] = 'nghe_tinh'
    result = source_result([fact(production_scope=scope)], 'contQuayVolumesCB',
                           {'shipId': '9'}, config)
    assert result['ready'] and result['rows'] == []
    assert result['excluded_scope_rows'] == 1


def test_ship_filter_still_blocks_a_matching_row_without_a_method():
    config = profile()
    config['quay_selection'] = 'source_statistics'
    result = source_result([fact(method_id=None)], 'contQuayVolumesCB',
                           {'shipId': '9'}, config)
    assert not result['ready']
    assert result['blockers'] == ['METHOD_ID_UNAVAILABLE']


def test_filtered_ship_uses_only_its_berth_history_without_changing_caller_rows():
    rows = [fact(), fact(source_id=2, ship_id=99, native_weight=None)]
    original = deepcopy(rows)
    query = SourceQuery(rows, berths=[
        {'voyage_id': 1, 'production_scope': 'nghe_tinh'},
        {'voyage_id': 2, 'production_scope': 'invalid-unrelated-scope'},
    ])
    result = ProductionSource(query).extract(DAY, DAY, profile(),
        resources=['contQuayVolumesCB'], production_filters={'shipId': '9'})['contQuayVolumesCB']
    assert result['ready'] and len(result['rows']) == 1
    assert rows == original
    unfiltered = ProductionSource(query).extract(DAY, DAY, profile(),
        resources=['contQuayVolumesCB'])['contQuayVolumesCB']
    assert not unfiltered['ready'] and 'SOURCE_DATA' in unfiltered['blockers']


@pytest.mark.parametrize('berths', [
    [{'voyage_id': 1, 'production_scope': 'invalid-matching-scope'}],
    [{'voyage_id': 1, 'production_scope': 'nghe_tinh'},
     {'voyage_id': 1, 'production_scope': 'vietsun'}],
])
def test_filtered_ship_does_not_accept_invalid_or_duplicate_matching_berth_history(berths):
    result = ProductionSource(SourceQuery([fact()], berths=berths)).extract(
        DAY, DAY, profile(), resources=['contQuayVolumesCB'],
        production_filters={'shipId': '9'})['contQuayVolumesCB']
    assert not result['ready'] and 'SOURCE_DATA' in result['blockers']


def reader_for(source, config=None):
    def query_filter_ids(database, statement, params):
        source.calls.append((database, statement, params))
        selected = [field for field in ('shipId', 'handlingMethodId') if 'AS [' + field + ']' in statement]
        fields = {'shipId': ('Vessel', 'vesselId'), 'handlingMethodId': ('JobMethod', 'jobMethodId')}
        return [{field: int(any(str(row[key]) == value and not row.get('rowDeleted')
                               and (field != 'shipId' or not row.get('isVirtualVessel'))
                               for row in source.data[database][table]))
                 for field, value in zip(selected, params) for table, key in [fields[field]]}]
    return LiveReader(profile=config or {**full_profile(), **NATIVE_PROFILE}, extract_fn=source.extract,
                      filter_query_fn=query_filter_ids)


def read_bulk(reader, **filters):
    return reader.read('bulkQuayVolumesCB', PublicQuery(companyId='CNT',
        startDate='20260916', endDate='20260916', **filters))


def add_unrelated_ship(source):
    row = deepcopy(source.facts['SmartTOS'][0])
    row.update(source_id=999, voyage_id=999, ship_id=999)
    source.facts['SmartTOS'].append(row)


def test_live_ship_filter_excludes_unrelated_missing_reference_before_catalog_expansion():
    source = ProductionSourceQuery()
    add_unrelated_ship(source)
    result = read_bulk(reader_for(source), shipId='10')
    assert result['code'] == '1' and result['pagination']['total'] == 1
    assert result['data'][0]['shipId'] == '10' and result['data'][0]['bulkWeight'] == 12.75
    assert source.scopes[1]['shipDetails'] == {'cua_lo': ['10'], 'ben_thuy': []}
    assert source_fact_calls(source) == ['SmartTOS', 'SmartTOS_BenThuy']
    with pytest.raises(CorporateError) as caught:
        read_bulk(reader_for(source))
    assert caught.value.code == 'SOURCE_REFERENCE_NOT_FOUND'


def test_live_method_filter_excludes_unrelated_missing_method_catalog():
    source = ProductionSourceQuery()
    row = deepcopy(source.facts['SmartTOS'][0])
    row.update(source_id=999, voyage_id=999, method_id=999)
    source.facts['SmartTOS'].append(row)
    config = {**full_profile(), **NATIVE_PROFILE, 'quay_selection': 'source_statistics'}
    result = read_bulk(reader_for(source, config), handlingMethodId='40')
    assert len(result['data']) == 1 and result['data'][0]['handlingMethodId'] == '40'
    assert source.scopes[1]['handlingMethodList'] == {'cua_lo': ['40'], 'ben_thuy': []}
    with pytest.raises(CorporateError) as caught:
        read_bulk(reader_for(source, config))
    assert caught.value.code == 'SOURCE_REFERENCE_NOT_FOUND'


def test_live_matching_filter_still_validates_each_contributing_source_identity():
    source = ProductionSourceQuery()
    source.facts['SmartTOS_BenThuy'] = deepcopy(source.facts['SmartTOS'])
    source.data['SmartTOS_BenThuy']['Vessel'][0]['vesselName'] = 'Different actual ship'
    with pytest.raises(CorporateError) as caught:
        read_bulk(reader_for(source), shipId='10')
    assert caught.value.code == 'SOURCE_ID_CONFLICT'
    assert source.scopes[1]['shipDetails'] == {'cua_lo': ['10'], 'ben_thuy': ['10']}


def test_live_known_id_without_activity_needs_no_unused_catalogs_and_retains_zero_total():
    source = ProductionSourceQuery()
    source.facts = {'SmartTOS': [], 'SmartTOS_BenThuy': []}
    result = read_bulk(reader_for(source), shipId='10')
    assert result['code'] == '1' and result['data'] == []
    assert result['pagination']['total'] == 0 and result['pagination']['hasNext'] is False
    assert source.scopes == [None]


def test_empty_filter_match_cannot_turn_a_failed_terminal_read_into_complete_zero():
    source = ProductionSourceQuery()
    source.fail_fact_database = 'SmartTOS_BenThuy'
    with pytest.raises(CorporateError) as caught:
        read_bulk(reader_for(source), shipId='999')
    assert caught.value.code == 'SOURCE_UNAVAILABLE'
    assert source_fact_calls(source) == ['SmartTOS', 'SmartTOS_BenThuy']


def test_repeated_filtered_requests_read_fresh_source_instead_of_retaining_results():
    source = ProductionSourceQuery()
    reader = reader_for(source)
    first = read_bulk(reader, shipId='10')
    source.facts['SmartTOS'][0]['native_weight'] = Decimal('18.25')
    second = read_bulk(reader, shipId='10')
    assert first['data'][0]['bulkWeight'] == 12.75
    assert second['data'][0]['bulkWeight'] == 18.25
    assert source_fact_calls(source) == ['SmartTOS', 'SmartTOS_BenThuy'] * 2


def test_filtered_preview_marker_cannot_be_published_to_a_persistent_store(tmp_path):
    source = ProductionSourceQuery()
    config = {**full_profile(), **NATIVE_PROFILE}
    preview = extract(config, DAY, DAY, ['bulkQuayVolumesCB'], source,
                      production_filters={'shipId': '10'})
    assert preview['productionFilters'] == {'shipId': '10'}
    store = ExportStore(tmp_path / 'exports.sqlite3')
    for options in ({}, {'_request_filters': {'shipId': '10'}}):
        with pytest.raises(ValueError, match='matching transient API request'):
            publish_preview(preview, config, store, ['bulkQuayVolumesCB'], **options)
    with store.db() as db:
        assert db.execute('SELECT COUNT(*) FROM export_versions').fetchone()[0] == 0


def test_filtered_preview_requires_the_exact_request_scope_even_in_memory():
    source = ProductionSourceQuery()
    config = {**full_profile(), **NATIVE_PROFILE}
    preview = extract(config, DAY, DAY, ['bulkQuayVolumesCB'], source,
                      production_filters={'shipId': '10'})
    store = MemoryExportStore()
    try:
        for filters in (None, {'shipId': '999'}, {'handlingMethodId': '40'}):
            with pytest.raises(ValueError, match='matching transient API request'):
                publish_preview(preview, config, store, ['bulkQuayVolumesCB'], _request_filters=filters)
    finally:
        store.close()


@pytest.mark.parametrize('resources', [['origins'], ['oprt.cargoDirect'], ['bulkQuayVolumesCB', 'origins']])
def test_production_filter_keyword_cannot_scope_catalog_or_mixed_extraction(resources):
    with pytest.raises(ValueError, match='only available for production extraction'):
        extract(full_profile(), DAY, DAY, resources, production_filters={'handlingMethodId': '40'})

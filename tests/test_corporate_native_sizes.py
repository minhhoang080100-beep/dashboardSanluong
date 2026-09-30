"""Native size identity and production references; all source calls are synthetic."""
from copy import deepcopy
from datetime import date

import pytest

from backend.corporate_api.catalog_source import extract_catalogs
from backend.corporate_api.source import ProductionSource, SCHEMA_SQL
from test_corporate_catalog_source import FakeQuery, approved_profile
from test_corporate_source import Query, fact, profile


TABLE = 'vwContainerSizeTypeDomestic'


def native_source():
    query = FakeQuery()
    schema = {
        'containerSizeTypeDomesticId': 'int', 'containerSizeTypeDomesticCode': 'nvarchar',
        'containerSizeTypeId': 'int', 'containerSizeTypeCode': 'nvarchar',
        'containerSize': 'nvarchar', 'rowDeleted': 'bit', 'rowInvisible': 'bit',
        'createTime': 'datetime', 'updateTime': 'datetime',
    }
    rows = [dict(containerSizeTypeDomesticId=617, containerSizeTypeDomesticCode='45G0',
                 containerSizeTypeId=321, containerSizeTypeCode='45G0', containerSize='40',
                 rowDeleted=False, rowInvisible=False, createTime=None, updateTime=None),
            dict(containerSizeTypeDomesticId=775, containerSizeTypeDomesticCode='HC40',
                 containerSizeTypeId=321, containerSizeTypeCode='45G0', containerSize='40',
                 rowDeleted=None, rowInvisible=None, createTime=None, updateTime=None)]
    for db in query.schemas:
        query.schemas[db][TABLE] = deepcopy(schema)
        query.data[db][TABLE] = deepcopy(rows)
    return query


def test_native_sizes_preserve_local_rows_sharing_iso_and_actual_length():
    source = native_source()
    p = {**approved_profile(), 'container_size_source': 'native_domestic'}
    result = extract_catalogs(source, profile=p)['containerSize']
    assert result['ready']
    assert [r['containerSizeId'] for r in result['rows']] == ['617', '775']
    assert {r['isoSzTp'] for r in result['rows']} == {'45G0'}
    assert {r['sizeCode'] for r in result['rows']} == {'40'}
    assert all(r['heightCode'] is None and r['containerTypeCode'] is None for r in result['rows'])
    assert all(r['createdDate'] is None for r in result['rows'])


def test_native_size_only_request_probes_only_domestic_size_view_metadata():
    source = native_source()
    result = extract_catalogs(source, profile={'container_size_source': 'native_domestic'},
                              resources=['containerSize'])
    assert result['containerSize']['ready']
    assert [row['containerSizeId'] for row in result['containerSize']['rows']] == ['617', '775']
    metadata = [(sql, params) for _, sql, params in source.calls if 'INFORMATION_SCHEMA' in sql]
    assert len(metadata) == 2
    assert all(params == (TABLE,) and sql.count('?') == 1 for sql, params in metadata)


@pytest.mark.parametrize('problem', ['missing_schema', 'duplicate', 'missing_name'])
def test_native_sizes_fail_closed_on_invalid_source(problem):
    source = native_source()
    if problem == 'missing_schema':
        del source.schemas['SmartTOS'][TABLE]['containerSize']
    elif problem == 'duplicate':
        source.data['SmartTOS'][TABLE].append(deepcopy(source.data['SmartTOS'][TABLE][0]))
    else:
        source.data['SmartTOS'][TABLE][0]['containerSizeTypeDomesticCode'] = None
    result = extract_catalogs(source, profile={'container_size_source': 'native_domestic'})['containerSize']
    assert not result['ready'] and result['blockers']


@pytest.mark.parametrize('field', ['createTime', 'updateTime'])
def test_native_sizes_distinguish_omitted_selected_dates_from_explicit_null(field):
    source = native_source()
    del source.data['SmartTOS'][TABLE][0][field]
    result = extract_catalogs(source, profile={'container_size_source': 'native_domestic'})['containerSize']
    assert not result['ready'] and result['rows'] == []
    assert result['blockers'][0]['code'] == 'SOURCE_DATA'


def test_native_sizes_respect_hidden_rows_and_cross_database_conflicts():
    source = native_source()
    for db in source.data:
        source.data[db][TABLE][1]['rowInvisible'] = True
    p = {'container_size_source': 'native_domestic'}
    result = extract_catalogs(source, profile=p)['containerSize']
    assert result['ready'] and len(result['rows']) == 1
    source.data['SmartTOS_BenThuy'][TABLE][0]['containerSizeTypeDomesticCode'] = 'DIFFERENT'
    result = extract_catalogs(source, profile=p)['containerSize']
    assert not result['ready']
    assert result['blockers'][0]['code'] == 'SOURCE_ID_CONFLICT'


@pytest.mark.parametrize('mapped_id,cargo_name,expected', [
    (617, '40F', '617'), (775, '40E', '775'),
    (None, '40F', None), (321, '40F', None), (617, '45F', None),
])
def test_production_native_size_requires_verified_relation(mapped_id, cargo_name, expected):
    catalogs = native_source()
    facts = Query([fact(cargo_name=cargo_name)])
    def query(database, sql, params):
        if (sql == SCHEMA_SQL or 't.tallyShiftId AS source_id' in sql
                or 'berth_scope.vesselVoyageId AS voyage_id' in sql):
            return facts(database, sql, params)
        return catalogs(database, sql, params)
    p = {**profile(), 'container_size_source': 'native_domestic',
         'container_size_ids_by_cargo': {'cua_lo': {'1': mapped_id}}}
    result = ProductionSource(query).extract(date(2026, 9, 16), date(2026, 9, 16), p)['contQuayVolumesCB']
    if expected:
        assert result['ready']
        assert result['rows'][0]['containerSizeId'] == expected
        assert result['rows'][0]['containerTEU'] == 2
        assert result['rows'][0]['originId'] is None
        assert result['rows'][0]['containerOperatorId'] is None
    else:
        assert not result['ready'] and not result['rows']
        assert 'CONTAINER_SIZE_RELATION_UNCONFIRMED' in result['blockers']


def test_native_sizes_publish_with_matching_production_references(tmp_path):
    from test_corporate_end_to_end import FullSource, full_profile
    from backend.corporate_api.contracts import MODELS
    from backend.corporate_api.contracts import Query as ApiQuery
    from backend.corporate_api.manage_exports import extract, publish_preview
    from backend.corporate_api.store import ExportStore

    source, sizes = FullSource(), native_source()
    for db in source.data:
        source.schemas[db][TABLE] = sizes.schemas[db][TABLE]
        source.data[db][TABLE] = [dict(sizes.data[db][TABLE][0],
            containerSizeTypeDomesticCode='20GP', containerSizeTypeCode='22G0', containerSize='20')]
    p = {**full_profile(), 'container_size_source': 'native_domestic',
         'container_size_ids_by_cargo': {t: {'31': 617} for t in ('cua_lo', 'ben_thuy')}}
    preview = extract(p, date(2026, 9, 16), date(2026, 9, 16), sorted(MODELS), source)
    assert all(item['ready'] for item in preview['datasets'].values())
    store = ExportStore(tmp_path / 'native-exports.sqlite3')
    assert len(publish_preview(preview, p, store, sorted(MODELS))) == 12
    rows = store.read('contQuayVolumesCB', ApiQuery(companyId='CNT', startDate='20260916', endDate='20260916'))['data']
    assert rows and {r['containerSizeId'] for r in rows} == {'617'}

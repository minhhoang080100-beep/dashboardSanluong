"""Source-qualified live references: synthetic adapters only, no network/SQL."""
from copy import deepcopy
from datetime import datetime, timezone

import pytest

from backend.corporate_api.contracts import Query
from backend.corporate_api.errors import CorporateError
from backend.corporate_api.live import LiveReader
from backend.corporate_api.manage_exports import FORMAT, profile_digest, references_for
from backend.corporate_api.registry import IDENTITY
from backend.corporate_api.source_identity import decode_id, encode_id
from test_corporate_filter_identity_validation import ExistenceQuery
from test_corporate_end_to_end import full_profile
from test_corporate_live import MASTER, PRODUCTION_PROFILE, bulk_source
from test_corporate_live_reference_sources import ProductionSourceQuery, source_fact_calls
from test_corporate_reference_scope import NATIVE_PROFILE


PROFILE = {**PRODUCTION_PROFILE, **NATIVE_PROFILE, 'source_identity_mode': 'terminal_prefixed_v1',
           'quay_selection': 'source_statistics',
           'accepted_null_fields': {'bulkGateVolumesCB': ['bulkOriginId', 'customerCode']}}


class QualifiedSource:
    def __init__(self):
        self.rows, self.calls = {}, []
        for resource, originals in bulk_source().rows.items():
            self.rows[resource] = []
            for terminal in ('cua_lo', 'ben_thuy'):
                for original in originals:
                    row = deepcopy(original)
                    for field in set(references_for(resource)) | {IDENTITY.get(resource)}:
                        if field in row and row[field] is not None:
                            row[field] = encode_id(terminal, row[field], PROFILE)
                    row['_sourceTerminals'] = [terminal]
                    self.rows[resource].append(row)
        self.rows['origins'] = [
            {**MASTER, 'originId': f'{prefix}:1', 'originName': terminal, '_sourceTerminals': [terminal]}
            for prefix, terminal in [('CL', 'cua_lo'), ('BT', 'ben_thuy')]]
        self.rows['bulkQuayVolumesCB'] = []
        self.ignore_filters = False

    def __call__(self, profile, start, end, resources, *, reference_scope=None,
                 catalog_filters=None, production_filters=None):
        self.calls.append({'resources': list(resources), 'reference_scope': deepcopy(reference_scope),
                           'catalog_filters': deepcopy(catalog_filters),
                           'production_filters': deepcopy(production_filters)})
        datasets = {}
        for resource in resources:
            rows = deepcopy(self.rows[resource])
            if reference_scope is not None:
                rows = [row for row in rows if self.in_scope(row, resource, reference_scope[resource])]
            if catalog_filters and not self.ignore_filters:
                for field, value in catalog_filters[resource].items():
                    if field == 'identity':
                        rows = [row for row in rows if row[IDENTITY[resource]] == value]
                    elif field in ('cargoTypeId', 'containerSizeId'):
                        rows = [row for row in rows if row.get(field) == value]
            if production_filters and not self.ignore_filters:
                rows = [row for row in rows if all(row.get(field) == value for field, value in production_filters.items())]
            datasets[resource] = {'rows': rows, 'ready': True, 'blockers': [], 'warnings': [],
                'coverage': [[start.strftime('%Y%m%d'), end.strftime('%Y%m%d')]]}
        return {'format': FORMAT, 'companyId': 'CNT', 'profileDigest': profile_digest(profile),
                'sourceReadAt': datetime.now(timezone.utc).isoformat(),
                'startDate': start.strftime('%Y%m%d'), 'endDate': end.strftime('%Y%m%d'),
                **({'productionFilters': production_filters} if production_filters else {}), 'datasets': datasets}

    @staticmethod
    def in_scope(row, resource, scope):
        native, terminal = decode_id(row[IDENTITY[resource]], PROFILE)
        return native in scope[terminal]


def production_query(**filters):
    return Query(companyId='CNT', startDate='20260916', endDate='20260916', **filters)


def read(source, resource='bulkGateVolumesCB', **kwargs):
    return LiveReader(profile=PROFILE, extract_fn=source).read(resource, production_query(), **kwargs)


def test_same_native_dimensions_remain_two_source_qualified_rows_and_scopes():
    source = QualifiedSource()
    result = read(source)
    assert len(result['data']) == 2 and sum(row['bulkWeight'] for row in result['data']) == 25
    assert {row['handlingMethodId'] for row in result['data']} == {'CL:30', 'BT:30'}
    assert source.calls[1]['reference_scope'] == {
        'cargoCategory': {'cua_lo': ['20'], 'ben_thuy': ['20']},
        'cargoType': {'cua_lo': ['10'], 'ben_thuy': ['10']},
        'handlingMethodList': {'cua_lo': ['30'], 'ben_thuy': ['30']}}
    assert not any(key.startswith('_') for row in result['data'] for key in row)


def test_filter_retains_public_id_but_closure_reads_only_native_id_in_matching_terminal():
    source = QualifiedSource()
    result = LiveReader(profile=PROFILE, extract_fn=source).read(
        'bulkGateVolumesCB', production_query(handlingMethodId='BT:30'))
    assert len(result['data']) == 1 and result['data'][0]['handlingMethodId'] == 'BT:30'
    assert source.calls[0]['production_filters'] == {'handlingMethodId': 'BT:30'}
    assert all(targets['cua_lo'] == [] for targets in source.calls[1]['reference_scope'].values())


def test_catalog_detail_does_not_alias_same_native_id_from_other_terminal():
    source = QualifiedSource()
    result = LiveReader(profile=PROFILE, extract_fn=source).read(
        'origins', Query(companyId='CNT'), identity='BT:1')
    assert [row['originId'] for row in result['data']] == ['BT:1']
    assert source.calls == [{'resources': ['origins'], 'reference_scope': None,
                            'catalog_filters': {'origins': {'identity': 'BT:1'}}, 'production_filters': None}]


def test_catalog_detail_rejects_adapter_returning_wrong_terminal():
    source = QualifiedSource()
    source.ignore_filters = True
    with pytest.raises(CorporateError) as caught:
        LiveReader(profile=PROFILE, extract_fn=source).read('origins', Query(companyId='CNT'), identity='CL:1')
    assert caught.value.status == 503 and caught.value.code == 'SOURCE_DATA_NOT_READY'
    assert 'CL:' not in caught.value.message and 'BT:' not in caught.value.message


@pytest.mark.parametrize('resource', ['origins', 'cargoCategory', 'handlingMethodList'])
def test_full_catalog_prefix_must_match_provenance_even_without_dependencies(resource):
    source = QualifiedSource()
    source.rows[resource][0]['_sourceTerminals'] = ['ben_thuy']
    with pytest.raises(CorporateError) as caught:
        LiveReader(profile=PROFILE, extract_fn=source).read(resource, Query(companyId='CNT'))
    assert caught.value.code == 'SOURCE_DATA_NOT_READY'


@pytest.mark.parametrize('provenance', [['ben_thuy'], ['cua_lo', 'ben_thuy'], [], None])
def test_reference_prefix_must_match_exactly_one_source_provenance(provenance):
    source = QualifiedSource()
    source.rows['bulkGateVolumesCB'][0]['_sourceTerminals'] = provenance
    with pytest.raises(CorporateError) as caught:
        read(source)
    assert caught.value.code == 'SOURCE_DATA_NOT_READY'
    assert len(source.calls) == 1


def test_missing_cl_reference_cannot_be_satisfied_by_bt_catalog_row():
    source = QualifiedSource()
    source.rows['handlingMethodList'] = source.rows['handlingMethodList'][1:]
    with pytest.raises(CorporateError) as caught:
        read(source)
    assert caught.value.code == 'SOURCE_REFERENCE_NOT_FOUND'


def test_reference_membership_rejects_correct_native_id_from_unrequested_terminal():
    api = LiveReader(profile=PROFILE, extract_fn=lambda *args: pytest.fail('No extract expected'))
    with pytest.raises(ValueError, match='unrequested source'):
        api._verify_reference_membership('origins', [
            {'originId': 'BT:1', '_sourceTerminals': ['ben_thuy']}],
            {'cua_lo': {'1'}, 'ben_thuy': set()}, PROFILE)


def test_reference_membership_rejects_duplicate_prefixed_identity():
    api = LiveReader(profile=PROFILE, extract_fn=lambda *args: pytest.fail('No extract expected'))
    row = {'originId': 'CL:1', '_sourceTerminals': ['cua_lo']}
    with pytest.raises(ValueError, match='duplicate'):
        api._verify_reference_membership('origins', [row, row],
            {'cua_lo': {'1'}, 'ben_thuy': set()}, PROFILE)


def test_parent_closure_keeps_same_native_ids_separate_per_source():
    source = QualifiedSource()
    source.rows['cargoCategory'][0]['cargoParentId'] = 'CL:21'
    source.rows['cargoCategory'].append({**source.rows['cargoCategory'][0],
        'cargoId': 'CL:21', 'cargoParentId': None, 'cargoName': 'CL parent'})
    source.rows['cargoCategory'][1]['cargoParentId'] = 'BT:21'
    source.rows['cargoCategory'].append({**source.rows['cargoCategory'][1],
        'cargoId': 'BT:21', 'cargoParentId': None, 'cargoName': 'BT parent'})
    result = read(source)
    assert len(result['data']) == 2
    assert source.calls[-1]['reference_scope']['cargoCategory'] == {
        'cua_lo': ['20', '21'], 'ben_thuy': ['20', '21']}


def test_parent_cannot_reference_other_terminal_even_when_catalog_contains_same_id():
    source = QualifiedSource()
    source.rows['cargoCategory'][0]['cargoParentId'] = 'BT:20'
    with pytest.raises(CorporateError) as caught:
        read(source)
    assert caught.value.code == 'SOURCE_DATA_NOT_READY'


def test_scoped_reference_limit_counts_both_namespaces_even_when_native_ids_match(monkeypatch):
    monkeypatch.setattr('backend.corporate_api.live.MAX_REFERENCE_IDS', 1)
    source = QualifiedSource()
    with pytest.raises(CorporateError) as caught:
        read(source)
    assert caught.value.code == 'SOURCE_DATA_NOT_READY'
    assert len(source.calls) == 1


@pytest.mark.parametrize('value', ['10', 'cl:10', 'BT:01', 'BT:0', 'XX:10',
                                 'CL:9223372036854775808', 'CL:10 OR 1=1'])
@pytest.mark.parametrize('field', ['shipId', 'handlingMethodId'])
def test_invalid_production_filter_rejected_before_any_extract_or_membership(field, value):
    source, membership = QualifiedSource(), ExistenceQuery()
    with pytest.raises(CorporateError) as caught:
        LiveReader(profile=PROFILE, extract_fn=source, filter_query_fn=membership).read(
            'bulkQuayVolumesCB', production_query(**{field: value}))
    assert caught.value.code == 'FILTER_ID_NOT_FOUND' and caught.value.status == 422
    assert source.calls == [] and membership.calls == []


@pytest.mark.parametrize('value', ['1', 'cl:1', 'XX:1', 'BT:01', 'CL:9223372036854775808'])
def test_invalid_catalog_detail_rejected_before_extract(value):
    source = QualifiedSource()
    with pytest.raises(CorporateError) as caught:
        LiveReader(profile=PROFILE, extract_fn=source).read('origins', Query(companyId='CNT'), identity=value)
    assert caught.value.code == 'NOT_FOUND' and caught.value.status == 404
    assert source.calls == []


@pytest.mark.parametrize('resource,field', [('cargoCategory', 'cargoTypeId'), ('containerSize', 'containerSizeId')])
@pytest.mark.parametrize('value', ['1', 'cl:1', 'XX:1', 'BT:01', 'CL:9223372036854775808'])
def test_invalid_catalog_filter_rejected_before_extract(resource, field, value):
    source = QualifiedSource()
    with pytest.raises(CorporateError) as caught:
        LiveReader(profile=PROFILE, extract_fn=source).read(resource, Query(companyId='CNT', **{field: value}))
    assert caught.value.code == 'FILTER_ID_NOT_FOUND' and caught.value.status == 422
    assert source.calls == []


def test_mixed_valid_terminal_filters_can_return_empty_after_exact_membership_checks():
    source = QualifiedSource()
    membership = ExistenceQuery(found={
        'SmartTOS': {'shipId': {'10'}, 'handlingMethodId': set()},
        'SmartTOS_BenThuy': {'shipId': set(), 'handlingMethodId': {'30'}}})
    result = LiveReader(profile=PROFILE, extract_fn=source, filter_query_fn=membership).read(
        'bulkQuayVolumesCB', production_query(shipId='CL:10', handlingMethodId='BT:30'))
    assert result['data'] == [] and result['pagination']['total'] == 0
    assert [(db, params) for db, _, params in membership.calls] == [
        ('SmartTOS', ('10',)), ('SmartTOS_BenThuy', ('30',))]


def test_unknown_identity_mode_is_profile_error_before_extract():
    source = QualifiedSource()
    with pytest.raises(CorporateError) as caught:
        LiveReader(profile={**PROFILE, 'source_identity_mode': 'private-invalid'}, extract_fn=source).read(
            'origins', Query(companyId='CNT'))
    assert caught.value.code == 'SOURCE_PROFILE_INVALID'
    assert source.calls == [] and 'private-invalid' not in caught.value.message


def prefixed_real_adapter_reader(source, membership=None):
    return LiveReader(profile={**full_profile(), **NATIVE_PROFILE, 'source_identity_mode': 'terminal_prefixed_v1'},
                      extract_fn=source.extract, filter_query_fn=membership)


def test_real_adapters_keep_conflicting_ids_separate_and_validate_all_references():
    source = ProductionSourceQuery()
    source.facts['SmartTOS_BenThuy'] = deepcopy(source.facts['SmartTOS'])
    source.data['SmartTOS_BenThuy']['Vessel'][0]['vesselName'] = 'Different BT vessel with same native ID'
    source.data['SmartTOS_BenThuy']['JobMethod'][0]['jobMethodName'] = 'Different BT method'
    result = prefixed_real_adapter_reader(source).read('bulkQuayVolumesCB', production_query())
    assert len(result['data']) == 2
    assert {row['shipId'] for row in result['data']} == {'CL:10', 'BT:10'}
    assert {row['handlingMethodId'] for row in result['data']} == {'CL:40', 'BT:40'}
    assert sum(row['bulkWeight'] for row in result['data']) == 25.5
    assert source.scopes[1]['shipDetails'] == {'cua_lo': ['10'], 'ben_thuy': ['10']}


def test_real_adapters_scoped_production_filter_never_reads_wrong_source_facts():
    source = ProductionSourceQuery()
    source.facts['SmartTOS_BenThuy'] = deepcopy(source.facts['SmartTOS'])
    result = prefixed_real_adapter_reader(source).read('bulkQuayVolumesCB',
        production_query(shipId='BT:10', handlingMethodId='BT:40'))
    assert len(result['data']) == 1 and result['data'][0]['shipId'] == 'BT:10'
    assert source_fact_calls(source) == ['SmartTOS_BenThuy']
    assert all(targets['cua_lo'] == [] for targets in source.scopes[1].values())


def test_real_adapters_mixed_sources_skip_fact_queries_but_validate_exact_filter_keys():
    source = ProductionSourceQuery()
    membership = ExistenceQuery(found={
        'SmartTOS': {'shipId': {'10'}, 'handlingMethodId': set()},
        'SmartTOS_BenThuy': {'shipId': set(), 'handlingMethodId': {'40'}}})
    result = prefixed_real_adapter_reader(source, membership).read('bulkQuayVolumesCB',
        production_query(shipId='CL:10', handlingMethodId='BT:40'))
    assert result['data'] == [] and result['pagination']['total'] == 0
    assert source_fact_calls(source) == []
    assert [(db, params) for db, _, params in membership.calls] == [
        ('SmartTOS', ('10',)), ('SmartTOS_BenThuy', ('40',))]

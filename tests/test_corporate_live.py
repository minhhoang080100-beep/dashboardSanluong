"""Direct SmartTOS reader tests: synthetic adapters, no network or source writes."""
from copy import deepcopy
from datetime import date, datetime, timezone
import json
import threading
from concurrent.futures import ThreadPoolExecutor

import pytest

from backend.corporate_api.contracts import Query, IDENTITY
from backend.corporate_api.errors import CorporateError
from backend.corporate_api.live import LiveReader
from backend.corporate_api.manage_exports import FORMAT, extract, profile_digest
from backend.corporate_api.memory_store import MemoryExportStore
from backend.corporate_api.operation_contracts import OperationQuery


PROFILE = {'approved': True, 'company_id': 'CNT', 'terminals': ['cua_lo', 'ben_thuy']}
PRODUCTION_PROFILE = {**PROFILE, 'date_basis': 'shiftDate', 'production_scope': 'all_activity',
                      'gate_selection': 'vessel_type'}
MASTER = {'reportDate': '20260929', 'createdDate': '2026-09-01', 'modifiedDate': None}


class Source:
    def __init__(self, rows=None):
        self.rows = rows or {'origins': [
            {**MASTER, 'originId': str(number), 'originName': f'Origin {number}'}
            for number in (1, 2, 3)]}
        self.calls = []
        self.scopes = []
        self.blocked = set()

    def __call__(self, profile, start, end, resources, *, reference_scope=None):
        self.calls.append(tuple(resources))
        self.scopes.append(deepcopy(reference_scope))
        def rows(name):
            result = deepcopy(self.rows[name])
            if reference_scope is not None:
                requested = set().union(*map(set, reference_scope[name].values()))
                result = [row for row in result if row[IDENTITY[name]] in requested]
            return result
        return {'format': FORMAT, 'companyId': 'CNT', 'profileDigest': profile_digest(profile),
                'sourceReadAt': datetime.now(timezone.utc).isoformat(),
                'startDate': start.strftime('%Y%m%d'), 'endDate': end.strftime('%Y%m%d'),
                'datasets': {name: {'rows': rows(name),
                    'ready': name not in self.blocked,
                    'blockers': ['SOURCE_ID_CONFLICT'] if name in self.blocked else [],
                    'warnings': [], 'coverage': [[start.strftime('%Y%m%d'), end.strftime('%Y%m%d')]]}
                    for name in resources}}


def q(**changes):
    return Query(companyId='CNT', **changes)


def assert_error(code, action, status=None):
    with pytest.raises(CorporateError) as caught:
        action()
    assert caught.value.code == code
    if status is not None:
        assert caught.value.status == status
    return caught.value


@pytest.fixture
def source():
    return Source()


@pytest.fixture
def reader(source):
    result = LiveReader(profile=PROFILE, extract_fn=source)
    yield result
    result.close()


def test_every_page_reads_fresh_source_and_uses_no_export_file(tmp_path, monkeypatch, source):
    export = tmp_path / 'must-not-exist.sqlite3'
    monkeypatch.setenv('CORPORATE_EXPORT_PATH', str(export))
    reader = LiveReader(profile=PROFILE, extract_fn=source)
    try:
        first = reader.read('origins', q(limit=1))
        assert first['data'][0]['originId'] == '1'
        assert first['pagination']['total'] == 3
        assert 'snapshotId' not in first['pagination']
        source.rows['origins'][1]['originName'] = 'Changed source after page one'
        second = reader.read('origins', q(limit=1, page=2))
        assert second['data'][0]['originName'] == 'Changed source after page one'
        assert 'snapshotId' not in second['pagination']
        assert source.calls == [('origins',), ('origins',)]
        assert not export.exists()
        assert list(tmp_path.iterdir()) == []
    finally:
        reader.close()


def test_repeated_first_page_queries_again_and_sees_changed_data(reader, source):
    one = reader.read('origins', q())
    source.rows['origins'][0]['originName'] = 'Changed source'
    two = reader.read('origins', q())
    assert one['data'][0]['originName'] == 'Origin 1'
    assert two['data'][0]['originName'] == 'Changed source'
    assert len(source.calls) == 2


def test_incoming_snapshot_is_rejected_before_querying_source(reader, source):
    token = 'a' * 32
    assert_error('SNAPSHOT_NOT_SUPPORTED', lambda: reader.read('origins', q(snapshotId=token)), 422)
    assert_error('SNAPSHOT_NOT_SUPPORTED', lambda: reader.read(
        'origins', q(limit=1, page=2, snapshotId=token)), 422)
    assert source.calls == []


def test_page_two_does_not_require_a_prior_request(reader, source):
    result = reader.read('origins', q(limit=1, page=2))
    assert result['data'][0]['originId'] == '2'
    assert result['pagination']['page'] == 2
    assert result['pagination']['hasNext'] is True
    assert 'snapshotId' not in result['pagination']
    assert source.calls == [('origins',)]


def test_request_store_closes_before_each_response_and_is_never_retained(source):
    stores = []
    def factory():
        store = MemoryExportStore()
        stores.append(store)
        return store
    reader = LiveReader(profile=PROFILE, extract_fn=source, store_factory=factory)
    try:
        reader.read('origins', q(limit=1))
        assert stores[0]._closed
        reader.read('origins', q(limit=2))
        assert all(store._closed for store in stores)
        assert len(stores) == 2
        assert not any(isinstance(value, MemoryExportStore) for value in vars(reader).values())
    finally:
        reader.close()
    assert all(store._closed for store in stores)


def test_profile_is_loaded_again_for_each_request(tmp_path, monkeypatch, source):
    path = tmp_path / 'profile.json'
    path.write_text(json.dumps(PROFILE), encoding='utf-8')
    monkeypatch.setenv('CORPORATE_SOURCE_PROFILE', str(path))
    reader = LiveReader(extract_fn=source)
    try:
        reader.read('origins', q())
        path.write_text(json.dumps({**PROFILE, 'approved': False}), encoding='utf-8')
        assert_error('SOURCE_PROFILE_UNAPPROVED', lambda: reader.read('origins', q()), 503)
        assert source.calls == [('origins',)]
    finally:
        reader.close()


def test_missing_unapproved_or_partial_profile_fails_before_source(monkeypatch, source):
    monkeypatch.delenv('CORPORATE_SOURCE_PROFILE', raising=False)
    monkeypatch.delenv('CORPORATE_SYNC_PROFILE', raising=False)
    assert_error('SOURCE_PROFILE_REQUIRED', lambda: LiveReader(extract_fn=source).read('origins', q()))
    assert_error('SOURCE_PROFILE_UNAPPROVED', lambda: LiveReader(
        profile={**PROFILE, 'approved': False}, extract_fn=source).read('origins', q()))
    assert_error('SOURCE_TERMINALS_INCOMPLETE', lambda: LiveReader(
        profile={**PROFILE, 'terminals': ['cua_lo']}, extract_fn=source).read('origins', q()))
    assert source.calls == []


def test_sync_profile_fallback_and_invalid_file_safe_error(tmp_path, monkeypatch, source):
    monkeypatch.delenv('CORPORATE_SOURCE_PROFILE', raising=False)
    path = tmp_path / 'profile.json'
    monkeypatch.setenv('CORPORATE_SYNC_PROFILE', str(path))
    path.write_text('secret malformed source configuration', encoding='utf-8')
    reader = LiveReader(extract_fn=source)
    error = assert_error('SOURCE_PROFILE_INVALID', lambda: reader.read('origins', q()))
    assert 'secret' not in error.message
    path.write_text(json.dumps(PROFILE), encoding='utf-8')
    try:
        assert reader.read('origins', q())['code'] == '1'
    finally:
        reader.close()


def test_production_rejects_more_than_31_days_before_extraction(reader, source):
    assert_error('SOURCE_RANGE_TOO_LARGE', lambda: reader.read('bulkGateVolumesCB', q(
        startDate='20260101', endDate='20260929')), 422)
    assert source.calls == []


@pytest.mark.parametrize('resource,changes', [
    ('bulkGateVolumesCB', {'date_basis': None}),
    ('contGateVolumesCB', {'date_basis': 'createTime'}),
    ('bulkGateVolumesCB', {'production_scope': None}),
    ('bulkGateVolumesCB', {'production_scope': ['private-invalid-value']}),
    ('bulkGateVolumesCB', {'gate_selection': ['private-invalid-value']}),
    ('bulkQuayVolumesCB', {'quay_selection': ['private-invalid-value']}),
    ('bulkQuayVolumesCB', {'quay_selection': 'unknown'}),
    ('bulkQuayVolumesCB', {}),
    ('contQuayVolumesCB', {'quay_method_ids': {'cua_lo': [1]}}),
    ('bulkQuayVolumesCB', {'quay_method_ids': {'cua_lo': [1], 'ben_thuy': []}}),
    ('bulkGateVolumesCB', {'gate_selection': 'methods'}),
    ('contGateVolumesCB', {'gate_selection': 'methods', 'gate_method_ids': {'cua_lo': [1]}}),
])
def test_incomplete_production_mapping_fails_before_slot_or_source(resource, changes, source):
    class ForbiddenSlots:
        def acquire(self, **_):
            pytest.fail('Invalid production mapping must fail before acquiring the SQL slot')
    reader = LiveReader(profile={**PRODUCTION_PROFILE, **changes}, extract_fn=source)
    reader._slots = ForbiddenSlots()
    error = assert_error('SOURCE_MAPPING_REQUIRED', lambda: reader.read(
        resource, q(startDate='20260901', endDate='20260930')), 503)
    assert 'private-invalid-value' not in error.message
    assert source.calls == []


@pytest.mark.parametrize('resource,methods', [
    ('bulkGateVolumesCB', {}),
    ('contGateVolumesCB', {}),
    ('bulkQuayVolumesCB', {'quay_method_ids': {'cua_lo': [1], 'ben_thuy': [2]}}),
    ('contQuayVolumesCB', {'quay_method_ids': {'cua_lo': [1], 'ben_thuy': [2]}}),
    ('bulkQuayVolumesCB', {'quay_selection': 'source_statistics'}),
    ('contQuayVolumesCB', {'quay_selection': 'source_statistics'}),
    ('bulkGateVolumesCB', {'gate_selection': 'methods',
                           'gate_method_ids': {'cua_lo': [1], 'ben_thuy': [2]}}),
])
def test_production_preflight_accepts_endpoint_specific_selection(resource, methods):
    source = Source({resource: []})
    reader = LiveReader(profile={**PRODUCTION_PROFILE, **methods}, extract_fn=source)
    try:
        assert reader.read(resource, q(startDate='20260901', endDate='20260930'))['code'] == '1'
        assert source.calls == [(resource,)]
    finally:
        reader.close()


def bulk_source():
    rows = {
        'bulkGateVolumesCB': [{'reportDate': '20260929', 'finishDate': '20260916', 'companyId': 'CNT',
            'cargoTypeId': '10', 'cargoCategoryId': '20', 'handlingMethodId': '30',
            'bulkOriginId': None, 'bulkWeight': 12.5, 'customerCode': None}],
        'cargoCategory': [{**MASTER, 'cargoTypeId': '10', 'cargoParentId': None,
                           'cargoId': '20', 'cargoName': 'Native cargo'}],
        'cargoType': [{**MASTER, 'cargoTypeId': '10', 'cargoTypeName': 'Native group'}],
        'handlingMethodList': [{**MASTER, 'handlingMethodId': '30', 'handlingMethodName': 'Native method'}],
    }
    for data in rows.values():
        for row in data:
            row['_sourceTerminals'] = ['cua_lo']
    return Source(rows)


def test_production_resolves_actual_dependencies_only_and_enforces_null_policy():
    source = bulk_source()
    profile = {**PRODUCTION_PROFILE, 'accepted_null_fields': {'bulkGateVolumesCB': ['bulkOriginId', 'customerCode']}}
    reader = LiveReader(profile=profile, extract_fn=source)
    try:
        result = reader.read('bulkGateVolumesCB', q(startDate='20260901', endDate='20260930'))
        assert result['data'][0]['bulkWeight'] == 12.5
        assert {name for call in source.calls for name in call} == {
            'bulkGateVolumesCB', 'cargoCategory', 'cargoType', 'handlingMethodList'}
        assert len(source.calls) == 2
        assert source.scopes[1] == {
            'cargoCategory': {'cua_lo': ['20'], 'ben_thuy': []},
            'cargoType': {'cua_lo': ['10'], 'ben_thuy': []},
            'handlingMethodList': {'cua_lo': ['30'], 'ben_thuy': []}}
        assert not any(key.startswith('_') for row in result['data'] for key in row)
    finally:
        reader.close()
    source.calls.clear()
    def forbidden_factory():
        pytest.fail('Unaccepted nulls must fail before dependency reads or store allocation')
    unaccepted = LiveReader(profile=PRODUCTION_PROFILE, extract_fn=source, store_factory=forbidden_factory)
    try:
        assert_error('SOURCE_NULL_POLICY_REQUIRED', lambda: unaccepted.read(
            'bulkGateVolumesCB', q(startDate='20260901', endDate='20260930')), 503)
        assert source.calls == [('bulkGateVolumesCB',)]
    finally:
        unaccepted.close()


def test_partially_accepted_null_policy_still_blocks_before_dependency_reads():
    source = bulk_source()
    profile = {**PRODUCTION_PROFILE, 'accepted_null_fields': {'bulkGateVolumesCB': ['bulkOriginId']}}
    reader = LiveReader(profile=profile, extract_fn=source)
    try:
        error = assert_error('SOURCE_NULL_POLICY_REQUIRED', lambda: reader.read(
            'bulkGateVolumesCB', q(startDate='20260901', endDate='20260930')), 503)
        assert source.calls == [('bulkGateVolumesCB',)]
        assert 'Native cargo' not in error.message
    finally:
        reader.close()


def test_missing_dependency_reference_and_conflict_never_return_partial_success():
    source = bulk_source()
    profile = {**PRODUCTION_PROFILE, 'accepted_null_fields': {'bulkGateVolumesCB': ['bulkOriginId', 'customerCode']}}
    source.rows['cargoType'][0]['cargoTypeId'] = '999'
    reader = LiveReader(profile=profile, extract_fn=source)
    try:
        assert_error('SOURCE_REFERENCE_NOT_FOUND', lambda: reader.read(
            'bulkGateVolumesCB', q(startDate='20260901', endDate='20260930')), 503)
        source.blocked.add('cargoCategory')
        assert_error('SOURCE_ID_CONFLICT', lambda: reader.read(
            'bulkGateVolumesCB', q(startDate='20260901', endDate='20260930')), 503)
    finally:
        reader.close()


def production_reader(source, **kwargs):
    profile = {**PRODUCTION_PROFILE,
               'accepted_null_fields': {'bulkGateVolumesCB': ['bulkOriginId', 'customerCode']}}
    return LiveReader(profile=profile, extract_fn=source, **kwargs)


def read_bulk(reader):
    return reader.read('bulkGateVolumesCB', q(startDate='20260901', endDate='20260930'))


@pytest.mark.parametrize('provenance', [None, [], ['unknown'], ['cua_lo', 'cua_lo'], ['cua_lo', 1]])
def test_production_without_valid_provenance_cannot_use_union_catalogs(provenance):
    source = bulk_source()
    source.rows['bulkGateVolumesCB'][0]['_sourceTerminals'] = provenance
    reader = production_reader(source)
    assert_error('SOURCE_DATA_NOT_READY', lambda: read_bulk(reader), 503)
    assert source.calls == [('bulkGateVolumesCB',)]


def test_used_id_found_only_in_other_database_does_not_resolve_the_reference():
    source = bulk_source()
    source.rows['cargoType'][0]['_sourceTerminals'] = ['ben_thuy']
    reader = production_reader(source)
    assert_error('SOURCE_REFERENCE_NOT_FOUND', lambda: read_bulk(reader), 503)
    assert len(source.calls) == 2


def test_aggregated_production_requires_reference_in_every_contributing_source():
    source = bulk_source()
    source.rows['bulkGateVolumesCB'][0]['_sourceTerminals'] = ['cua_lo', 'ben_thuy']
    for resource in ('cargoType', 'handlingMethodList'):
        source.rows[resource][0]['_sourceTerminals'] = ['cua_lo', 'ben_thuy']
    reader = production_reader(source)
    assert_error('SOURCE_REFERENCE_NOT_FOUND', lambda: read_bulk(reader), 503)
    assert source.scopes[1]['cargoCategory'] == {'cua_lo': ['20'], 'ben_thuy': ['20']}
    source.rows['cargoCategory'][0]['_sourceTerminals'] = ['cua_lo', 'ben_thuy']
    assert read_bulk(reader)['data'][0]['bulkWeight'] == 12.5


def test_catalog_provenance_is_required_even_when_its_id_matches():
    source = bulk_source()
    del source.rows['cargoType'][0]['_sourceTerminals']
    assert_error('SOURCE_DATA_NOT_READY', lambda: read_bulk(production_reader(source)), 503)


def test_cargo_parent_closure_adds_parent_and_its_own_type_dependencies():
    source = bulk_source()
    source.rows['cargoCategory'][0]['cargoParentId'] = '21'
    source.rows['cargoCategory'].append({**MASTER, 'cargoId': '21', 'cargoName': 'Parent cargo',
        'cargoTypeId': '11', 'cargoParentId': None, '_sourceTerminals': ['cua_lo']})
    source.rows['cargoType'].append({**MASTER, 'cargoTypeId': '11', 'cargoTypeName': 'Parent type',
                                    '_sourceTerminals': ['cua_lo']})
    result = read_bulk(production_reader(source))
    assert result['data'][0]['bulkWeight'] == 12.5
    assert source.calls == [('bulkGateVolumesCB',),
        ('cargoCategory', 'cargoType', 'handlingMethodList'), ('cargoCategory',), ('cargoType',)]
    assert source.scopes[2] == {'cargoCategory': {'cua_lo': ['20', '21'], 'ben_thuy': []}}
    assert source.scopes[3] == {'cargoType': {'cua_lo': ['10', '11'], 'ben_thuy': []}}
    assert '_sourceTerminals' not in json.dumps(result)


def test_catalog_presence_in_an_additional_source_grows_dependencies_for_that_source():
    source = bulk_source()
    source.rows['cargoCategory'][0]['_sourceTerminals'] = ['cua_lo', 'ben_thuy']
    reader = production_reader(source)
    assert_error('SOURCE_REFERENCE_NOT_FOUND', lambda: read_bulk(reader), 503)
    assert source.scopes[-1] == {'cargoType': {'cua_lo': ['10'], 'ben_thuy': ['10']}}


def test_cargo_parent_cycle_stops_before_store_allocation():
    source = bulk_source()
    source.rows['cargoCategory'][0]['cargoParentId'] = '21'
    source.rows['cargoCategory'].append({**MASTER, 'cargoId': '21', 'cargoName': 'Parent cargo',
        'cargoTypeId': '10', 'cargoParentId': '20', '_sourceTerminals': ['cua_lo']})
    def forbidden_factory():
        pytest.fail('Cyclic dependencies must fail before allocating a store')
    reader = production_reader(source, store_factory=forbidden_factory)
    assert_error('SOURCE_DATA_NOT_READY', lambda: read_bulk(reader), 503)
    assert len(source.calls) == 3


def test_reference_expansion_has_a_round_limit(monkeypatch):
    source = bulk_source()
    source.rows['cargoCategory'][0]['cargoParentId'] = '21'
    source.rows['cargoCategory'].extend({**MASTER, 'cargoId': str(identity), 'cargoName': 'Parent cargo',
        'cargoTypeId': '10', 'cargoParentId': str(identity + 1), '_sourceTerminals': ['cua_lo']}
        for identity in range(21, 30))
    monkeypatch.setattr('backend.corporate_api.live.MAX_REFERENCE_ROUNDS', 2)
    assert_error('SOURCE_DATA_NOT_READY', lambda: read_bulk(production_reader(source)), 503)
    assert len(source.calls) == 3  # one production read and two bounded catalog rounds


def test_standalone_catalog_reads_are_not_narrowed_to_production_references():
    source = bulk_source()
    source.rows['cargoType'].append({**MASTER, 'cargoTypeId': '11', 'cargoTypeName': 'Unrelated type'})
    result = LiveReader(profile=PROFILE, extract_fn=source).read('cargoType', q())
    assert len(result['data']) == 2 and source.scopes == [None]


def test_failed_contract_or_not_found_read_closes_request_store(source):
    stores = []
    def factory():
        store = MemoryExportStore()
        stores.append(store)
        return store
    reader = LiveReader(profile=PROFILE, extract_fn=source, store_factory=factory)
    try:
        assert_error('NOT_FOUND', lambda: reader.read('origins', q(), identity='999'), 404)
        assert stores[-1]._closed
        source.rows['origins'][0]['originName'] = None
        assert_error('SOURCE_DATA_NOT_READY', lambda: reader.read('origins', q()), 503)
        assert stores[-1]._closed
    finally:
        reader.close()


def test_exception_details_are_never_exposed():
    def fail(*_):
        raise RuntimeError('Password=do-not-return;Server=private-host')
    reader = LiveReader(profile=PROFILE, extract_fn=fail)
    error = assert_error('SOURCE_DATA_NOT_READY', lambda: reader.read('origins', q()))
    assert 'Password' not in error.message and 'private-host' not in error.message


@pytest.mark.parametrize('code,retry_after', [
    ('SOURCE_UNAVAILABLE', 5), ('SOURCE_TIMEOUT', 5), ('SOURCE_ID_CONFLICT', None),
    ('DATE_BASIS_UNCONFIRMED', None), ('PRODUCTION_SCOPE_UNCONFIRMED', None),
    ('QUAY_METHODS_UNCONFIRMED', None), ('GATE_METHODS_UNCONFIRMED', None),
    ('CARGO_KIND_UNMAPPED', None), ('CARGO_TYPE_UNMAPPED', None),
    ('CONTAINER_SIZE_RELATION_UNCONFIRMED', None), ('CONTAINER_SIZE_UNMAPPED', None),
    ('CONTAINER_QUANTITY_UNIT_UNCONFIRMED', None), ('WEIGHT_OR_UNIT_UNAVAILABLE', None),
    ('CONTAINER_QUANTITY_UNAVAILABLE', None), ('WEIGHT_OUT_OF_RANGE', None),
    ('AGGREGATE_OUT_OF_RANGE', None),
])
@pytest.mark.parametrize('object_blocker', [False, True])
def test_known_source_failure_categories_keep_fixed_safe_messages(source, code, retry_after, object_blocker):
    secret = 'Password=never-return;Server=private-host;source-row=private-record'
    def fail(*args):
        preview = source(*args)
        preview['datasets']['origins'].update(ready=False, blockers=[
            {'code': code, 'message': secret, 'terminal': secret} if object_blocker else code])
        return preview
    reader = LiveReader(profile=PROFILE, extract_fn=fail)
    error = assert_error(code, lambda: reader.read('origins', q()), 503)
    assert error.retry_after == retry_after
    assert 'Password' not in error.message and 'private-host' not in error.message
    assert 'private-record' not in error.message


@pytest.mark.parametrize('blockers,expected', [
    (['CARGO_KIND_UNMAPPED', 'SOURCE_UNAVAILABLE', 'SOURCE_TIMEOUT'], 'SOURCE_TIMEOUT'),
    (['WEIGHT_OR_UNIT_UNAVAILABLE', 'SOURCE_ID_CONFLICT', 'SOURCE_UNAVAILABLE'], 'SOURCE_UNAVAILABLE'),
    (['DATE_BASIS_UNCONFIRMED', 'SOURCE_ID_CONFLICT'], 'SOURCE_ID_CONFLICT'),
])
def test_source_failure_priority_precedes_mapping_and_null_policy(blockers, expected):
    source = bulk_source()
    def fail(*args):
        preview = source(*args)
        preview['datasets']['bulkGateVolumesCB'].update(ready=False, blockers=blockers)
        return preview
    reader = LiveReader(profile=PRODUCTION_PROFILE, extract_fn=fail)
    try:
        assert_error(expected, lambda: reader.read(
            'bulkGateVolumesCB', q(startDate='20260901', endDate='20260930')), 503)
        assert source.calls == [('bulkGateVolumesCB',)]
    finally:
        reader.close()


def test_unknown_source_failure_never_echoes_arbitrary_code_or_message(source):
    secret = 'Password=never-return;Server=private-host'
    def fail(*args):
        preview = source(*args)
        preview['datasets']['origins'].update(ready=False, blockers=[{'code': secret, 'message': secret}])
        return preview
    reader = LiveReader(profile=PROFILE, extract_fn=fail)
    error = assert_error('SOURCE_DATA_NOT_READY', lambda: reader.read('origins', q()), 503)
    assert secret not in error.message


def test_large_result_is_bounded_before_allocating_store(source):
    source.rows['origins'][0]['originName'] = 'x' * 4096
    def forbidden_factory():
        pytest.fail('Must reject oversize result before allocating a store')
    reader = LiveReader(profile=PROFILE, extract_fn=source, store_factory=forbidden_factory, max_total_bytes=1024)
    assert_error('SOURCE_RESULT_TOO_LARGE', lambda: reader.read('origins', q()), 422)


def test_operation_calendar_filter_matches_created_or_modified_for_arbitrary_range():
    source = Source({'oprt.unitMeasurement': [
        {'reportDate': '2026-09-29', 'companyId': 'CNT', 'unitId': '1', 'unitCode': 'TAN',
         'unitName': 'Tấn', 'createdDate': '2020-01-01', 'modifiedDate': '2026-09-01'},
        {'reportDate': '2026-09-29', 'companyId': 'CNT', 'unitId': '2', 'unitCode': 'KG',
         'unitName': 'Kilogram', 'createdDate': '2020-01-01', 'modifiedDate': '2025-09-01'},
    ]})
    reader = LiveReader(profile=PROFILE, extract_fn=source)
    try:
        result = reader.read('oprt.unitMeasurement', OperationQuery(
            companyId='CNT', startDate='20260101', endDate='20260929'))
        assert [row['unitId'] for row in result['data']] == ['1']
        assert len(source.calls) == 1
    finally:
        reader.close()


def test_customer_period_uses_creation_date_and_unmapped_filters_remain_blocked():
    def customer(identity, created, modified):
        return {'reportDate': '20260929', 'customerCode': identity, 'customerNameVN': 'Native customer',
                'customerNameEN': None, 'customerTaxCode': None, 'customerPhoneNum': None,
                'customerAddress': None, 'customerEmail': None, 'isCarrier': None, 'isAgent': None,
                'customerStatus': None, 'metadata': {'isDeleted': False,
                    'createdDate': created, 'modifiedDate': modified},
                '_taxCodeMapped': False, '_customerTypeMapped': False}
    source = Source({'customers': [customer('1', '2026-01-01', None),
                                   customer('2', '2025-01-01', '2026-01-01')]})
    reader = LiveReader(profile=PROFILE, extract_fn=source)
    try:
        result = reader.read('customers', q(startDate='20260101', endDate='20260929'))
        assert [row['customerCode'] for row in result['data']] == ['1']
        assert_error('FILTER_NOT_READY', lambda: reader.read('customers', q(customerTaxCode='123')), 503)
        assert_error('FILTER_NOT_READY', lambda: reader.read('customers', q(customerType='carrier')), 503)
    finally:
        reader.close()


def test_invalid_rows_and_missing_operation_dates_fail_validation():
    source = Source({'oprt.unitMeasurement': [
        {'reportDate': '2026-09-29', 'companyId': 'CNT', 'unitId': '1', 'unitCode': 'TAN',
         'createdDate': None, 'modifiedDate': None}]})
    reader = LiveReader(profile=PROFILE, extract_fn=source)
    try:
        assert_error('SOURCE_DATA_NOT_READY', lambda: reader.read('oprt.unitMeasurement', OperationQuery(
            companyId='CNT', startDate='20260101', endDate='20260929')), 503)
    finally:
        reader.close()


def test_31_day_production_limit_is_inclusive_and_accepts_verified_empty_period():
    source = Source({'bulkGateVolumesCB': []})
    reader = LiveReader(profile=PRODUCTION_PROFILE, extract_fn=source)
    try:
        result = reader.read('bulkGateVolumesCB', q(startDate='20260701', endDate='20260731'))
        assert result['data'] == [] and result['code'] == '1'
        assert result['pagination']['total'] == 0
        assert_error('SOURCE_RANGE_TOO_LARGE', lambda: reader.read('bulkGateVolumesCB', q(
            startDate='20260701', endDate='20260801')), 422)
    finally:
        reader.close()


def test_concurrent_identical_requests_each_query_source_without_coalescing(source):
    both_entered, release = threading.Event(), threading.Event()
    lock = threading.Lock()
    started = []
    def blocked(*args):
        with lock:
            started.append(True)
            if len(started) == 2:
                both_entered.set()
        assert release.wait(5)
        return source(*args)
    reader = LiveReader(profile=PROFILE, extract_fn=blocked, wait_seconds=0)
    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(reader.read, 'origins', q())
        second = pool.submit(reader.read, 'origins', q())
        assert both_entered.wait(2)
        assert_error('SOURCE_BUSY', lambda: reader.read('origins', q()), 503)
        release.set()
        try:
            assert first.result(timeout=5)['code'] == '1'
            assert second.result(timeout=5)['code'] == '1'
            assert len(source.calls) == 2
        finally:
            reader.close()


def test_global_source_concurrency_is_bounded(source):
    entered, release = threading.Event(), threading.Event()
    def blocked(*args):
        entered.set()
        assert release.wait(5)
        return source(*args)
    reader = LiveReader(profile=PROFILE, extract_fn=blocked, max_concurrent=1, wait_seconds=0)
    with ThreadPoolExecutor(max_workers=2) as pool:
        pending = pool.submit(reader.read, 'origins', q(limit=1))
        assert entered.wait(2)
        assert_error('SOURCE_BUSY', lambda: reader.read('origins', q(limit=2)), 503)
        release.set()
        try:
            pending.result(timeout=5)
        finally:
            reader.close()


def test_selective_native_catalog_does_not_query_unrelated_source_tables():
    from backend.corporate_api.catalog_source import extract_catalogs
    calls = []
    def query(database, statement, params):
        calls.append(statement)
        if 'INFORMATION_SCHEMA' in statement:
            return [
                {'table_name': 'CargoOrigin', 'column_name': 'cargoOriginId', 'data_type': 'int'},
                {'table_name': 'CargoOrigin', 'column_name': 'cargoOriginName', 'data_type': 'nvarchar'},
                {'table_name': 'CargoOrigin', 'column_name': 'rowDeleted', 'data_type': 'bit'},
            ]
        assert '[CargoOrigin]' in statement
        return [{'cargoOriginId': 1, 'cargoOriginName': 'Native origin', 'rowDeleted': False}]
    result = extract_catalogs(query, profile={**PROFILE, 'cargo_catalog_source': 'native_groups'},
                              resources=['origins'])
    assert set(result) == {'origins'}
    assert result['origins']['ready']
    assert len(calls) == 4


def test_manage_extract_passes_only_selected_s_catalogs(monkeypatch):
    calls = []
    def catalogs(query_fn, **kwargs):
        calls.append(kwargs['resources'])
        return {'origins': {'rows': [], 'ready': True, 'blockers': []}}
    monkeypatch.setattr('backend.corporate_api.catalog_source.extract_catalogs', catalogs)
    result = extract(PROFILE, date(2026, 1, 1), date(2026, 9, 29), ['origins'])
    assert calls == [['origins']]
    assert list(result['datasets']) == ['origins']


def test_manage_extract_forwards_internal_reference_scope_without_profile_changes(monkeypatch):
    calls = []
    def catalogs(query_fn, **kwargs):
        calls.append(kwargs)
        return {'origins': {'rows': [], 'ready': True, 'blockers': []}}
    monkeypatch.setattr('backend.corporate_api.catalog_source.extract_catalogs', catalogs)
    scope = {'origins': {'cua_lo': ['1'], 'ben_thuy': []}}
    result = extract(PROFILE, date(2026, 9, 1), date(2026, 9, 30), ['origins'], reference_scope=scope)
    assert calls[0]['reference_scope'] == scope and calls[0]['profile'] == PROFILE
    assert result['profileDigest'] == profile_digest(PROFILE)


@pytest.mark.parametrize('resource', ['bulkGateVolumesCB', 'oprt.cargoDirect'])
def test_manage_extract_cannot_use_reference_scope_for_production_or_operation(resource):
    with pytest.raises(ValueError, match='only available for S catalogs'):
        extract(PROFILE, date(2026, 9, 1), date(2026, 9, 30), [resource], reference_scope={})

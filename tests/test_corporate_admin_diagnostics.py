"""Private diagnostics use only synthetic source reads and never reach public DTOs."""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from datetime import datetime, timezone
from decimal import Decimal
import importlib
import json

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

from backend.control_api import require_user
from backend.corporate_api.contracts import Query
from backend.corporate_api.diagnostics import dataset_diagnostics, sanitize_diagnostics
from backend.corporate_api.errors import CorporateError
from backend.corporate_api.live import LiveReader
from test_corporate_live import PRODUCTION_PROFILE, bulk_source
from test_corporate_live_reference_sources import ProductionSourceQuery, read


inspector = importlib.import_module('backend.corporate_api.inspector')
public = importlib.import_module('backend.corporate_api.router')
RESOURCE = 'bulkGateVolumesCB'
ADMIN = {'role': 'admin', 'terminals': ['cua_lo', 'ben_thuy'], 'must_change_password': False}
PERIOD = {'companyId': 'CNT', 'startDate': '20260901', 'endDate': '20260930'}
SECRET = 'Password=private-token;Server=private-host;private-customer-name'


def failed_dataset():
    return {'ready': False, 'rows': [],
        'blockers': ['WEIGHT_OR_UNIT_UNAVAILABLE', {'code': SECRET, 'message': SECRET}],
        'issue_counts': {'WEIGHT_OR_UNIT_UNAVAILABLE': 101, SECRET: 5},
        'issue_samples': [{'code': 'WEIGHT_OR_UNIT_UNAVAILABLE', 'terminal': 'cua_lo',
            'sourceId': index, 'date': '2026-09-16', 'cargoId': 0, 'methodId': 20,
            'quantityUnit': SECRET, 'message': SECRET, 'record': {'name': SECRET}}
            for index in range(1, 81)],
        'source_errors': [{'terminal': 'cua_lo', 'message': SECRET}], 'warnings': [SECRET]}


@pytest.fixture
def api(monkeypatch):
    monkeypatch.setenv('CORPORATE_API_ENABLED', 'true')
    monkeypatch.setenv('CORPORATE_READ_MODE', 'live')
    def forbidden(*args, **kwargs):
        pytest.fail('These diagnostics tests must not contact source SQL')
    source_sql = importlib.import_module('backend.corporate_api.sql')
    monkeypatch.setattr(source_sql, 'query_source', forbidden)
    calls = []
    state = {'dataset': failed_dataset()}
    def extract(profile, start, end, resources, **kwargs):
        calls.append(tuple(resources))
        return {'sourceReadAt': datetime.now(timezone.utc).isoformat(),
                'datasets': {name: deepcopy(state['dataset']) for name in resources}}
    reader = LiveReader(profile=PRODUCTION_PROFILE, extract_fn=extract)
    app = FastAPI()
    app.state.corporate_exports = reader
    app.include_router(inspector.router)
    app.include_router(public.router)
    app.dependency_overrides[require_user] = lambda: dict(ADMIN)
    app.dependency_overrides[public.principal] = lambda: {
        'enabled': True, 'company_ids': ['CNT'], 'resources': [RESOURCE]}
    with TestClient(app) as client:
        yield client, app, calls, state
    reader.close()


def test_public_response_is_unchanged_and_admin_gets_bounded_safe_same_read_diagnostics(api):
    client, _, calls, _ = api
    external = client.get('/api/' + RESOURCE, params=PERIOD)
    assert external.status_code == 503
    assert set(external.json()) == {'data', 'code', 'message'}
    assert 'diagnostics' not in external.text and SECRET not in external.text
    internal = client.post('/api/admin/corporate-api/inspect', json={'resource': RESOURCE, 'query': PERIOD})
    assert internal.status_code == 200
    result = internal.json()
    assert result['statusCode'] == 503 and result['body'] == external.json()
    assert internal.headers['Cache-Control'] == 'no-store'
    diagnostic = result['diagnostics']
    assert diagnostic['resource'] == RESOURCE
    assert diagnostic['blockers'] == [{'code': 'WEIGHT_OR_UNIT_UNAVAILABLE', 'count': 101}]
    assert len(diagnostic['samples']) == 50 and diagnostic['samplesTruncated'] is True
    assert diagnostic['samples'][0] == {'code': 'WEIGHT_OR_UNIT_UNAVAILABLE', 'terminal': 'cua_lo',
        'sourceId': '1', 'date': '2026-09-16', 'cargoId': '0', 'methodId': '20'}
    assert SECRET not in internal.text and 'quantityUnit' not in internal.text
    assert calls == [(RESOURCE,), (RESOURCE,)]  # One source read per explicit request.


@pytest.mark.parametrize('user', [
    {**ADMIN, 'role': 'viewer'}, {**ADMIN, 'role': 'manager'},
    {**ADMIN, 'terminals': ['cua_lo']}, {**ADMIN, 'terminals': ['ben_thuy']},
])
def test_diagnostics_admin_and_all_terminal_scopes_required_before_source_read(api, user):
    client, app, calls, _ = api
    app.dependency_overrides[require_user] = lambda: user
    result = client.post('/api/admin/corporate-api/inspect', json={'resource': RESOURCE, 'query': PERIOD})
    assert result.status_code == 403 and calls == []
    assert 'diagnostics' not in result.text


@pytest.mark.parametrize('value', [None, [], {}, {'resource': []}, {'resource': SECRET},
    {'resource': RESOURCE, 'blockers': [{'code': SECRET, 'message': SECRET}]}])
def test_unknown_or_malformed_diagnostics_cannot_cross_admin_boundary(value):
    assert sanitize_diagnostics(value) is None


def test_diagnostics_revalidate_every_field_and_bound_even_hostile_adapter_values():
    dataset = failed_dataset()
    dataset['issue_counts']['WEIGHT_OR_UNIT_UNAVAILABLE'] = True
    dataset['issue_samples'] = [
        {'code': 'WEIGHT_OR_UNIT_UNAVAILABLE', 'terminal': 'ben_thuy',
         'sourceId': True, 'cargoId': -1, 'methodId': SECRET, 'date': '2026-02-30'},
        {'code': 'WEIGHT_OR_UNIT_UNAVAILABLE', 'terminal': SECRET, 'sourceId': 1},
        {'code': [SECRET], 'terminal': 'cua_lo'},
        {'code': 'WEIGHT_OR_UNIT_UNAVAILABLE', 'terminal': [], 'sourceId': 2},
    ]
    result = dataset_diagnostics(RESOURCE, dataset)
    assert result['blockers'] == [{'code': 'WEIGHT_OR_UNIT_UNAVAILABLE', 'count': None}]
    assert result['samples'] == [{'code': 'WEIGHT_OR_UNIT_UNAVAILABLE', 'terminal': 'ben_thuy'}]
    assert result['samplesTruncated'] is True and SECRET not in json.dumps(result)
    assert sanitize_diagnostics(result) == result


def test_inspector_sanitizes_attached_context_again(api):
    client, app, _, _ = api
    class InvalidContext:
        def read(self, *args, **kwargs):
            raise CorporateError(503, 'SOURCE_DATA_NOT_READY', 'Source not ready', diagnostics={
                'resource': RESOURCE, 'blockers': [{'code': 'SOURCE_DATA', 'count': 1, 'message': SECRET}],
                'samples': [{'code': 'SOURCE_DATA', 'terminal': 'cua_lo', 'sourceId': SECRET}],
                'raw': SECRET})
    app.state.corporate_exports = InvalidContext()
    result = client.post('/api/admin/corporate-api/inspect', json={'resource': RESOURCE, 'query': PERIOD})
    assert result.json()['diagnostics']['samples'] == [{'code': 'SOURCE_DATA', 'terminal': 'cua_lo'}]
    assert SECRET not in result.text


def test_adapter_missing_reference_identifies_terminal_but_does_not_infer_missing_ids():
    source = ProductionSourceQuery()
    source.data['SmartTOS']['Vessel'] = []
    with pytest.raises(CorporateError) as caught:
        read(source)
    result = caught.value.diagnostics
    assert caught.value.code == 'SOURCE_REFERENCE_NOT_FOUND'
    assert result == {'resource': 'shipDetails',
        'blockers': [{'code': 'SOURCE_REFERENCE_NOT_FOUND', 'count': None}],
        'samples': [{'code': 'SOURCE_REFERENCE_NOT_FOUND', 'terminal': 'cua_lo'}],
        'samplesTruncated': False}


def test_membership_check_reports_exact_missing_reference_in_actual_source():
    source = bulk_source()
    source.rows['cargoType'][0]['_sourceTerminals'] = ['ben_thuy']
    profile = {**PRODUCTION_PROFILE, 'accepted_null_fields': {RESOURCE: ['bulkOriginId', 'customerCode']}}
    reader = LiveReader(profile=profile, extract_fn=source)
    with pytest.raises(CorporateError) as caught:
        reader.read(RESOURCE, Query(**PERIOD))
    assert caught.value.diagnostics == {'resource': 'cargoType',
        'blockers': [{'code': 'SOURCE_REFERENCE_NOT_FOUND', 'count': 1}],
        'samples': [{'code': 'SOURCE_REFERENCE_NOT_FOUND', 'terminal': 'cua_lo', 'referenceId': '10'}],
        'samplesTruncated': False}


def test_known_units_and_original_finite_measures_are_safe_without_assuming_tonnes():
    dataset = failed_dataset()
    dataset['issue_samples'] = [{**dataset['issue_samples'][0],
        'weightUnit': 'GÀU', 'quantityUnit': 'cont', 'nativeWeight': Decimal('500.000'),
        'quantity': Decimal('2.50'), 'tonneFactor': SECRET}]
    sample = dataset_diagnostics(RESOURCE, dataset)['samples'][0]
    assert sample['weightUnit'] == 'GÀU' and sample['quantityUnit'] == 'CONT'
    assert sample['nativeWeight'] == '500' and sample['quantity'] == '2.5'
    assert 'tonneFactor' not in sample


@pytest.mark.parametrize('value', [True, None, SECRET, 'NaN', 'sNaN', 'Infinity',
    '-1', '1e16', '1e-999999', '9' * 100, [], {}])
def test_measurements_never_pass_invalid_or_unbounded_values(value):
    dataset = failed_dataset()
    dataset['issue_samples'] = [{**dataset['issue_samples'][0], 'nativeWeight': value, 'quantity': value,
                                'weightUnit': SECRET, 'quantityUnit': 'unmapped-unit'}]
    sample = dataset_diagnostics(RESOURCE, dataset)['samples'][0]
    assert not {'nativeWeight', 'quantity', 'weightUnit', 'quantityUnit'} & set(sample)


def test_catalog_conflict_keeps_ids_and_count_but_no_business_values():
    source = ProductionSourceQuery()
    source.facts['SmartTOS_BenThuy'] = deepcopy(source.facts['SmartTOS'])
    source.data['SmartTOS_BenThuy']['Vessel'][0]['vesselName'] = SECRET
    with pytest.raises(CorporateError) as caught:
        read(source)
    diagnostic = caught.value.diagnostics
    assert diagnostic['resource'] == 'shipDetails'
    assert diagnostic['conflictIds'] == ['10'] and diagnostic['conflictCount'] == 1
    assert SECRET not in json.dumps(diagnostic)


def test_concurrent_reads_do_not_share_diagnostics_or_leave_reader_state():
    def extract(profile, start, end, resources):
        dataset = failed_dataset()
        dataset['issue_samples'] = [{**dataset['issue_samples'][0], 'sourceId': start.day}]
        return {'datasets': {RESOURCE: dataset}}
    reader = LiveReader(profile=PRODUCTION_PROFILE, extract_fn=extract)
    def invoke(day):
        with pytest.raises(CorporateError) as caught:
            reader.read(RESOURCE, Query(companyId='CNT', startDate=f'202609{day:02d}', endDate=f'202609{day:02d}'))
        return caught.value.diagnostics
    with ThreadPoolExecutor(max_workers=2) as pool:
        first, second = pool.map(invoke, [1, 2])
    assert first['samples'][0]['sourceId'] == '1' and second['samples'][0]['sourceId'] == '2'
    assert not any('diagnostic' in name for name in vars(reader))

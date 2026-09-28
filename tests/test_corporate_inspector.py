"""Internal inspection uses dashboard permissions and the real immutable reader.

All rows and credentials here are synthetic, and no SQL Server is contacted.
"""
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
import importlib
import sqlite3

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

from backend.control_api import require_user
from backend.control_store import ControlStore
from backend.corporate_api.auth import MachineStore
from backend.corporate_api.contracts import PRODUCTION
from backend.corporate_api.operation_contracts import OPERATION_MODELS, OPERATION_PATHS
from backend.corporate_api.registry import MODELS
from backend.corporate_api.store import ExportStore
from test_corporate_api import fixture_rows
from test_corporate_operation_api import fixture_row

inspector = importlib.import_module('backend.corporate_api.inspector')
public = importlib.import_module('backend.corporate_api.router')
CATALOG = '/api/admin/corporate-api/catalog'
INSPECT = '/api/admin/corporate-api/inspect'
ADMIN = {'role': 'admin', 'terminals': ['cua_lo', 'ben_thuy'], 'must_change_password': False}


def no_access(*args, **kwargs):
    raise AssertionError('No source SQL, machine credentials, or premature export access permitted')


def query_for(resource, **changes):
    result = {}
    if resource in PRODUCTION or resource in OPERATION_MODELS:
        result.update(startDate='20260901', endDate='20260930')
    return {**result, **changes}


@pytest.fixture
def api(tmp_path, monkeypatch):
    monkeypatch.setenv('CORPORATE_API_ENABLED', 'true')
    monkeypatch.setenv('CORPORATE_MAX_SOURCE_AGE_SECONDS', '86400')
    monkeypatch.setattr(inspector, 'default_exports', no_access)
    monkeypatch.setattr(public, 'default_auth', no_access)
    source_sql = importlib.import_module('backend.corporate_api.sql')
    monkeypatch.setattr(source_sql, 'query_source', no_access)
    monkeypatch.setattr(source_sql, 'describe_table', no_access)
    store = ExportStore(tmp_path / 'exports.sqlite3')
    rows = {**fixture_rows(), **{resource: [fixture_row(resource)] for resource in OPERATION_MODELS}}
    rows['oprt.vesselType'] = [fixture_row('oprt.vesselType', index) for index in (1, 2, 3)]
    now = datetime.now(timezone.utc).isoformat()
    with store.atomic_publication():
        for resource, items in rows.items():
            store.publish(resource, 'CNT', items, read_at=now, rule_version='inspector-synthetic-v1',
                          coverage=[['20260901', '20260930']] if resource in PRODUCTION else None,
                          warnings=['PRIVATE-SYNTHETIC-WARNING'])
    app = FastAPI()
    app.include_router(inspector.router)
    app.include_router(public.router)
    app.state.corporate_exports = store
    app.dependency_overrides[require_user] = lambda: dict(ADMIN)
    app.dependency_overrides[public.principal] = lambda: {
        'enabled': True, 'company_ids': ['CNT'], 'resources': list(MODELS)}
    with TestClient(app) as client:
        yield {'client': client, 'app': app, 'store': store, 'rows': rows}


def inspect(api, resource, **query):
    response = api['client'].post(INSPECT, json={'resource': resource, 'query': query_for(resource, **query)})
    assert response.status_code == 200, response.text
    return response.json()


def test_catalog_contains_only_current_cnt_metadata_and_all_32_resources(api):
    store = api['store']
    # Other companies and unrelated internal dataset names must never appear.
    with store.db() as db:
        db.execute("UPDATE export_versions SET company_id='OTHER' WHERE resource='origins'")
        db.execute("UPDATE export_versions SET resource='private-internal-export' WHERE resource='class'")
    value = api['client'].get(CATALOG)
    assert value.status_code == 200
    result = value.json()
    assert set(result) == {'enabled', 'companyId', 'resources', 'mode'}
    assert result['enabled'] is True and result['companyId'] == 'CNT' and result['mode'] == 'internal'
    resources = {row['id']: row for row in result['resources']}
    assert set(resources) == set(MODELS) == set(inspector.LABELS)
    assert len(resources) == 32
    assert resources['origins']['status'] == resources['class']['status'] == 'not_published'
    assert resources['origins']['rowCount'] is None and resources['origins']['snapshotId'] is None
    assert resources['bulkGateVolumesCB']['coverage'] == [['20260901', '20260930']]
    assert resources['oprt.vesselType']['rowCount'] == 3
    assert resources['oprt.vesselType']['path'] == OPERATION_PATHS['oprt.vesselType']
    assert {row['group'] for row in resources.values()} == {'production', 'catalog_s', 'operations'}
    assert 'PRIVATE-SYNTHETIC-WARNING' not in value.text and 'OTHER' not in value.text
    assert 'payload' not in value.text and 'rule_version' not in value.text
    for row in resources.values():
        assert set(row) == {'id', 'label', 'group', 'path', 'method', 'filters', 'status',
                            'rowCount', 'sourceReadAt', 'coverage', 'snapshotId'}
        assert row['method'] == 'GET'


def test_catalog_only_queries_metadata_without_payload_or_warnings(api, monkeypatch):
    queries = []
    original = api['store'].db

    @contextmanager
    def trace_db():
        with original() as db:
            db.set_trace_callback(queries.append)
            yield db

    monkeypatch.setattr(api['store'], 'db', trace_db)
    assert api['client'].get(CATALOG).status_code == 200
    selects = [statement.lower() for statement in queries if statement.lstrip().upper().startswith('SELECT')]
    assert len(selects) == 1 and 'company_id=' in selects[0]
    assert 'payload' not in selects[0] and 'warnings' not in selects[0]


def test_metadata_filters_match_resource_contract(api):
    resources = {row['id']: row for row in api['client'].get(CATALOG).json()['resources']}
    for resource in PRODUCTION | set(OPERATION_MODELS):
        dates = [item for item in resources[resource]['filters'] if item['type'] == 'date']
        assert {item['name'] for item in dates} == {'startDate', 'endDate'}
        assert all(item['required'] for item in dates)
    assert not any(item['required'] for item in resources['customers']['filters'])
    assert {item['name'] for item in resources['customers']['filters']} == {
        'startDate', 'endDate', 'customerTaxCode', 'customerType'}
    assert resources['shipDetails']['filters'] == []


@pytest.mark.parametrize('resource', sorted(MODELS))
def test_internal_result_matches_public_reader_for_every_resource(api, resource):
    result = inspect(api, resource)
    path = OPERATION_PATHS.get(resource, '/api/' + resource)
    external = api['client'].get(path, params={'companyId': 'CNT', **query_for(resource)})
    assert external.status_code == result['statusCode'] == 200
    assert result['body'] == external.json()
    assert set(result['body']) == {'data', 'code', 'message'}
    assert result['requestQuery'] == {'companyId': 'CNT', 'page': 1, 'limit': 20, **query_for(resource)}
    assert result['path'] == path and result['method'] == 'GET'
    assert result['durationMs'] >= 0
    assert set(result) == {'resource', 'path', 'method', 'requestQuery', 'statusCode', 'durationMs', 'headers', 'body'}
    for name, value in result['headers'].items():
        assert external.headers[name] == value


@pytest.mark.parametrize('scenario', ['success', 'stale', 'missing_period', 'pagination', 'snapshot_required'])
def test_inspector_parity_with_real_machine_login_and_public_auth(api, tmp_path, scenario):
    machines = MachineStore(tmp_path / 'machines.sqlite3')
    password = 'Synthetic-inspector-machine-password-2026'
    machines.create_client('inspector-parity', password, company_ids=['CNT'],
                           resources=['origins', 'bulkGateVolumesCB', 'oprt.vesselType'])
    api['app'].state.corporate_auth = machines
    del api['app'].dependency_overrides[public.principal]
    login = api['client'].post('/api/login', json={'Username': 'inspector-parity', 'Password': password})
    assert login.status_code == 200
    headers = {'Authorization': 'Bearer ' + login.json()['accessToken']}
    resource, query = 'origins', {}
    if scenario == 'stale':
        with api['store'].db() as db:
            db.execute("UPDATE export_versions SET read_at=? WHERE resource='origins'",
                       ((datetime.now(timezone.utc) - timedelta(days=3)).isoformat(),))
    elif scenario == 'missing_period':
        resource = 'bulkGateVolumesCB'
    elif scenario in {'pagination', 'snapshot_required'}:
        resource = 'oprt.vesselType'
        query = query_for(resource, limit=1, page=2)
        if scenario == 'pagination':
            first = api['client'].get(OPERATION_PATHS[resource], headers=headers,
                                      params={'companyId': 'CNT', **query_for(resource, limit=1)})
            assert first.status_code == 200
            query['snapshotId'] = first.headers['X-Snapshot-Id']
    result = api['client'].post(INSPECT, json={'resource': resource, 'query': query}).json()
    external = api['client'].get(OPERATION_PATHS.get(resource, '/api/' + resource),
                                headers=headers, params={'companyId': 'CNT', **query})
    assert result['statusCode'] == external.status_code
    assert result['body'] == external.json()
    assert result['statusCode'] == {'success': 200, 'stale': 503, 'missing_period': 422,
                                    'pagination': 200, 'snapshot_required': 409}[scenario]
    for name, value in result['headers'].items():
        assert external.headers[name] == value


def test_internal_disabled_mode_does_not_enable_or_claim_public_api(api, monkeypatch):
    monkeypatch.setenv('CORPORATE_API_ENABLED', 'false')
    catalog = api['client'].get(CATALOG).json()
    assert catalog['enabled'] is False and catalog['mode'] == 'internal'
    assert inspect(api, 'origins')['statusCode'] == 200
    external = api['client'].get('/api/origins', params={'companyId': 'CNT'})
    assert external.status_code == 503 and external.headers['X-Error-Code'] == 'CORPORATE_API_DISABLED'


@pytest.mark.parametrize('role,terminals', [
    ('viewer', ['cua_lo', 'ben_thuy']), ('manager', ['cua_lo', 'ben_thuy']),
    ('admin', ['cua_lo']), ('admin', ['ben_thuy']), ('admin', []),
])
def test_insufficient_dashboard_permissions_never_open_export_store(api, monkeypatch, role, terminals):
    api['app'].dependency_overrides[require_user] = lambda: {**ADMIN, 'role': role, 'terminals': terminals}
    monkeypatch.setattr(inspector, '_exports', no_access)
    assert api['client'].get(CATALOG).status_code == 403
    assert api['client'].post(INSPECT, json={'resource': 'origins'}).status_code == 403


def test_dashboard_session_is_required_and_temporary_password_cannot_access(tmp_path, monkeypatch):
    control = ControlStore(tmp_path / 'control.sqlite3')
    created = control.bootstrap_admin()
    session = control.login(created['user']['username'], created['temporary_password'])
    app = FastAPI()
    app.state.control_store = control
    app.include_router(inspector.router)
    monkeypatch.setattr(inspector, '_exports', no_access)
    with TestClient(app) as client:
        for path, method, kwargs in [(CATALOG, client.get, {}), (INSPECT, client.post, {'json': {'resource': 'origins'}})]:
            assert method(path, **kwargs).status_code == 401
            assert method(path, headers={'Authorization': 'Bearer machine-looking-token'}, **kwargs).status_code == 401
            forced = method(path, headers={'Authorization': 'Bearer ' + session['token']}, **kwargs)
            assert forced.status_code == 403 and forced.json()['detail']['code'] == 'PASSWORD_CHANGE_REQUIRED'


def test_real_dashboard_admin_session_authorizes_internal_reader(tmp_path, monkeypatch):
    control = ControlStore(tmp_path / 'control.sqlite3')
    created = control.bootstrap_admin()
    session = control.login(created['user']['username'], created['temporary_password'])
    changed = control.change_password(session['token'], created['temporary_password'], 'Synthetic-inspector-password-2026')
    app = FastAPI()
    app.state.control_store = control
    app.state.corporate_exports = ExportStore(tmp_path / 'exports.sqlite3')
    app.include_router(inspector.router)
    monkeypatch.setattr(inspector, 'default_exports', no_access)
    monkeypatch.setattr(public, 'default_auth', no_access)
    with TestClient(app) as client:
        headers = {'Authorization': 'Bearer ' + changed['token']}
        assert client.get(CATALOG, headers=headers).status_code == 200
        response = client.post(INSPECT, headers=headers, json={'resource': 'origins'})
        assert response.status_code == 200
        assert response.json()['headers']['X-Error-Code'] == 'DATASET_NOT_READY'


@pytest.mark.parametrize('resource,query', [
    ('origins', {'url': 'https://untrusted.invalid/?password=DO-NOT-ECHO'}),
    ('origins', {'Authorization': 'DO-NOT-ECHO'}),
    ('origins', {'limit': 101}), ('origins', {'page': 0}),
    ('origins', {'snapshotId': '../exports.sqlite3'}),
    ('contQuayVolumesCB', {'startDate': '20260230', 'endDate': '20260930'}),
    ('oprt.berths', {}), ('oprt.berths', {'startDate': '19691231', 'endDate': '20260930'}),
])
def test_invalid_query_is_inner_422_without_store_access_or_raw_input(api, monkeypatch, resource, query):
    monkeypatch.setattr(inspector, '_exports', no_access)
    response = api['client'].post(INSPECT, json={'resource': resource, 'query': query})
    assert response.status_code == 200
    result = response.json()
    assert result['statusCode'] == 422 and result['headers']['X-Error-Code'] == 'INVALID_REQUEST'
    assert set(result['body']) == {'data', 'code', 'message'}
    assert 'DO-NOT-ECHO' not in response.text
    assert result['requestQuery'] == {'companyId': 'CNT'}


@pytest.mark.parametrize('resource,query,code', [
    ('origins', {'startDate': '20260901', 'endDate': '20260930'}, 'UNSUPPORTED_FILTER'),
    ('contGateVolumesCB', {}, 'PERIOD_REQUIRED'),
    ('customers', {'startDate': '20260901'}, 'INVALID_PERIOD'),
    ('oprt.berths', {'startDate': '20260930', 'endDate': '20260901'}, 'INVALID_PERIOD'),
])
def test_resource_specific_query_validation_runs_before_store_access(api, monkeypatch, resource, query, code):
    monkeypatch.setattr(inspector, '_exports', no_access)
    response = api['client'].post(INSPECT, json={'resource': resource, 'query': query}).json()
    assert response['statusCode'] == 422 and response['headers']['X-Error-Code'] == code


def test_other_companies_and_arbitrary_resources_never_reach_store(api, monkeypatch):
    monkeypatch.setattr(inspector, '_exports', no_access)
    wrong_company = inspect(api, 'origins', companyId='OTHER')
    assert wrong_company['statusCode'] == 403
    unknown = inspect(api, 'https://not-a-server.invalid/')
    assert unknown['statusCode'] == 422 and unknown['path'] is None
    extra_body = api['client'].post(INSPECT, json={'resource': 'origins', 'password': 'DO-NOT-ECHO'})
    assert extra_body.status_code == 422 and 'DO-NOT-ECHO' not in extra_body.text


def test_inspection_preserves_pinned_pagination_and_snapshot_errors(api):
    first = inspect(api, 'oprt.vesselType', limit=1)
    snapshot = first['headers']['X-Snapshot-Id']
    assert first['headers']['X-Total-Count'] == first['headers']['X-Total-Pages'] == '3'
    assert first['headers']['X-Has-Next'] == 'true'
    second = inspect(api, 'oprt.vesselType', limit=1, page=2, snapshotId=snapshot)
    assert second['statusCode'] == 200 and second['body']['data'][0]['vesselTypeId'] == '2'
    missing = inspect(api, 'oprt.vesselType', limit=1, page=2)
    assert missing['statusCode'] == 409 and missing['headers']['X-Error-Code'] == 'SNAPSHOT_REQUIRED'
    wrong = inspect(api, 'oprt.berths', snapshotId=snapshot)
    assert wrong['statusCode'] == 410 and wrong['headers']['X-Error-Code'] == 'SNAPSHOT_EXPIRED'


@pytest.mark.parametrize('mode,resource,code', [
    ('stale', 'origins', 'DATASET_STALE'),
    ('dependency', 'oprt.portOpStaff', 'REFERENCE_DATASET_STALE'),
    ('coverage', 'bulkGateVolumesCB', 'PERIOD_NOT_READY'),
    ('missing_dates', 'oprt.berths', 'FILTER_NOT_READY'),
])
def test_inspector_does_not_bypass_freshness_coverage_or_source_date_gates(api, mode, resource, code):
    with api['store'].db() as db:
        if mode in {'stale', 'dependency'}:
            target = 'origins' if mode == 'stale' else 'oprt.portOpTeam'
            db.execute('UPDATE export_versions SET read_at=? WHERE resource=?',
                       ((datetime.now(timezone.utc) - timedelta(days=3)).isoformat(), target))
        elif mode == 'missing_dates':
            db.execute('UPDATE export_rows SET created_date=NULL,modified_date=NULL WHERE snapshot_id='
                       '(SELECT snapshot_id FROM export_versions WHERE resource=?)', (resource,))
    query = {'startDate': '20260801', 'endDate': '20260930'} if mode == 'coverage' else {}
    result = inspect(api, resource, **query)
    assert result['statusCode'] == 503 and result['headers']['X-Error-Code'] == code
    assert result['body']['data'] == []
    # Publication metadata does not falsely promise current or usable data.
    metadata = {row['id']: row for row in api['client'].get(CATALOG).json()['resources']}
    assert metadata[resource]['status'] == 'published'


def test_storage_failures_are_sanitized_for_both_endpoints(api, monkeypatch):
    def unavailable(*args, **kwargs):
        raise sqlite3.OperationalError('PRIVATE DATABASE PATH / token DO-NOT-ECHO')

    monkeypatch.setattr(inspector, '_exports', unavailable)
    response = api['client'].post(INSPECT, json={'resource': 'origins'})
    assert response.status_code == 200 and response.json()['statusCode'] == 503
    assert response.json()['headers']['X-Error-Code'] == 'STORAGE_UNAVAILABLE'
    assert response.json()['headers']['Retry-After'] == '30'
    assert 'DO-NOT-ECHO' not in response.text
    catalog = api['client'].get(CATALOG)
    assert catalog.status_code == 503 and catalog.headers['X-Error-Code'] == 'STORAGE_UNAVAILABLE'
    assert 'DO-NOT-ECHO' not in catalog.text

"""Live routing queries fresh mock source data with real auth/DTOs, without caching."""
from datetime import datetime, timezone
import importlib
import sqlite3

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

from backend.control_api import require_user
from backend.corporate_api.auth import MachineStore
from backend.corporate_api.memory_store import MemoryExportStore
from backend.corporate_api.live import LiveReader
from backend.corporate_api.manage_exports import FORMAT, profile_digest
from backend.corporate_api.contracts import Query
from test_corporate_api import fixture_rows

routes = importlib.import_module('backend.corporate_api.router')
inspector = importlib.import_module('backend.corporate_api.inspector')
source = importlib.import_module('backend.corporate_api.sql')


def test_memory_store_never_creates_export_files_and_rolls_back(tmp_path, monkeypatch):
    monkeypatch.setenv('CORPORATE_EXPORT_PATH', str(tmp_path / 'must-not-exist.sqlite3'))
    store = MemoryExportStore()
    try:
        with pytest.raises(RuntimeError):
            with store.atomic_publication():
                store.publish('origins', 'CNT', fixture_rows()['origins'],
                              read_at=datetime.now(timezone.utc).isoformat(), rule_version='synthetic')
                raise RuntimeError('rollback')
        assert store.describe() == []
        store.publish('origins', 'CNT', fixture_rows()['origins'],
                      read_at=datetime.now(timezone.utc).isoformat(), rule_version='synthetic')
        assert len(store.read('origins', Query(companyId='CNT'))['data']) == 1
        assert store.size_bytes() > 0
        with store.db() as db:
            assert db.execute('PRAGMA database_list').fetchone()['file'] == ''
            assert db.execute('PRAGMA temp_store').fetchone()[0] == 2
        assert list(tmp_path.iterdir()) == []
    finally:
        store.close()
    store.close()
    with pytest.raises(sqlite3.ProgrammingError):
        store.describe()


@pytest.fixture
def live_api(tmp_path, monkeypatch):
    monkeypatch.setenv('CORPORATE_API_ENABLED', 'true')
    monkeypatch.setenv('CORPORATE_READ_MODE', 'live')
    auth = MachineStore(tmp_path / 'auth.sqlite3')
    auth.create_client('synthetic-reader', 'synthetic-api-password', company_ids=['CNT'], resources=['origins'])
    calls = []
    profile = {'approved': True, 'company_id': 'CNT', 'terminals': ['cua_lo', 'ben_thuy']}
    def extract(config, start, end, resources):
        calls.extend(resources)
        row = fixture_rows()['origins'][0]
        return {'format': FORMAT, 'companyId': 'CNT', 'profileDigest': profile_digest(config),
                'sourceReadAt': datetime.now(timezone.utc).isoformat(),
                'startDate': start.strftime('%Y%m%d'), 'endDate': end.strftime('%Y%m%d'),
                'datasets': {'origins': {'ready': True, 'blockers': [], 'rows': [
                    {**row, 'originId': str(index), 'originName': f'Read {len(calls)} item {index}'}
                    for index in (1, 2, 3)]}}}
    reader = LiveReader(profile=profile, extract_fn=extract)
    monkeypatch.setattr(routes, 'default_live', lambda: reader)
    monkeypatch.setattr(inspector, 'default_live', lambda: reader)
    def forbidden():
        raise AssertionError('No persistent export reader allowed in live mode')
    monkeypatch.setattr(routes, 'default_exports', forbidden)
    monkeypatch.setattr(inspector, 'default_exports', forbidden)
    app = FastAPI()
    app.state.corporate_auth = auth
    app.include_router(routes.router)
    app.include_router(inspector.router)
    app.dependency_overrides[require_user] = lambda: {
        'role': 'admin', 'terminals': ['cua_lo', 'ben_thuy'], 'must_change_password': False}
    with TestClient(app) as client:
        yield client, calls
    reader.close()


def test_live_auth_and_grants_before_source_read(live_api):
    client, calls = live_api
    assert client.get('/api/origins?companyId=CNT').status_code == 401
    login = client.post('/api/login', json={'Username': 'synthetic-reader', 'Password': 'synthetic-api-password'})
    assert login.status_code == 200
    headers = {'Authorization': 'Bearer ' + login.json()['accessToken']}
    assert client.get('/api/shipDetails?companyId=CNT', headers=headers).status_code == 403
    assert calls == []
    response = client.get('/api/origins?companyId=CNT', headers=headers)
    assert response.status_code == 200
    assert set(response.json()) == {'data', 'code', 'message'}
    assert 'X-Snapshot-Id' not in response.headers
    assert calls == ['origins']
    assert client.post('/api/logout', headers=headers).status_code == 200
    assert client.get('/api/origins?companyId=CNT', headers=headers).status_code == 401
    assert calls == ['origins']


def test_each_live_http_request_and_page_reads_current_source(live_api):
    client, calls = live_api
    login = client.post('/api/login', json={'Username': 'synthetic-reader', 'Password': 'synthetic-api-password'})
    headers = {'Authorization': 'Bearer ' + login.json()['accessToken']}
    url = '/api/origins?companyId=CNT&limit=1'
    first = client.get(url, headers=headers)
    repeated = client.get(url, headers=headers)
    second_page = client.get(url + '&page=2', headers=headers)
    assert first.status_code == repeated.status_code == second_page.status_code == 200
    assert first.json()['data'][0]['originName'] == 'Read 1 item 1'
    assert repeated.json()['data'][0]['originName'] == 'Read 2 item 1'
    assert second_page.json()['data'][0]['originName'] == 'Read 3 item 2'
    assert second_page.headers['X-Page'] == '2'
    assert second_page.headers['X-Total-Count'] == '3'
    assert second_page.headers['X-Has-Next'] == 'true'
    assert all('X-Snapshot-Id' not in result.headers for result in [first, repeated, second_page])
    assert calls == ['origins'] * 3
    old_snapshot = client.get(url + '&snapshotId=' + 'a' * 32, headers=headers)
    assert old_snapshot.status_code == 422
    assert old_snapshot.headers['X-Error-Code'] == 'SNAPSHOT_NOT_SUPPORTED'
    assert calls == ['origins'] * 3


def test_live_inspector_catalog_does_not_read_source_and_internal_mode_shares_reader(live_api):
    client, calls = live_api
    catalog = client.get('/api/admin/corporate-api/catalog')
    assert catalog.status_code == 200
    assert catalog.json()['readMode'] == 'live'
    assert len(catalog.json()['resources']) == 32
    assert {row['status'] for row in catalog.json()['resources']} == {'live'}
    assert calls == []
    response = client.post('/api/admin/corporate-api/inspect', json={'resource': 'origins', 'query': {}})
    assert response.status_code == 200 and response.json()['statusCode'] == 200
    assert calls == ['origins']


def test_source_deadline_clips_driver_timeout_without_leaking_into_other_reads(monkeypatch):
    class Connection:
        timeout = 20
        closed = False
        def close(self): self.closed = True
    connection = Connection()
    now = [100.0]
    monkeypatch.setattr(source, 'perf_counter', lambda: now[0])
    monkeypatch.setattr(source, 'get_db_connection', lambda _, **kwargs: connection)
    with source.source_read_budget(5):
        assert source._connect('SmartTOS').timeout == 5
        now[0] = 105
        with pytest.raises(source.CorporateError) as error:
            source._connect('SmartTOS')
        assert error.value.code == 'SOURCE_TIMEOUT'
    assert source._remaining() is None


def test_source_connection_closed_if_connect_exhausts_budget(monkeypatch):
    class Connection:
        timeout = 20
        closed = False
        def close(self): self.closed = True
    connection = Connection()
    now = [100.0]
    monkeypatch.setattr(source, 'perf_counter', lambda: now[0])
    def connect(_, **kwargs):
        now[0] = 106
        return connection
    monkeypatch.setattr(source, 'get_db_connection', connect)
    with source.source_read_budget(5), pytest.raises(source.CorporateError):
        source._connect('SmartTOS')
    assert connection.closed


def test_source_fetch_budget_discards_partial_rows_and_closes(monkeypatch):
    now = [100.0]
    class Cursor:
        description = [('value',)]
        closed = False
        calls = 0
        def execute(self, *args): pass
        def fetchmany(self, limit):
            self.calls += 1
            now[0] += 3
            return [(1,)] * limit
        def close(self): self.closed = True
    cursor = Cursor()
    class Connection:
        timeout = 20
        closed = False
        def cursor(self): return cursor
        def close(self): self.closed = True
    connection = Connection()
    monkeypatch.setattr(source, 'perf_counter', lambda: now[0])
    monkeypatch.setattr(source, 'get_db_connection', lambda _, **kwargs: connection)
    with source.source_read_budget(5), pytest.raises(source.CorporateError) as error:
        source.query_source('SmartTOS', 'SELECT value FROM dbo.CargoOrigin')
    assert error.value.code == 'SOURCE_TIMEOUT'
    assert cursor.calls == 2
    assert cursor.closed and connection.closed


def test_request_connect_limit_can_only_shorten_configured_driver_timeout(monkeypatch):
    from backend import database
    calls = []
    class Connection:
        timeout = None
    monkeypatch.setattr(database.settings, 'DB_SERVER', 'synthetic-server')
    monkeypatch.setattr(database.settings, 'DB_USERNAME', 'synthetic-user')
    monkeypatch.setattr(database.settings, 'DB_PASSWORD', 'synthetic-password')
    monkeypatch.setattr(database.settings, 'DB_CONNECT_TIMEOUT_SECONDS', 5)
    def connect(_, **kwargs):
        calls.append(kwargs['timeout'])
        return Connection()
    monkeypatch.setattr(database.pyodbc, 'connect', connect)
    database.get_db_connection('SmartTOS', connect_timeout_seconds=3)
    database.get_db_connection('SmartTOS', connect_timeout_seconds=30)
    assert calls == [3, 5]

"""API logout uses isolated state and never affects dashboard or other API sessions."""
from contextlib import closing
from datetime import datetime, timezone
import hashlib
import sqlite3

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

from backend.control_store import ControlStore
from backend.corporate_api.auth import MachineAuthError, MachineStore
from backend.corporate_api.router import router
from backend.corporate_api.store import ExportStore


PASSWORD = 'synthetic-api-logout-password'


@pytest.fixture
def api(tmp_path, monkeypatch):
    monkeypatch.setenv('CORPORATE_API_ENABLED', 'true')
    monkeypatch.setenv('DASHBOARD_STATE_PATH', str(tmp_path / 'control.sqlite3'))
    monkeypatch.setenv('CORPORATE_MAX_SOURCE_AGE_SECONDS', '86400')
    now = [2_000_000_000.0]
    machines = MachineStore(tmp_path / 'machines.sqlite3', clock=lambda: now[0])
    machines.create_client('api-reader', PASSWORD, company_ids=['CNT'], resources=['origins'])
    exports = ExportStore(tmp_path / 'exports.sqlite3')
    exports.publish('origins', 'CNT', [{
        'reportDate': '20260929', 'createdDate': None, 'modifiedDate': None,
        'originId': 'SYN-ORIGIN', 'originName': 'Synthetic origin',
    }], read_at=datetime.now(timezone.utc).isoformat(), rule_version='synthetic-logout-v1')
    app = FastAPI()
    app.include_router(router)
    app.state.corporate_auth = machines
    app.state.corporate_exports = exports
    with TestClient(app) as client:
        yield {'client': client, 'machines': machines, 'now': now, 'path': tmp_path}


def login(api):
    response = api['client'].post('/api/login', json={'Username': 'api-reader', 'Password': PASSWORD})
    assert response.status_code == 200
    return response.json()['accessToken']


def bearer(token):
    return {'Authorization': 'Bearer ' + token}


def read(api, token):
    return api['client'].get('/api/origins', params={'companyId': 'CNT'}, headers=bearer(token))


def token_state(api):
    with closing(sqlite3.connect(api['machines'].path)) as db:
        return db.execute('SELECT token_hash,revoked_at FROM machine_tokens ORDER BY token_hash').fetchall()


def test_http_logout_revokes_only_presented_token_and_keeps_other_session(api):
    first, second = login(api), login(api)
    assert read(api, first).status_code == read(api, second).status_code == 200
    response = api['client'].post('/api/logout', headers=bearer(first))
    assert response.status_code == 200
    assert response.json() == {'data': [], 'code': '1', 'message': 'Đăng xuất API thành công.'}
    assert response.headers['Cache-Control'] == 'no-store'
    assert response.headers['X-Content-Type-Options'] == 'nosniff'
    assert first not in response.text and second not in response.text
    assert read(api, first).status_code == 401
    assert read(api, second).status_code == 200
    repeat = api['client'].post('/api/logout', headers=bearer(first))
    assert repeat.status_code == 401
    states = dict(token_state(api))
    assert states == {
        hashlib.sha256(first.encode()).hexdigest(): api['now'][0],
        hashlib.sha256(second.encode()).hexdigest(): None,
    }
    assert api['machines'].authenticate(second)['enabled'] is True


@pytest.mark.parametrize('authorization', [None, 'Basic invalid', 'Bearer', 'Bearer short',
                                           'Bearer ' + 'a' * 43, 'Bearer  ' + 'a' * 43])
def test_invalid_or_missing_authorization_cannot_change_tokens(api, authorization):
    token = login(api)
    before = token_state(api)
    headers = {'Authorization': authorization} if authorization is not None else {}
    response = api['client'].post('/api/logout', headers=headers)
    assert response.status_code == 401
    assert response.json()['data'] == [] and response.json()['code'] == '0'
    assert response.headers['WWW-Authenticate'] == 'Bearer'
    assert token_state(api) == before
    assert read(api, token).status_code == 200


def test_dashboard_session_is_refused_without_revoking_either_session(api):
    machine_token = login(api)
    humans = ControlStore(api['path'] / 'control.sqlite3')
    created = humans.bootstrap_admin('synthetic-human')
    human_token = humans.login('synthetic-human', created['temporary_password'], 'synthetic-client')['token']
    before = token_state(api)
    response = api['client'].post('/api/logout', headers=bearer(human_token))
    assert response.status_code == 401
    assert token_state(api) == before
    assert humans.authenticate(human_token)['username'] == 'synthetic-human'
    assert read(api, machine_token).status_code == 200


def test_expired_token_is_not_marked_revoked(api):
    token = login(api)
    before = token_state(api)
    api['now'][0] += 28_800
    response = api['client'].post('/api/logout', headers=bearer(token))
    assert response.status_code == 401
    assert response.headers['X-Error-Code'] == 'MACHINE_TOKEN_INVALID'
    assert token_state(api) == before


def test_expiry_between_route_authentication_and_write_is_rechecked(api, monkeypatch):
    token = login(api)
    before = token_state(api)
    logout = api['machines'].logout

    def expire_then_logout(value):
        api['now'][0] += 28_800
        return logout(value)

    monkeypatch.setattr(api['machines'], 'logout', expire_then_logout)
    response = api['client'].post('/api/logout', headers=bearer(token))
    assert response.status_code == 401
    assert token_state(api) == before


def test_disabled_api_keeps_current_token_unchanged(api, monkeypatch):
    token = login(api)
    before = token_state(api)
    monkeypatch.setenv('CORPORATE_API_ENABLED', 'false')
    response = api['client'].post('/api/logout', headers=bearer(token))
    assert response.status_code == 503
    assert response.headers['X-Error-Code'] == 'CORPORATE_API_DISABLED'
    assert token_state(api) == before
    api['machines'].authenticate(token)


def test_concurrent_logout_commits_only_once(api):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier

    token, other = login(api), login(api)
    ready = Barrier(2)

    def attempt(_):
        ready.wait(timeout=5)
        try:
            api['machines'].logout(token)
            return 200
        except MachineAuthError as exc:
            return exc.status_code

    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(attempt, range(2))) == [200, 401]
    assert read(api, token).status_code == 401
    assert read(api, other).status_code == 200

"""The external corporate service exposes machine APIs without dashboard state."""
import importlib
import os
from pathlib import Path
import sqlite3
import subprocess
import sys

from fastapi.testclient import TestClient
import pytest

from backend.corporate_api.app import EXPOSED_HEADERS, create_app, settings
from backend.corporate_api.auth import MachineStore
from backend.corporate_api.errors import CorporateError


routes = importlib.import_module('backend.corporate_api.router')
PASSWORD = 'synthetic-independent-api-password'


class SyntheticExports:
    def __init__(self):
        self.calls = []

    def read(self, resource, query, **options):
        self.calls.append((resource, query, options))
        return {
            'data': [{'reportDate': '20261015', 'createdDate': None, 'modifiedDate': None,
                      'originId': '1', 'originName': 'Synthetic origin'}],
            'code': '1', 'message': 'Lấy dữ liệu thành công',
            'pagination': {'sourceReadAt': '2026-10-15T00:00:00Z', 'resultDigest': 'a' * 64,
                           'page': query.page, 'limit': query.limit, 'total': 1, 'hasNext': False},
        }


@pytest.fixture
def isolated_api(tmp_path, monkeypatch):
    monkeypatch.setenv('CORPORATE_API_ENABLED', 'true')
    monkeypatch.setenv('DASHBOARD_STATE_PATH', str(tmp_path / 'control.sqlite3'))
    auth = MachineStore(tmp_path / 'corporate.sqlite3')
    auth.create_client('isolated-reader', PASSWORD, company_ids=['CNT'], resources=['origins'])
    exports = SyntheticExports()
    application = create_app()
    application.state.corporate_auth = auth
    application.state.corporate_exports = exports
    with TestClient(application) as client:
        yield client, exports
    assert not (tmp_path / 'control.sqlite3').exists()


def login(client):
    response = client.post('/api/login', json={'Username': 'isolated-reader', 'Password': PASSWORD})
    assert response.status_code == 200
    assert set(response.json()) == {'code', 'message', 'accessToken', 'expiresIn'}
    return {'Authorization': 'Bearer ' + response.json()['accessToken']}


def test_liveness_never_opens_auth_export_or_source_state(tmp_path, monkeypatch):
    def forbidden():
        raise AssertionError('Health must not initialize state or query data')
    monkeypatch.setattr(routes, 'default_auth', forbidden)
    monkeypatch.setattr(routes, 'default_exports', forbidden)
    monkeypatch.setattr(routes, 'default_live', forbidden)
    monkeypatch.setenv('DASHBOARD_STATE_PATH', str(tmp_path / 'control.sqlite3'))
    monkeypatch.delenv('CORPORATE_API_ENABLED', raising=False)
    with TestClient(create_app()) as client:
        response = client.get('/health')
    assert response.status_code == 200
    assert response.json() == {'status': 'ok'}
    assert response.headers['Cache-Control'] == 'no-store'
    assert response.headers['X-Content-Type-Options'] == 'nosniff'
    assert list(tmp_path.iterdir()) == []


def test_runtime_import_is_independent_of_dashboard_and_opens_no_database(tmp_path):
    environment = {**os.environ, 'DASHBOARD_STATE_PATH': str(tmp_path / 'control.sqlite3')}
    script = """
import sqlite3
import sys
from unittest.mock import patch
with patch.object(sqlite3, 'connect', side_effect=AssertionError('Unexpected database initialization')):
    from corporate_api.app import app
assert not {'main', 'control_api', 'integration', 'repository', 'reporting'} & set(sys.modules)
assert 'corporate_api.sql' not in sys.modules
assert 'corporate_api.live' not in sys.modules
assert 'corporate_api.inspector' not in sys.modules
assert app.title == 'Cảng Nghệ Tĩnh - API Tổng công ty'
"""
    result = subprocess.run(
        [sys.executable, '-c', script], env=environment,
        cwd=Path(__file__).resolve().parents[1] / 'backend',
        capture_output=True, text=True, timeout=30,
    )
    assert result.returncode == 0, result.stderr
    assert list(tmp_path.iterdir()) == []


def test_login_grants_read_and_logout_use_independent_machine_auth(isolated_api):
    client, exports = isolated_api
    unauthenticated = client.get('/api/origins?companyId=CNT')
    assert unauthenticated.status_code == 401
    assert unauthenticated.headers['WWW-Authenticate'] == 'Bearer'
    headers = login(client)
    assert client.get('/api/shipDetails?companyId=CNT', headers=headers).status_code == 403
    assert exports.calls == []
    response = client.get('/api/origins?companyId=CNT', headers=headers)
    assert response.status_code == 200
    assert set(response.json()) == {'data', 'code', 'message'}
    assert len(exports.calls) == 1
    assert response.headers['X-Result-Digest'] == 'a' * 64
    assert response.headers['X-Total-Count'] == '1'
    assert response.headers['X-Has-Next'] == 'false'
    assert response.headers['Cache-Control'] == 'no-store'
    assert client.post('/api/logout', headers=headers).status_code == 200
    assert client.get('/api/origins?companyId=CNT', headers=headers).status_code == 401
    assert len(exports.calls) == 1


@pytest.mark.parametrize('method,path', [
    ('GET', '/'), ('GET', '/redoc'),
    ('GET', '/api/dashboard'), ('GET', '/api/health'), ('GET', '/api/health/live'),
    ('GET', '/api/voyages/cua_lo/1'), ('GET', '/api/plans'), ('POST', '/api/auth/login'),
    ('GET', '/api/admin/users'), ('GET', '/api/admin/corporate-api/catalog'),
    ('POST', '/api/admin/corporate-api/inspect'),
])
def test_dashboard_and_inspector_routes_are_absent(method, path):
    with TestClient(create_app()) as client:
        response = client.request(method, path)
    assert response.status_code == 404
    assert response.json() == {'data': [], 'code': '0', 'message': 'Đường dẫn API không tồn tại.'}
    assert response.headers['X-Error-Code'] == 'NOT_FOUND'
    assert response.headers['Cache-Control'] == 'no-store'
    assert response.headers['X-Content-Type-Options'] == 'nosniff'


def test_swagger_ui_uses_same_origin_schema_without_persistent_token_or_validator():
    with TestClient(create_app()) as client:
        response = client.get('/docs')
    assert response.status_code == 200
    assert response.headers['Content-Type'].startswith('text/html')
    assert "url: '/openapi.json'" in response.text
    assert '"persistAuthorization": false' in response.text
    assert '"validatorUrl": null' in response.text
    assert 'oauth2RedirectUrl' not in response.text
    assert response.headers['Cache-Control'] == 'no-store'
    assert response.headers['X-Content-Type-Options'] == 'nosniff'


def test_openapi_documents_only_corporate_routes_and_correct_machine_bearer_security(tmp_path, monkeypatch):
    def forbidden():
        raise AssertionError('Documentation must not initialize storage or query data')
    monkeypatch.setattr(routes, 'default_auth', forbidden)
    monkeypatch.setattr(routes, 'default_exports', forbidden)
    monkeypatch.setattr(routes, 'default_live', forbidden)
    monkeypatch.setenv('DASHBOARD_STATE_PATH', str(tmp_path / 'control.sqlite3'))
    with TestClient(create_app()) as client:
        response = client.get('/openapi.json')
    assert response.status_code == 200
    schema = response.json()
    assert set(schema['paths']) == {route.path for route in routes.router.routes}
    assert '/api/admin/corporate-api/catalog' not in schema['paths']
    assert '/api/dashboard' not in schema['paths']
    assert '/health' not in schema['paths']
    security = schema['components']['securitySchemes']
    assert set(security) == {'CorporateBearer'}
    assert security['CorporateBearer']['type'] == 'http'
    assert security['CorporateBearer']['scheme'] == 'bearer'
    assert schema['paths']['/api/login']['post'].get('security') is None
    for path, methods in schema['paths'].items():
        for method, operation in methods.items():
            if path != '/api/login':
                assert operation['security'] == [{'CorporateBearer': []}], (path, method)
    password_schema = schema['components']['schemas']['Login']['properties']['Password']
    assert password_schema['format'] == 'password'
    assert password_schema['writeOnly'] is True
    assert not {'default', 'example', 'examples'} & password_schema.keys()
    assert all(text not in response.text for text in [PASSWORD, 'DB_PASSWORD', 'sourceDatabase', 'sqlite3'])
    assert all(step in schema['info']['description'] for step in [
        'POST /api/login', 'accessToken', 'Authorize', 'Try it out', 'POST /api/logout',
    ])
    assert response.headers['Cache-Control'] == 'no-store'
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize('authorization,code', [
    (None, 'UNAUTHORIZED'), ('Basic synthetic', 'UNAUTHORIZED'), ('Bearer', 'UNAUTHORIZED'),
    ('Bearer ', 'UNAUTHORIZED'), ('Bearer  synthetic', 'UNAUTHORIZED'),
    ('Bearer synthetic extra', 'UNAUTHORIZED'), ('Bearer invalid-token', 'MACHINE_TOKEN_INVALID'),
])
def test_swagger_security_dependency_preserves_manual_auth_error_contract(isolated_api, authorization, code):
    client, exports = isolated_api
    response = client.get('/api/origins?companyId=CNT', headers=(
        {} if authorization is None else {'Authorization': authorization}
    ))
    assert response.status_code == 401
    assert response.headers['X-Error-Code'] == code
    assert response.headers['WWW-Authenticate'] == 'Bearer'
    assert set(response.json()) == {'data', 'code', 'message'}
    assert exports.calls == []


def test_machine_api_disabled_response_does_not_initialize_store(monkeypatch):
    monkeypatch.delenv('CORPORATE_API_ENABLED', raising=False)
    monkeypatch.setattr(routes, 'default_auth', lambda: pytest.fail('Disabled API created auth store'))
    with TestClient(create_app()) as client:
        response = client.post('/api/login', json={'Username': 'isolated-reader', 'Password': PASSWORD})
    assert response.status_code == 503
    assert response.headers['X-Error-Code'] == 'CORPORATE_API_DISABLED'
    assert set(response.json()) == {'data', 'code', 'message'}


def test_invalid_login_never_echoes_credentials(isolated_api):
    client, _ = isolated_api
    response = client.post('/api/login', json={'Username': [], 'Password': PASSWORD, 'extra': PASSWORD})
    assert response.status_code == 422
    assert response.headers['X-Error-Code'] == 'INVALID_REQUEST'
    assert PASSWORD not in response.text


def test_wrong_http_method_keeps_allow_and_safe_envelope():
    with TestClient(create_app()) as client:
        response = client.get('/api/login')
    assert response.status_code == 405
    assert response.headers['Allow'] == 'POST'
    assert response.headers['X-Error-Code'] == 'METHOD_NOT_ALLOWED'
    assert set(response.json()) == {'data', 'code', 'message'}


def test_allowed_browser_origin_can_read_pagination_and_auth_headers(isolated_api):
    client, _ = isolated_api
    headers = {**login(client), 'Origin': settings.FRONTEND_ORIGIN}
    response = client.get('/api/origins?companyId=CNT', headers=headers)
    assert response.status_code == 200
    assert response.headers['Access-Control-Allow-Origin'] == settings.FRONTEND_ORIGIN
    assert 'access-control-allow-credentials' not in response.headers
    assert set(EXPOSED_HEADERS) <= set(response.headers['Access-Control-Expose-Headers'].split(', '))
    assert 'Origin' in response.headers['Vary']


def test_unlisted_browser_origin_gets_no_cors_permission():
    with TestClient(create_app()) as client:
        response = client.get('/health', headers={'Origin': 'https://unlisted.invalid'})
    assert response.status_code == 200
    assert 'access-control-allow-origin' not in response.headers


@pytest.mark.parametrize('method,status', [('GET', 200), ('POST', 200), ('DELETE', 400), ('PATCH', 400)])
def test_cors_preflight_only_allows_corporate_methods(method, status):
    with TestClient(create_app()) as client:
        response = client.options('/api/login', headers={
            'Origin': settings.FRONTEND_ORIGIN,
            'Access-Control-Request-Method': method,
            'Access-Control-Request-Headers': 'authorization,content-type',
        })
    assert response.status_code == status
    assert response.headers['Access-Control-Allow-Origin'] == settings.FRONTEND_ORIGIN
    assert 'access-control-allow-credentials' not in response.headers


@pytest.mark.parametrize('error,status,code', [
    (CorporateError(503, 'SOURCE_TIMEOUT', 'Nguồn dữ liệu tạm thời chưa phản hồi.', retry_after=30), 503, 'SOURCE_TIMEOUT'),
    (sqlite3.OperationalError('private storage path'), 503, 'STORAGE_UNAVAILABLE'),
    (RuntimeError('private-password source connection'), 500, 'INTERNAL_ERROR'),
])
def test_errors_keep_safe_contract_cors_and_security_headers(isolated_api, error, status, code):
    client, exports = isolated_api
    def fail(*args, **kwargs):
        raise error
    exports.read = fail
    response = client.get('/api/origins?companyId=CNT', headers={
        **login(client), 'Origin': settings.FRONTEND_ORIGIN,
    })
    assert response.status_code == status
    assert response.headers['X-Error-Code'] == code
    assert response.headers['Access-Control-Allow-Origin'] == settings.FRONTEND_ORIGIN
    assert response.headers['Cache-Control'] == 'no-store'
    assert response.headers['X-Content-Type-Options'] == 'nosniff'
    assert 'private' not in response.text
    assert set(response.json()) == {'data', 'code', 'message'}
    if status == 503:
        assert response.headers['Retry-After'] == '30'

"""Workbook S query documentation must describe the request parser and wire errors."""
import importlib

from fastapi.testclient import TestClient
import pytest

from backend.corporate_api.app import create_app
from backend.corporate_api.contracts import ErrorResult, MODELS, Query
from backend.corporate_api.errors import CorporateError


routes = importlib.import_module('backend.corporate_api.router')
EXPECTED_FILTERS = {
    'contQuayVolumesCB': {'startDate', 'endDate', 'shipId', 'handlingMethodId'},
    'contGateVolumesCB': {'startDate', 'endDate', 'handlingMethodId'},
    'bulkQuayVolumesCB': {'startDate', 'endDate', 'shipId', 'handlingMethodId'},
    'bulkGateVolumesCB': {'startDate', 'endDate', 'handlingMethodId'},
    'shipDetails': set(),
    'customers': {'startDate', 'endDate', 'customerTaxCode', 'customerType'},
    'cargoType': set(),
    'cargoCategory': {'cargoTypeId'},
    'handlingMethodList': set(),
    'class': set(),
    'origins': {'origin'},
    'containerSize': {'containerSizeId'},
}


@pytest.mark.parametrize('resource', EXPECTED_FILTERS)
def test_live_openapi_only_advertises_filters_accepted_by_resource(resource, monkeypatch):
    monkeypatch.setenv('CORPORATE_READ_MODE', 'live')
    schema = create_app().openapi()
    operation = schema['paths']['/api/' + resource]['get']
    parameters = {item['name']: item for item in operation['parameters']}
    assert set(parameters) == {'companyId', 'page', 'limit'} | EXPECTED_FILTERS[resource]
    assert parameters['companyId']['required'] is True
    if resource.endswith('VolumesCB'):
        for name in ('startDate', 'endDate'):
            assert parameters[name]['required'] is True
            assert parameters[name]['schema']['type'] == 'string'
            assert parameters[name]['schema']['pattern'] == '^[0-9]{8}$'
        assert '31 ngày' in operation['description']
        assert 'tính cả hai đầu' in operation['description']
    if resource == 'customers':
        assert parameters['startDate']['required'] is False
        assert parameters['endDate']['required'] is False
    if '/api/' + resource + '/{record_id}' in schema['paths']:
        detail = schema['paths']['/api/' + resource + '/{record_id}']['get']
        assert {item['name'] for item in detail['parameters']} == set(parameters) | {'record_id'}


def test_schema_tracks_read_mode_after_import_without_mutating_published_compatibility(monkeypatch):
    application = create_app()
    monkeypatch.setenv('CORPORATE_READ_MODE', 'published')
    published = application.openapi()
    monkeypatch.setenv('CORPORATE_READ_MODE', 'live')
    live = application.openapi()
    for path, methods in live['paths'].items():
        for method, operation in methods.items():
            assert 'snapshotId' not in {item['name'] for item in operation.get('parameters', [])}
            assert 'X-Snapshot-Id' not in operation['responses']['200'].get('headers', {})
            if method == 'get':
                previous = published['paths'][path][method]
                assert 'snapshotId' in {item['name'] for item in previous['parameters']}
                assert 'X-Snapshot-Id' in previous['responses']['200']['headers']
    monkeypatch.setenv('CORPORATE_READ_MODE', 'published')
    assert application.openapi() is published


def test_openapi_errors_and_pagination_describe_actual_compact_contract(monkeypatch):
    monkeypatch.setenv('CORPORATE_READ_MODE', 'live')
    schema = create_app().openapi()
    assert set(schema['components']['schemas']['ErrorResult']['properties']) == {'data', 'code', 'message'}
    assert 'HTTPValidationError' not in schema['components']['schemas']
    for methods in schema['paths'].values():
        for operation in methods.values():
            for status in ('401', '403', '410', '422', '429', '503'):
                error = operation['responses'][status]
                assert error['content']['application/json']['schema']['$ref'].endswith('/ErrorResult')
                assert 'X-Error-Code' in error['headers']
                assert 'Cache-Control' in error['headers']
            assert 'WWW-Authenticate' in operation['responses']['401']['headers']
            assert 'Retry-After' in operation['responses']['429']['headers']
    headers = schema['paths']['/api/contQuayVolumesCB']['get']['responses']['200']['headers']
    assert {'X-Page', 'X-Limit', 'X-Has-Next', 'X-Total-Count', 'X-Source-Read-At',
            'X-Result-Digest', 'X-Corporate-Contract', 'Cache-Control'} <= headers.keys()
    assert headers['X-Has-Next']['schema']['type'] == 'boolean'
    assert 'trang 1' in headers['X-Result-Digest']['description']


class RecordingReader:
    def __init__(self):
        self.calls = []

    def read(self, resource, query, **options):
        query.check_resource(resource)
        self.calls.append((resource, query, options))
        return {'data': [], 'code': '1', 'message': 'Lấy dữ liệu thành công', 'pagination': {
            'page': query.page, 'limit': query.limit, 'total': 0, 'hasNext': False,
            'sourceReadAt': '2026-10-15T00:00:00Z', 'resultDigest': 'a' * 64}}


@pytest.fixture
def parsing_api():
    """No network, source, credentials or storage; auth is covered in auth suites."""
    reader = RecordingReader()
    application = create_app()
    application.dependency_overrides[routes.principal] = lambda: {
        'company_ids': ['CNT'], 'resources': list(MODELS), 'enabled': True}
    application.dependency_overrides[routes.get_exports] = lambda: reader
    with TestClient(application) as client:
        yield client, reader


@pytest.mark.parametrize('resource', EXPECTED_FILTERS)
def test_resource_specific_parser_preserves_query_type_and_omitted_fields(resource, parsing_api):
    client, reader = parsing_api
    params = {'companyId': 'CNT'}
    if resource.endswith('VolumesCB'):
        params.update(startDate='20260901', endDate='20260930')
    response = client.get('/api/' + resource, params=params)
    assert response.status_code == 200
    assert isinstance(reader.calls[0][1], Query)
    assert reader.calls[0][1].model_fields_set == set(params) | {'page', 'limit'}
    assert set(response.json()) == {'data', 'code', 'message'}
    assert response.headers['X-Total-Count'] == '0'
    assert response.headers['X-Has-Next'] == 'false'
    assert response.headers['X-Result-Digest'] == 'a' * 64


@pytest.mark.parametrize('resource,params', [
    ('contQuayVolumesCB', {}),
    ('contQuayVolumesCB', {'startDate': '20260901'}),
    ('bulkQuayVolumesCB', {'startDate': '20260230', 'endDate': '20260301'}),
    ('customers', {'startDate': '20260230', 'endDate': '20260301'}),
    ('bulkGateVolumesCB', {'startDate': '20260901', 'endDate': '20260930', 'shipId': '9'}),
    ('shipDetails', {'handlingMethodId': '9'}),
    ('class', {'classId': '1'}),
    ('origins', {'origin': '   '}),
    ('origins', {'origin': 'x' * 255}),
    ('customers', {'origin': 'test'}),
])
def test_invalid_or_unsupported_queries_fail_before_reader_with_documented_error(resource, params, parsing_api):
    client, reader = parsing_api
    response = client.get('/api/' + resource, params={'companyId': 'CNT', **params})
    assert response.status_code == 422
    assert ErrorResult.model_validate(response.json()).code == '0'
    assert response.headers['X-Error-Code'] == 'INVALID_REQUEST'
    assert not reader.calls


@pytest.mark.parametrize('value,expected', [('  Nội địa  ', 'Nội địa'), ('x' * 254, 'x' * 254)])
def test_origin_query_trimmed_and_restricted_to_origins(parsing_api, value, expected):
    client, reader = parsing_api
    response = client.get('/api/origins', params={'companyId': 'CNT', 'origin': value})
    assert response.status_code == 200
    assert reader.calls[0][1].origin == expected
    with pytest.raises(CorporateError) as caught:
        Query(companyId='CNT', origin='test').check_resource('class')
    assert caught.value.code == 'UNSUPPORTED_FILTER'


def test_published_snapshot_still_reaches_reader_unchanged(parsing_api):
    client, reader = parsing_api
    response = client.get('/api/origins', params={'companyId': 'CNT', 'snapshotId': 'b' * 32, 'page': 2})
    assert response.status_code == 200
    assert reader.calls[0][1].snapshotId == 'b' * 32
    assert reader.calls[0][1].page == 2


@pytest.mark.parametrize('resource', ['cargoType', 'cargoCategory', 'handlingMethodList', 'class', 'origins'])
def test_s_detail_identifier_boundary_matches_workbook_before_reader(resource, parsing_api):
    client, reader = parsing_api
    operation = client.app.openapi()['paths']['/api/' + resource + '/{record_id}']['get']
    parameter = next(item for item in operation['parameters'] if item['name'] == 'record_id')
    assert parameter['schema']['maxLength'] == 254
    response = client.get('/api/' + resource + '/' + 'x' * 255, params={'companyId': 'CNT'})
    assert response.status_code == 422
    assert ErrorResult.model_validate(response.json()).code == '0'
    assert not reader.calls
    response = client.get('/api/' + resource + '/' + 'x' * 254, params={'companyId': 'CNT'})
    assert response.status_code == 200  # Injected reader; this tests syntax, not source existence.
    assert reader.calls[0][2]['identity'] == 'x' * 254

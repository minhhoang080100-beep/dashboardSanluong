"""Inspector exposes the same receiver-facing filters and integrity metadata."""
import re

import pytest

from backend.corporate_api.contracts import PRODUCTION, RESOURCE_QUERY_FIELDS
from test_corporate_inspector import CATALOG, INSPECT, api, inspect, query_for  # noqa: F401


@pytest.mark.parametrize('resource', sorted(PRODUCTION))
def test_inspector_keeps_public_production_result_digest(api, resource):
    internal = inspect(api, resource)
    public = api['client'].get('/api/' + resource,
        params={'companyId': 'CNT', **query_for(resource)})
    assert public.status_code == internal['statusCode'] == 200
    assert re.fullmatch('[a-f0-9]{64}', public.headers['X-Result-Digest'])
    assert internal['headers']['X-Result-Digest'] == public.headers['X-Result-Digest']
    assert 'resultDigest' not in internal['body']
    assert set(internal['body']) == {'data', 'code', 'message'}


def test_inspector_catalog_advertises_all_supported_s_query_filters(api):
    resources = {row['id']: row for row in api['client'].get(CATALOG).json()['resources']}
    for resource, expected in RESOURCE_QUERY_FIELDS.items():
        actual = {field['name'] for field in resources[resource]['filters']}
        assert actual == expected, resource


@pytest.mark.parametrize('resource, query', [
    ('bulkQuayVolumesCB', {}),
    ('contGateVolumesCB', {'startDate': '20260901'}),
    ('bulkGateVolumesCB', {'startDate': '20260230', 'endDate': '20260301'}),
    ('contQuayVolumesCB', {'startDate': '20260901', 'endDate': '20260930', 'origin': 'ignored?'}),
    ('origins', {'startDate': '20260901', 'endDate': '20260930'}),
])
def test_inspector_and_public_reject_the_same_invalid_s_query_before_source(api, monkeypatch, resource, query):
    def forbidden(*args, **kwargs):
        pytest.fail('An invalid public query must not read production or catalog data')

    monkeypatch.setattr(api['store'], 'read', forbidden)
    internal = api['client'].post(INSPECT, json={'resource': resource, 'query': query}).json()
    external = api['client'].get('/api/' + resource, params={'companyId': 'CNT', **query})
    assert internal['statusCode'] == external.status_code == 422
    assert internal['headers']['X-Error-Code'] == external.headers['X-Error-Code'] == 'INVALID_REQUEST'
    assert internal['body'] == external.json()
    assert internal['requestQuery'] == {'companyId': 'CNT'}

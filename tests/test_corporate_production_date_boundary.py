"""Unsupported exclusive end dates are client errors, without source reads."""
from datetime import date

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

from backend.corporate_api import router as routes
from backend.corporate_api.contracts import PRODUCTION, Query
from backend.corporate_api.errors import CorporateError
from backend.corporate_api.live import LiveReader
from backend.corporate_api.operation_contracts import OperationQuery
from backend.corporate_api.source import ProductionSource, SourceError
from test_corporate_live import PROFILE, Source
from test_corporate_operation_api import fixture_row
from test_corporate_source import Query as SourceQuery, profile


@pytest.mark.parametrize('resource', sorted(PRODUCTION))
@pytest.mark.parametrize('start', ['99991201', '99991231'])
def test_http_maximum_end_date_is_invalid_period_before_profile_or_source_reads(monkeypatch, resource, start):
    monkeypatch.setenv('CORPORATE_API_ENABLED', 'true')
    source_calls = []

    def forbidden_source(*args, **kwargs):
        source_calls.append((args, kwargs))
        raise AssertionError('Unsupported dates must not read SmartTOS')

    # Deliberately invalid profile: request validation must precede even profile
    # readiness, instead of reporting an unrelated 503 to the caller.
    reader = LiveReader(profile={}, extract_fn=forbidden_source)
    app = FastAPI()
    app.include_router(routes.router)
    app.state.corporate_exports = reader
    app.dependency_overrides[routes.principal] = lambda: {
        'enabled': True, 'company_ids': ['CNT'], 'resources': list(PRODUCTION)}
    with TestClient(app) as client:
        response = client.get('/api/' + resource, params={
            'companyId': 'CNT', 'startDate': start, 'endDate': '99991231'})
    assert response.status_code == 422
    assert response.headers['x-error-code'] == 'INVALID_PERIOD'
    assert response.headers['cache-control'] == 'no-store'
    assert 'retry-after' not in response.headers
    assert set(response.json()) == {'data', 'code', 'message'}
    assert response.json()['data'] == [] and response.json()['code'] == '0'
    assert source_calls == []


@pytest.mark.parametrize('resource', sorted(PRODUCTION))
def test_query_rejects_maximum_day_as_production_end(resource):
    with pytest.raises(CorporateError) as caught:
        Query(companyId='CNT', startDate='99991231', endDate='99991231').check_resource(resource)
    assert caught.value.status == 422 and caught.value.code == 'INVALID_PERIOD'


@pytest.mark.parametrize('resources', [None, ['bulkQuayVolumesCB'], ['contGateVolumesCB']])
def test_source_adapter_rejects_maximum_end_before_metadata_queries(resources):
    source = SourceQuery([])
    with pytest.raises(SourceError) as caught:
        ProductionSource(source).extract(date.max, date.max, profile(), resources=resources)
    assert caught.value.code == 'SOURCE_RANGE'
    assert source.calls == []


def test_previous_day_keeps_inclusive_source_range_and_empty_success():
    day = date(9999, 12, 30)
    Query(companyId='CNT', startDate='99991230', endDate='99991230').check_resource('bulkQuayVolumesCB')
    source = SourceQuery([])
    result = ProductionSource(source).extract(day, day, profile(), resources=['bulkQuayVolumesCB'])
    assert result['bulkQuayVolumesCB']['ready'] is True
    assert result['bulkQuayVolumesCB']['rows'] == []
    fact_calls = [params for _, statement, params in source.calls if 'WITH period_facts AS' in statement]
    assert fact_calls == [(day, date.max), (day, date.max)]


def test_operation_catalog_inclusive_filter_still_handles_maximum_day():
    resource = 'oprt.cargoDirect'
    source = Source({resource: [fixture_row(resource)]})
    reader = LiveReader(profile=PROFILE, extract_fn=source)
    result = reader.read(resource, OperationQuery(
        companyId='CNT', startDate='99991231', endDate='99991231'))
    assert result['code'] == '1' and result['data'] == []
    assert result['pagination']['total'] == 0
    assert source.calls == [(resource,)]

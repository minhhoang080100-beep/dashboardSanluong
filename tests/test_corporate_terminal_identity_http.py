"""Source-qualified IDs survive real adapters, auth, HTTP and pagination."""
from copy import deepcopy
from decimal import Decimal

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

from backend.corporate_api.auth import MachineStore
from backend.corporate_api.contracts import IDENTITY, MODELS, PRODUCTION
from backend.corporate_api.live import LiveReader
from backend.corporate_api.router import router
from test_corporate_end_to_end import full_profile
from test_corporate_live_reference_sources import ProductionSourceQuery
from test_corporate_reference_scope import NATIVE_PROFILE
from test_corporate_source import fact


class HttpSource(ProductionSourceQuery):
    def __call__(self, database, statement, params):
        rows = super().__call__(database, statement, params)
        if 'FROM [dbo].[Cargo]' in statement and '[cargoGroupId]=1034' in statement:
            rows = [row for row in rows if row['cargoGroupId'] == 1034
                    and row['cargoName'] == row['cargoCode'] == '20F']
        return rows


@pytest.fixture
def api(tmp_path, monkeypatch):
    monkeypatch.setenv('CORPORATE_API_ENABLED', 'true')
    source = HttpSource()
    for database, data in source.data.items():
        # Deliberately different entities with the same numeric source IDs.
        for table, name in [('Vessel', 'vesselName'), ('Partner', 'partnerFullName'),
                            ('JobMethod', 'jobMethodName'), ('CargoGroup', 'cargoGroupName')]:
            data[table][0][name] = database + ' synthetic entity'
        data['Cargo'][0]['cargoName'] = database + ' synthetic bulk'
        data['Cargo'][1]['cargoGroupId'] = 1034
        data['CargoGroup'].append({**data['CargoGroup'][0], 'cargoGroupId': 1034})
        data['JobMethod'].append({**data['JobMethod'][0], 'jobMethodId': 41})
        source.facts[database] = [
            fact(source_id=index, cargo_id=cargo, cargo_group_id=1034 if cargo == 31 else 1,
                 cargo_name='20F' if cargo == 31 else 'Bulk', method_id=method,
                 quay_eligible=int(method == 40), ship_id=10, customer_id=20,
                 quantity_unit_code='CONT' if cargo == 31 else 'TAN',
                 native_weight=Decimal(weight))
            for index, (cargo, method, weight) in enumerate(
                [(31, 40, '1.125'), (30, 40, '2.250'), (31, 41, '3.125'), (30, 41, '4.250')], 1)
        ]
    profile = {**full_profile(), **NATIVE_PROFILE, 'container_size_source': 'native_cargo',
               'source_identity_mode': 'terminal_prefixed_v1'}
    auth = MachineStore(tmp_path / 'machine.sqlite3')
    auth.create_client('synthetic-reader', 'Synthetic-HTTP-Password',
                       company_ids=['CNT'], resources=sorted(MODELS))
    app = FastAPI()
    app.include_router(router)
    app.state.corporate_auth = auth
    app.state.corporate_exports = LiveReader(profile=profile, extract_fn=source.extract)
    with TestClient(app) as client:
        login = client.post('/api/login', json={'Username': 'synthetic-reader', 'Password': 'Synthetic-HTTP-Password'})
        assert login.status_code == 200
        headers = {'Authorization': 'Bearer ' + login.json()['accessToken']}
        yield client, headers, source, profile


@pytest.mark.parametrize('resource', sorted(MODELS))
def test_all_s_endpoints_paginate_and_preserve_source_ids(api, resource):
    client, headers, source, _ = api
    params = {'companyId': 'CNT', 'limit': 1}
    if resource in PRODUCTION:
        params.update(startDate='20260916', endDate='20260916')
    rows, digests = [], set()
    for page in range(1, 10):
        response = client.get('/api/' + resource, params={**params, 'page': page}, headers=headers)
        assert response.status_code == 200, response.text
        body = response.json()
        assert set(body) == {'data', 'code', 'message'} and body['code'] == '1'
        assert len(body['data']) == 1
        rows.extend(body['data'])
        if resource in PRODUCTION:
            digests.add(response.headers['X-Result-Digest'])
        assert not any(key.startswith('_') or key == 'sourceDatabase' for row in rows for key in row)
        if response.headers['X-Has-Next'] == 'false':
            break
    assert len(digests) == (1 if resource in PRODUCTION else 0)
    assert len(rows) == int(response.headers['X-Total-Count'])
    if resource in IDENTITY:
        key = IDENTITY[resource]
        assert len({row[key] for row in rows}) == len(rows)
        assert {row[key].split(':')[0] for row in rows} == {'CL', 'BT'}
    if resource in PRODUCTION:
        assert len(rows) == 2
        assert {row['handlingMethodId'].split(':')[0] for row in rows} == {'CL', 'BT'}
        assert {row['companyId'] for row in rows} == {'CNT'}
        field, expected = {
            'contQuayVolumesCB': ('containerWeight', 2.25),
            'contGateVolumesCB': ('containerWeight', 6.25),
            'bulkQuayVolumesCB': ('bulkWeight', 4.5),
            'bulkGateVolumesCB': ('bulkWeight', 8.5),
        }[resource]
        assert sum(row[field] for row in rows) == expected
        if resource.startswith('cont'):
            assert sum(row['containerTEU'] for row in rows) == 2


@pytest.mark.parametrize('resource', sorted(set(IDENTITY) - {'containerSize'}))
def test_detail_returns_only_the_requested_source(api, resource):
    client, headers, source, _ = api
    listing = client.get('/api/' + resource, params={'companyId': 'CNT'}, headers=headers)
    assert listing.status_code == 200, listing.text
    for row in listing.json()['data']:
        source.calls.clear()
        identity = row[IDENTITY[resource]]
        response = client.get('/api/' + resource + '/' + identity,
                              params={'companyId': 'CNT'}, headers=headers)
        assert response.status_code == 200, response.text
        assert response.json()['data'] == [row]
        database = 'SmartTOS' if identity.startswith('CL:') else 'SmartTOS_BenThuy'
        assert {db for db, _, _ in source.calls} == {database}


@pytest.mark.parametrize('value', ['10', 'cl:10', 'CL:010', 'CL:0', 'CL:10 ', ' CL:10', 'BT:9223372036854775808'])
def test_invalid_qualified_production_filter_is_422_without_source_read(api, value):
    client, headers, source, _ = api
    source.calls.clear()
    response = client.get('/api/bulkQuayVolumesCB', params={
        'companyId': 'CNT', 'startDate': '20260916', 'endDate': '20260916', 'shipId': value}, headers=headers)
    assert response.status_code == 422, response.text
    assert source.calls == []


def test_unfiltered_request_still_fails_when_one_source_is_unavailable(api):
    client, headers, source, _ = api
    source.fail_fact_database = 'SmartTOS_BenThuy'
    response = client.get('/api/bulkQuayVolumesCB', params={
        'companyId': 'CNT', 'startDate': '20260916', 'endDate': '20260916'}, headers=headers)
    assert response.status_code == 503 and response.json()['data'] == []
    assert response.headers['X-Error-Code'] == 'SOURCE_UNAVAILABLE'


def test_identity_activation_reloads_profile_on_next_request(tmp_path, api, monkeypatch):
    import json
    client, headers, source, profile = api
    filename = tmp_path / 'profile.json'
    legacy = deepcopy(profile)
    legacy['source_identity_mode'] = 'native'
    filename.write_text(json.dumps(legacy), encoding='utf8')
    monkeypatch.setenv('CORPORATE_SOURCE_PROFILE', str(filename))
    client.app.state.corporate_exports = LiveReader(extract_fn=source.extract)
    before = client.get('/api/handlingMethodList', params={'companyId': 'CNT'}, headers=headers)
    assert before.status_code == 503 and before.headers['X-Error-Code'] == 'SOURCE_ID_CONFLICT'
    filename.write_text(json.dumps(profile), encoding='utf8')
    after = client.get('/api/handlingMethodList', params={'companyId': 'CNT'}, headers=headers)
    assert after.status_code == 200
    assert {row['handlingMethodId'] for row in after.json()['data']} == {'CL:40', 'BT:40', 'CL:41', 'BT:41'}

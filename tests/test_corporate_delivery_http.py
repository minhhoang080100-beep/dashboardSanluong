"""Real auth/router/live validation through ASGI HTTP; extraction is synthetic.

These boundary tests make no network or configured database calls. A temporary
machine store is the only persistent state and no dashboard app is imported.
"""
from collections import Counter
from copy import deepcopy
from datetime import date
from decimal import Decimal
import json
import re

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

from backend.corporate_api import verify_delivery as verify
from backend.corporate_api.auth import MachineStore
from backend.corporate_api.live import LiveReader
from backend.corporate_api.reconciliation import NULL_FIELDS
from backend.corporate_api.router import router
from test_corporate_api import fixture_rows
from test_corporate_live import Source


CREDENTIALS = {'Username': 'boundary-reader', 'Password': 'synthetic-boundary-password'}
START, END = date(2026, 9, 16), date(2026, 9, 17)


class BoundarySource(Source):
    def __init__(self):
        rows = fixture_rows()
        ids = {'SYN-METHOD-1': '30', 'SYN-40F': '40', 'SYN-CLASS-1': '1',
               'SYN-SHIP-1': '10', 'SYN-SHIP-2': '11', 'SYN-SHIP-3': '12',
               'SYN-CARGO-1': '20', 'SYN-BULK': '3', 'SYN-CUSTOMER-1': '50',
               'SYN-ORIGIN-1': '60'}
        for name, items in rows.items():
            for row in items:
                for key, value in row.items():
                    if isinstance(value, str) and value in ids:
                        row[key] = ids[value]
                row['_sourceTerminals'] = ['cua_lo']
            if name in verify.RESOURCES:
                items.append({**deepcopy(items[0]), 'finishDate': '20260917'})
        super().__init__(rows)
        self.production_reads = Counter()
        self.fail_on_read = None
        self.mutate_preview = None

    def __call__(self, profile, start, end, resources, *, reference_scope=None):
        preview = super().__call__(profile, start, end, resources, reference_scope=reference_scope)
        for name in resources:
            if name not in verify.RESOURCES:
                continue
            self.production_reads[name] += 1
            if self.fail_on_read == (name, self.production_reads[name]):
                preview['datasets'][name].update(
                    ready=False, blockers=['WEIGHT_OR_UNIT_UNAVAILABLE'],
                    issue_counts={'WEIGHT_OR_UNIT_UNAVAILABLE': 1},
                    issue_samples=[{'code': 'WEIGHT_OR_UNIT_UNAVAILABLE',
                                    'terminal': 'cua_lo', 'sourceId': 12345}])
            if self.mutate_preview is not None:
                self.mutate_preview(name, self.production_reads[name], preview['datasets'][name])
        return preview


class AppTransport:
    """Adapt TestClient responses to the exact verifier transport interface."""
    def __init__(self, client):
        self.client = client
        self.get_responses = []
        self.logout_headers = None

    def request(self, method, path, *, params=None, headers=None, json_body=None):
        response = self.client.request(method, path, params=params, headers=headers, json=json_body)
        if method == 'GET':
            self.get_responses.append(response)
        if path == '/api/logout':
            self.logout_headers = dict(headers or {})
        return verify.Response(response.status_code, dict(response.headers), response.content)


@pytest.fixture
def boundary(tmp_path, monkeypatch):
    monkeypatch.setenv('CORPORATE_API_ENABLED', 'true')
    monkeypatch.setenv('CORPORATE_READ_MODE', 'live')
    monkeypatch.setenv('DASHBOARD_STATE_PATH', str(tmp_path / 'unused-control.sqlite3'))
    monkeypatch.setenv('CORPORATE_EXPORT_PATH', str(tmp_path / 'unused-exports.sqlite3'))
    monkeypatch.setattr(verify, 'PAGE_SIZE', 1)
    source = BoundarySource()
    profile = {'approved': True, 'company_id': 'CNT', 'terminals': ['cua_lo', 'ben_thuy'],
               'date_basis': 'shiftDate', 'production_scope': 'all_activity',
               'quay_selection': 'source_statistics', 'gate_selection': 'vessel_type',
               'accepted_null_fields': {key: list(fields) for key, fields in NULL_FIELDS.items()}}
    reader = LiveReader(profile=profile, extract_fn=source)
    machines = MachineStore(tmp_path / 'machines.sqlite3')
    machines.create_client(CREDENTIALS['Username'], CREDENTIALS['Password'],
                           company_ids=['CNT'], resources=list(verify.RESOURCES))
    app = FastAPI()
    app.state.corporate_auth = machines
    app.state.corporate_exports = reader
    app.include_router(router)
    try:
        with TestClient(app) as client:
            yield {'client': client, 'source': source, 'machines': machines,
                   'transport': AppTransport(client)}
    finally:
        reader.close()
    assert not (tmp_path / 'unused-control.sqlite3').exists()
    assert not (tmp_path / 'unused-exports.sqlite3').exists()


def run(boundary):
    return verify.verify(boundary['transport'], dict(CREDENTIALS),
                         start=START, end=END, production_scope='all_activity')


def test_verifier_reads_all_four_live_routes_with_real_auth_and_wire_serialization(boundary):
    report = run(boundary)
    assert report['retrievalComplete'] is True
    assert report['status'] == 'retrieved_without_reference'
    assert report['suppliedComparisonsMatched'] is False
    assert report['logoutHttp'] == 200
    for resource in verify.RESOURCES:
        expected = ({'tonnes': Decimal('60.250'), 'TEU': Decimal('4')}
                    if resource.startswith('cont') else {'tonnes': Decimal('24.750')})
        assert {key: Decimal(value) for key, value in report['resources'][resource]['totals'].items()} == expected
    assert boundary['source'].production_reads == Counter({name: 2 for name in verify.RESOURCES})
    assert all(len(window['pages']) == 2 for window in report['windows'])
    assert len(boundary['transport'].get_responses) == 8
    for response in boundary['transport'].get_responses:
        body = response.json()
        assert set(body) == {'data', 'code', 'message'}
        assert body['code'] == '1'
        assert response.headers['cache-control'] == 'no-store'
        assert response.headers['x-corporate-contract'] == 'S-v3-CNT-1'
        assert re.fullmatch('[0-9a-f]{64}', response.headers['x-result-digest'])
        assert 'x-snapshot-id' not in response.headers
        for row in body['data']:
            assert not any(key.startswith('_') for key in row)
            assert 'sourceDatabase' not in row
            for measure in ('containerWeight', 'containerTEU', 'bulkWeight'):
                if measure in row:
                    assert type(row[measure]) in (int, float)
    assert CREDENTIALS['Password'] not in json.dumps(report)


def test_source_failure_after_first_page_keeps_partial_totals_unavailable(boundary):
    boundary['source'].fail_on_read = ('contQuayVolumesCB', 2)
    report = run(boundary)
    assert report['retrievalComplete'] is False and report['status'] == 'incomplete'
    assert report['resources']['contQuayVolumesCB']['totals'] is None
    window = report['windows'][0]
    assert window['rowsReceived'] == 1 and window['totals'] is None
    assert {key: Decimal(value) for key, value in window['receivedSubtotal'].items()} == {
        'tonnes': Decimal('30.125'), 'TEU': Decimal('2')}
    assert window['pages'][1]['http'] == 503
    assert window['pages'][1]['serverErrorCode'] == 'WEIGHT_OR_UNIT_UNAVAILABLE'
    failed = boundary['transport'].get_responses[1]
    assert set(failed.json()) == {'data', 'code', 'message'}
    assert failed.json()['data'] == [] and failed.json()['code'] == '0'
    assert '12345' not in failed.text and 'issue_samples' not in failed.text
    assert report['logoutHttp'] == 200


@pytest.mark.parametrize('mutation', ['weight', 'business_key'])
def test_same_count_source_change_invalidates_all_pages_in_real_http_pipeline(boundary, mutation):
    def mutate(resource, read_number, dataset):
        if read_number != 2:
            return
        first = dataset['rows'][0]
        if mutation == 'weight':
            # The altered row was already consumed on page one. Page two's own
            # payload, keys and total count remain unchanged.
            measure = 'containerWeight' if resource.startswith('cont') else 'bulkWeight'
            first[measure] = Decimal('99.125')
        else:
            # Optional operator/customer IDs accept null; setting an actual
            # catalog ID changes a business key while preserving record count.
            first['originId' if resource.startswith('cont') else 'bulkOriginId'] = '60'

    boundary['source'].mutate_preview = mutate
    report = run(boundary)
    assert report['retrievalComplete'] is False
    assert report['status'] == 'incomplete'
    assert report['suppliedComparisonsMatched'] is False
    assert report['logoutHttp'] == 200
    for window in report['windows']:
        assert window['error'] == 'RESULT_CHANGED_BETWEEN_PAGES'
        assert window['totals'] is None and window['complete'] is False
        assert window['rowsReceived'] == 1
        assert len(window['pages']) == 2
        assert window['pages'][0]['resultDigest'] != window['pages'][1]['resultDigest']
        assert [item['http'] for item in window['pages']] == [200, 200]
    assert all(item['totals'] is None for item in report['resources'].values())
    assert boundary['source'].production_reads == Counter({name: 2 for name in verify.RESOURCES})


def test_report_date_and_private_metadata_changes_do_not_break_live_paging(boundary):
    def mutate(resource, read_number, dataset):
        for row in dataset['rows']:
            row['reportDate'] = f'202610{14 + read_number:02}'
            row['_internalAuditReference'] = f'private-read-{read_number}'
        if read_number % 2 == 0:
            dataset['rows'].reverse()

    boundary['source'].mutate_preview = mutate
    report = run(boundary)
    assert report['retrievalComplete'] is True
    for window in report['windows']:
        assert window['complete'] is True
        assert window['pages'][0]['resultDigest'] == window['pages'][1]['resultDigest']
    assert all('private-read-' not in response.text
               for response in boundary['transport'].get_responses)


def test_verifier_logout_revokes_real_token_before_any_more_source_reads(boundary):
    report = run(boundary)
    assert report['logoutHttp'] == 200
    before = deepcopy(boundary['source'].calls)
    response = boundary['client'].get('/api/contQuayVolumesCB',
        params={'companyId': 'CNT', 'startDate': '20260916', 'endDate': '20260917'},
        headers=boundary['transport'].logout_headers)
    assert response.status_code == 401
    assert response.headers['www-authenticate'] == 'Bearer'
    assert boundary['source'].calls == before


def test_missing_token_and_denied_grant_never_reach_live_source(boundary):
    params = {'companyId': 'CNT', 'startDate': '20260916', 'endDate': '20260917'}
    assert boundary['client'].get('/api/contQuayVolumesCB', params=params).status_code == 401
    assert boundary['source'].calls == []
    machines = boundary['machines']
    machines.create_client('boundary-limited', CREDENTIALS['Password'],
                           company_ids=['CNT'], resources=['contQuayVolumesCB'])
    login = boundary['client'].post('/api/login', json={
        'Username': 'boundary-limited', 'Password': CREDENTIALS['Password']})
    assert login.status_code == 200
    headers = {'Authorization': 'Bearer ' + login.json()['accessToken']}
    denied = boundary['client'].get('/api/bulkGateVolumesCB', params=params, headers=headers)
    assert denied.status_code == 403
    assert denied.headers['x-error-code'] == 'MACHINE_SCOPE_FORBIDDEN'
    assert boundary['source'].calls == []
    assert boundary['client'].post('/api/logout', headers=headers).status_code == 200

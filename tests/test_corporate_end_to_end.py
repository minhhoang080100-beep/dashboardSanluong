"""Synthetic source -> real adapters -> atomic publication -> authenticated HTTP."""
from copy import deepcopy
from datetime import date
from decimal import Decimal

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

from backend.corporate_api.auth import MachineStore
from backend.corporate_api.contracts import Query, MODELS

RESOURCE_KEYS = frozenset(MODELS)
from backend.corporate_api.errors import CorporateError
from backend.corporate_api.manage_exports import extract, publish_preview
from backend.corporate_api.router import router
from backend.corporate_api.source import REQUIRED_COLUMNS, SCHEMA_SQL
from backend.corporate_api.store import ExportStore
from test_corporate_catalog_source import FakeQuery, approved_profile
from test_corporate_source import fact


class FullSource(FakeQuery):
    def __init__(self):
        super().__init__()
        for data in self.data.values():
            data['JobMethod'].append({**data['JobMethod'][0], 'jobMethodId': 41})

    def __call__(self, database, sql, params):
        if sql == SCHEMA_SQL:
            return [{'table_name': table, 'column_name': column}
                    for table, columns in REQUIRED_COLUMNS.items() for column in columns]
        if 't.tallyShiftId AS source_id' in sql:
            return [fact(source_id=index, cargo_id=cargo, cargo_name='20F' if cargo == 31 else 'Bulk',
                         method_id=method, quay_eligible=int(method == 40), ship_id=10, customer_id=20,
                         native_weight=Decimal(weight))
                    for index, (cargo, method, weight) in enumerate(
                        [(31, 40, '1.125'), (30, 40, '2.250'), (31, 41, '3.125'), (30, 41, '4.250')], 1)]
        return super().__call__(database, sql, params)


def full_profile():
    from backend.corporate_api.reconciliation import NULL_FIELDS
    return {**approved_profile(), 'date_basis': 'shiftDate', 'production_scope': 'all_activity',
            'accepted_null_fields': {key: list(fields) for key, fields in NULL_FIELDS.items()},
            'quay_method_ids': {terminal: [40] for terminal in ('cua_lo', 'ben_thuy')},
            'gate_method_ids': {terminal: [41] for terminal in ('cua_lo', 'ben_thuy')},
            'cargo_kind_by_cargo': {terminal: {'30': 'bulk', '31': 'container'} for terminal in ('cua_lo', 'ben_thuy')}}


@pytest.fixture
def published(tmp_path):
    profile = full_profile()
    preview = extract(profile, date(2026, 9, 16), date(2026, 9, 16), sorted(RESOURCE_KEYS), FullSource())
    assert all(item['ready'] for item in preview['datasets'].values())
    store = ExportStore(tmp_path / 'exports.sqlite3')
    result = publish_preview(preview, profile, store, sorted(RESOURCE_KEYS))
    assert len(result) == 12
    return profile, preview, store


def test_all_extractors_publish_and_serve_machine_http(published, tmp_path, monkeypatch):
    _, _, store = published
    monkeypatch.setenv('CORPORATE_API_ENABLED', 'true')
    auth = MachineStore(tmp_path / 'machine.sqlite3')
    auth.create_client('synthetic_tct', 'Synthetic-Password-Only', company_ids=['CNT'], resources=sorted(RESOURCE_KEYS))
    app = FastAPI()
    app.state.corporate_auth, app.state.corporate_exports = auth, store
    app.include_router(router)
    with TestClient(app) as client:
        login = client.post('/api/login', json={'Username': 'synthetic_tct', 'Password': 'Synthetic-Password-Only'})
        assert login.status_code == 200
        headers = {'Authorization': 'Bearer ' + login.json()['accessToken']}
        for resource in sorted(RESOURCE_KEYS):
            query = {'companyId': 'CNT'}
            if 'VolumesCB' in resource:
                query.update(startDate='20260916', endDate='20260916')
            response = client.get('/api/' + resource, params=query, headers=headers)
            assert response.status_code == 200, (resource, response.text)
            body = response.json()
            assert body['data'] and body['code'] == '1'
            assert not any(key.startswith('_') for row in body['data'] for key in row)
            if resource == 'contQuayVolumesCB':
                assert sum(row['containerWeight'] for row in body['data']) == 2.25
                assert sum(row['containerTEU'] for row in body['data']) == 2
                assert {row['shipId'] for row in body['data']} == {'10', '10'}
            if resource == 'customers':
                assert type(body['data'][0]['metadata']['isDeleted']) is int
                assert body['data'][0]['metadata']['isDeleted'] == 1


def test_catalog_replacement_cannot_break_retained_production_references(published):
    profile, preview, store = published
    before = store.describe()
    new = deepcopy(preview)
    new['datasets']['shipDetails']['rows'] = []
    with pytest.raises(ValueError, match='historical'):
        publish_preview(new, profile, store, ['shipDetails'])
    assert store.describe() == before


def test_unmapped_tax_code_is_distinct_from_known_null(published):
    profile, preview, store = published
    query = Query(companyId='CNT', customerTaxCode='NOT-A-REAL-TAX-CODE')
    with pytest.raises(CorporateError) as error:
        store.read('customers', query)
    assert error.value.code == 'FILTER_NOT_READY'
    new = deepcopy(preview)
    for row in new['datasets']['customers']['rows']:
        row['_taxCodeMapped'] = True  # A confirmed empty source value, not absent mapping.
    publish_preview(new, profile, store, ['customers'])
    assert store.read('customers', query)['data'] == []

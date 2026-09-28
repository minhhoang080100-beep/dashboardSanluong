"""Calendar days and S/customer createdDate filters, including retained exports."""
from copy import deepcopy
from datetime import datetime, timezone
import json

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest
from pydantic import ValidationError

from backend.corporate_api import router as routes
from backend.corporate_api.contracts import MODELS, PRODUCTION, Query
from backend.corporate_api.errors import CorporateError
from backend.corporate_api.manage_exports import FORMAT, profile_digest, publish_preview
from backend.corporate_api.store import CUSTOMER_DATE_BASIS, ExportStore
from test_corporate_api import fixture_rows


def customer(created='2026-09-16T23:00:00+00:00', modified='2026-09-25T08:00:00+07:00', code='C1'):
    row = deepcopy(fixture_rows()['customers'][0])
    row.update(customerCode=code, _changedDate='20260925', _createdDate='19000101')
    row['metadata'].update(createdDate=created, modifiedDate=modified)
    return row


def publish(store, rows):
    return store.publish('customers', 'CNT', rows, read_at=datetime.now(timezone.utc).isoformat(), rule_version='test')


def read(store, day=None, **options):
    params = {'companyId': 'CNT', **options}
    if day:
        params.update(startDate=day, endDate=day)
    return store.read('customers', Query(**params))


def test_customer_filter_uses_original_creation_in_vietnam_day_and_ignores_private_hints(tmp_path):
    store = ExportStore(tmp_path / 'export.sqlite3')
    row = customer()
    publish(store, [row])
    assert read(store, '20260916')['data'] == []
    result = read(store, '20260917')
    assert result['pagination']['total'] == 1
    assert result['data'][0]['metadata'] == row['metadata']
    assert read(store, '20260925')['data'] == []
    assert read(store, '19000101')['data'] == []


def test_legacy_current_and_pinned_customer_indexes_migrate_without_rewriting_payload(tmp_path):
    path = tmp_path / 'export.sqlite3'
    store = ExportStore(path)
    old = publish(store, [customer(created='2026-09-16', code='OLD')])
    publish(store, [customer(created='2026-09-17', code='NEW')])
    with store.db() as db:
        before = [row[0] for row in db.execute('SELECT payload FROM export_rows ORDER BY snapshot_id,seq')]
        db.execute('UPDATE export_rows SET created_date=NULL')
        db.execute('ALTER TABLE export_versions DROP COLUMN customer_date_basis')
    migrated = ExportStore(path)
    assert read(migrated, '20260916', snapshotId=old['snapshotId'])['data'][0]['customerCode'] == 'OLD'
    assert read(migrated, '20260917')['data'][0]['customerCode'] == 'NEW'
    assert read(migrated, '20260925')['data'] == []
    with migrated.db() as db:
        assert [row[0] for row in db.execute('SELECT payload FROM export_rows ORDER BY snapshot_id,seq')] == before
        assert {row[0] for row in db.execute('SELECT customer_date_basis FROM export_versions')} == {CUSTOMER_DATE_BASIS}


@pytest.mark.parametrize('created', [None, 'not-a-source-date', '2026-02-30'])
def test_legacy_customer_without_valid_creation_evidence_fails_closed_not_modified_fallback(tmp_path, created):
    path = tmp_path / 'export.sqlite3'
    store = ExportStore(path)
    publish(store, [customer()])
    damaged = customer(created=created)
    damaged = {key: value for key, value in damaged.items() if not key.startswith('_')}
    with store.db() as db:
        db.execute('UPDATE export_rows SET payload=?, created_date=?', (json.dumps(damaged), '20260925'))
        db.execute('UPDATE export_versions SET customer_date_basis=NULL')
    migrated = ExportStore(path)
    with pytest.raises(CorporateError) as error:
        read(migrated, '20260925')
    assert error.value.status == 503 and error.value.code == 'FILTER_NOT_READY'
    assert 'not-a-source-date' not in error.value.message
    assert read(migrated)['data'][0]['metadata']['createdDate'] == created


def test_missing_creation_blocks_only_filtered_customer_reads_and_preserves_source_null(tmp_path):
    store = ExportStore(tmp_path / 'export.sqlite3')
    publish(store, [customer(created=None)])
    assert read(store)['data'][0]['metadata']['createdDate'] is None
    with pytest.raises(CorporateError) as error:
        read(store, '20260925')
    assert error.value.code == 'FILTER_NOT_READY'


def test_invalid_customer_creation_import_rolls_back_and_preserves_current(tmp_path):
    store = ExportStore(tmp_path / 'export.sqlite3')
    publish(store, [customer()])
    before = store.describe()
    with pytest.raises(ValueError):
        publish(store, [customer(created='2026-02-30')])
    assert store.describe() == before
    with store.db() as db:
        assert db.execute('SELECT COUNT(*) FROM export_versions').fetchone()[0] == 1


def test_customer_http_filter_and_error_envelope_stay_compact(tmp_path, monkeypatch):
    monkeypatch.setenv('CORPORATE_API_ENABLED', 'true')
    store = ExportStore(tmp_path / 'export.sqlite3')
    publish(store, [customer()])
    app = FastAPI()
    app.include_router(routes.router)
    app.state.corporate_exports = store
    app.dependency_overrides[routes.principal] = lambda: {
        'enabled': True, 'company_ids': ['CNT'], 'resources': ['customers']}
    with TestClient(app) as client:
        params = {'companyId': 'CNT', 'startDate': '20260917', 'endDate': '20260917'}
        success = client.get('/api/customers', params=params)
        assert success.status_code == 200 and len(success.json()['data']) == 1
        assert set(success.json()) == {'data', 'code', 'message'}
        publish(store, [customer(created=None)])
        blocked = client.get('/api/customers', params=params)
        assert blocked.status_code == 503 and blocked.headers['X-Error-Code'] == 'FILTER_NOT_READY'
        assert set(blocked.json()) == {'data', 'code', 'message'}


@pytest.mark.parametrize('resource', sorted(MODELS))
@pytest.mark.parametrize('invalid_day', ['20260230', '20250229', '20261301', '00000000'])
def test_every_s_report_date_is_a_real_calendar_day(resource, invalid_day):
    row = deepcopy(fixture_rows()[resource][0])
    row['reportDate'] = invalid_day
    with pytest.raises(ValidationError):
        MODELS[resource].model_validate(row)


@pytest.mark.parametrize('resource', sorted(PRODUCTION))
@pytest.mark.parametrize('invalid_day', ['20260230', '20250229'])
def test_every_production_finish_date_is_a_real_calendar_day(resource, invalid_day):
    row = deepcopy(fixture_rows()[resource][0])
    row['finishDate'] = invalid_day
    with pytest.raises(ValidationError):
        MODELS[resource].model_validate(row)


def test_valid_leap_day_stays_yyyymmdd_on_wire():
    row = deepcopy(fixture_rows()['bulkGateVolumesCB'][0])
    row.update(reportDate='20240229', finishDate='20240229')
    value = MODELS['bulkGateVolumesCB'].model_validate(row).model_dump(mode='json')
    assert value['reportDate'] == value['finishDate'] == '20240229'


def test_invalid_report_date_cannot_replace_a_published_preview(tmp_path):
    store = ExportStore(tmp_path / 'export.sqlite3')
    profile = {'approved': True}
    row = deepcopy(fixture_rows()['origins'][0])
    draft = {'format': FORMAT, 'companyId': 'CNT', 'profileDigest': profile_digest(profile),
             'sourceReadAt': datetime.now(timezone.utc).isoformat(),
             'datasets': {'origins': {'ready': True, 'rows': [row]}}}
    publish_preview(draft, profile, store, ['origins'])
    before = store.describe()
    row['reportDate'] = '20260230'
    with pytest.raises(ValidationError):
        publish_preview(draft, profile, store, ['origins'])
    assert store.describe() == before

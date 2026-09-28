"""Linked operations catalogs share the same source-freshness gate; no live SQL."""
from datetime import datetime, timedelta, timezone
import importlib
import json

import pytest

from backend.corporate_api.errors import CorporateError
from backend.corporate_api.manage_exports import FORMAT, profile_digest, publish_preview
from backend.corporate_api.operation_contracts import OperationQuery
from backend.corporate_api.store import ExportStore
from test_corporate_operation_api import fixture_row


store_module = importlib.import_module('backend.corporate_api.store')
YARD, TYPE = 'oprt.portWHYard', 'oprt.portWHYardType'
MAX_AGE = 3600


def stamp(age=0):
    return (datetime.now(timezone.utc) - timedelta(seconds=age)).isoformat()


def publish(store, resource, rows=None, age=0):
    return store.publish(resource, 'CNT', rows if rows is not None else [fixture_row(resource)],
                         read_at=stamp(age), rule_version='test')


def query(**overrides):
    return OperationQuery(companyId='CNT', startDate='20260901', endDate='20260930', **overrides)


def assert_read_error(store, code, resource=YARD, **kwargs):
    with pytest.raises(CorporateError) as error:
        store.read(resource, query(**kwargs), max_age_seconds=MAX_AGE)
    assert error.value.status == 503 and error.value.code == code
    assert 'oprt.' not in error.value.message and '1' not in error.value.message


def test_fresh_snapshot_cannot_serve_when_linked_current_catalog_is_stale(tmp_path):
    store = ExportStore(tmp_path / 'export.sqlite3')
    publish(store, TYPE, age=MAX_AGE + 60)
    publish(store, YARD)
    assert_read_error(store, 'REFERENCE_DATASET_STALE')
    publish(store, TYPE)
    assert len(store.read(YARD, query(), max_age_seconds=MAX_AGE)['data']) == 1


@pytest.mark.parametrize('age,code', [(None, 'REFERENCE_DATASET_NOT_READY'), (-600, 'REFERENCE_SOURCE_TIME_INVALID')])
def test_missing_or_future_dependency_fails_with_safe_error(tmp_path, age, code):
    store = ExportStore(tmp_path / 'export.sqlite3')
    if age is not None:
        publish(store, TYPE, age=age)
    publish(store, YARD)
    assert_read_error(store, code)


def test_malformed_dependency_source_time_does_not_expose_stored_value(tmp_path):
    store = ExportStore(tmp_path / 'export.sqlite3')
    publish(store, TYPE)
    publish(store, YARD)
    with store.db() as db:
        db.execute('UPDATE export_versions SET read_at=? WHERE resource=?', ('private-malformed-value', TYPE))
    assert_read_error(store, 'REFERENCE_SOURCE_TIME_INVALID')


def test_dependency_in_later_page_also_blocks_page_one(tmp_path):
    store = ExportStore(tmp_path / 'export.sqlite3')
    rows = [fixture_row(YARD, index=i, whYardTypeId=None) for i in range(1, 31)]
    rows[-1]['whYardTypeId'] = '1'
    publish(store, YARD, rows)
    publish(store, TYPE, age=MAX_AGE + 60)
    assert_read_error(store, 'REFERENCE_DATASET_STALE', limit=1)


@pytest.mark.parametrize('rows', [[], [fixture_row(YARD, whYardTypeId=None)]])
def test_empty_or_null_optional_references_do_not_require_catalog(tmp_path, rows):
    store = ExportStore(tmp_path / 'export.sqlite3')
    publish(store, YARD, rows)
    assert len(store.read(YARD, query(), max_age_seconds=MAX_AGE)['data']) == len(rows)


def test_empty_optional_list_does_not_require_location_catalog(tmp_path):
    store = ExportStore(tmp_path / 'export.sqlite3')
    publish(store, 'oprt.portEquipType')
    publish(store, 'oprt.portEquipment', [fixture_row('oprt.portEquipment', operationLocationTypeId=[])])
    assert len(store.read('oprt.portEquipment', query(), max_age_seconds=MAX_AGE)['data']) == 1


@pytest.mark.parametrize('stale_target', ['oprt.portEquipType', 'oprt.operationLocationType'])
def test_each_used_scalar_or_list_dependency_has_its_own_freshness_gate(tmp_path, stale_target):
    store = ExportStore(tmp_path / 'export.sqlite3')
    for target in ('oprt.portEquipType', 'oprt.operationLocationType'):
        publish(store, target, age=MAX_AGE + 60 if target == stale_target else 0)
    publish(store, 'oprt.portEquipment', [fixture_row('oprt.portEquipment', operationLocationTypeId=['1'])])
    assert_read_error(store, 'REFERENCE_DATASET_STALE', resource='oprt.portEquipment')


@pytest.mark.parametrize('maximum', [0, None])
def test_explicit_freshness_disable_keeps_existing_semantics(tmp_path, maximum):
    store = ExportStore(tmp_path / 'export.sqlite3')
    publish(store, YARD)
    assert len(store.read(YARD, query(), max_age_seconds=maximum)['data']) == 1


def test_pinned_snapshot_uses_its_own_dependency_metadata(tmp_path):
    store = ExportStore(tmp_path / 'export.sqlite3')
    old = publish(store, YARD)
    publish(store, YARD, [fixture_row(YARD, whYardTypeId=None)])
    publish(store, TYPE, age=MAX_AGE + 60)
    assert len(store.read(YARD, query(), max_age_seconds=MAX_AGE)['data']) == 1
    with pytest.raises(CorporateError) as error:
        store.read(YARD, query(snapshotId=old['snapshotId']), max_age_seconds=MAX_AGE)
    assert error.value.status == 409 and error.value.code == 'SNAPSHOT_REFERENCES_CHANGED'


def test_old_store_migration_backfills_dependency_metadata_once(tmp_path):
    path = tmp_path / 'export.sqlite3'
    store = ExportStore(path)
    snapshot = publish(store, YARD)
    publish(store, TYPE, age=MAX_AGE + 60)
    with store.db() as db:
        db.execute('ALTER TABLE export_versions DROP COLUMN reference_catalogs')
    reopened = ExportStore(path)
    with reopened.db() as db:
        metadata = db.execute('SELECT reference_catalogs FROM export_versions WHERE snapshot_id=?',
                              (snapshot['snapshotId'],)).fetchone()[0]
    assert json.loads(metadata) == [TYPE]
    assert_read_error(reopened, 'REFERENCE_DATASET_STALE')


def test_read_does_not_rescan_payloads_for_dependencies_or_query_source(tmp_path, monkeypatch):
    store = ExportStore(tmp_path / 'export.sqlite3')
    publish(store, TYPE)
    publish(store, YARD, [fixture_row(YARD, index=i) for i in range(1, 301)])
    decoded_rows = []
    loads = json.loads

    def decode(value, *args, **kwargs):
        if isinstance(value, str) and 'whYardId' in value:
            decoded_rows.append(value)
        return loads(value, *args, **kwargs)

    monkeypatch.setattr(store_module.json, 'loads', decode)
    source_sql = importlib.import_module('backend.corporate_api.sql')
    monkeypatch.setattr(source_sql, 'query_source', lambda *args: pytest.fail('GET queried SQL Server'))
    response = store.read(YARD, query(limit=1), max_age_seconds=MAX_AGE)
    assert response['pagination']['total'] == 300
    assert len(decoded_rows) == 1  # Only the requested payload, never all 300 rows.


def test_dependency_check_and_result_use_same_sqlite_read_snapshot(tmp_path, monkeypatch):
    store = ExportStore(tmp_path / 'export.sqlite3')
    with store.db() as db:
        db.execute('PRAGMA journal_mode=WAL')
    publish(store, TYPE)
    publish(store, YARD)
    original = store_module.ensure_dependencies_fresh
    observed = []

    def concurrent_update(db, dependencies, company_id, maximum):
        observed.append(db.in_transaction)
        # Simulates an independently committed publication after source version
        # selection. The active read must retain one consistent SQLite view.
        publish(ExportStore(store.path), TYPE, age=MAX_AGE + 60)
        return original(db, dependencies, company_id, maximum)

    monkeypatch.setattr(store_module, 'ensure_dependencies_fresh', concurrent_update)
    assert len(store.read(YARD, query(), max_age_seconds=MAX_AGE)['data']) == 1
    assert observed == [True]
    monkeypatch.setattr(store_module, 'ensure_dependencies_fresh', original)
    assert_read_error(store, 'REFERENCE_DATASET_STALE')


def test_publication_rejects_stale_existing_dependency_and_keeps_current(tmp_path, monkeypatch):
    monkeypatch.setenv('CORPORATE_MAX_SOURCE_AGE_SECONDS', str(MAX_AGE))
    store = ExportStore(tmp_path / 'export.sqlite3')
    publish(store, TYPE, age=MAX_AGE + 60)
    current = publish(store, YARD, [fixture_row(YARD, whYardTypeId=None)])
    profile = {'approved': True}
    draft = {'format': FORMAT, 'companyId': 'CNT', 'profileDigest': profile_digest(profile),
             'sourceReadAt': stamp(), 'datasets': {YARD: {'ready': True, 'rows': [fixture_row(YARD)]}}}
    with pytest.raises(CorporateError) as error:
        publish_preview(draft, profile, store, [YARD])
    assert error.value.code == 'REFERENCE_DATASET_STALE'
    assert next(row for row in store.describe() if row['resource'] == YARD)['snapshot_id'] == current['snapshotId']
    publish(store, TYPE)
    assert publish_preview(draft, profile, store, [YARD])[YARD]['rows'] == 1


@pytest.mark.parametrize('maximum', [MAX_AGE, 0, None])
def test_retained_snapshot_cannot_reference_removed_id_even_with_age_checks_disabled(tmp_path, maximum):
    store = ExportStore(tmp_path / 'export.sqlite3')
    profile = {'approved': True}

    def draft(yards, types):
        return {'format': FORMAT, 'companyId': 'CNT', 'profileDigest': profile_digest(profile),
                'sourceReadAt': stamp(), 'datasets': {
                    YARD: {'ready': True, 'rows': yards}, TYPE: {'ready': True, 'rows': types}}}

    old = publish_preview(draft([fixture_row(YARD)], [fixture_row(TYPE)]), profile, store, [YARD, TYPE])
    publish_preview(draft([fixture_row(YARD, whYardTypeId=None)], []), profile, store, [YARD, TYPE])
    with pytest.raises(CorporateError) as error:
        store.read(YARD, query(snapshotId=old[YARD]['snapshotId']), max_age_seconds=maximum)
    assert error.value.status == 409 and error.value.code == 'SNAPSHOT_REFERENCES_CHANGED'
    assert 'oprt.' not in error.value.message
    assert store.read(YARD, query(), max_age_seconds=maximum)['data'][0]['whYardTypeId'] is None


def test_same_batch_timestamp_keeps_pinned_pagination_when_dependencies_unchanged(tmp_path):
    store = ExportStore(tmp_path / 'export.sqlite3')
    rows = [fixture_row(YARD, index=i) for i in (1, 2, 3)]
    with store.atomic_publication():
        old = publish(store, YARD, rows)  # Dependency may publish later within this transaction.
        publish(store, TYPE)
    with store.db() as db:
        assert len({row[0] for row in db.execute('SELECT published_at FROM export_versions')}) == 1
    first = store.read(YARD, query(limit=1), max_age_seconds=MAX_AGE)
    publish(store, YARD, [fixture_row(YARD, index=4)])
    second = store.read(YARD, query(limit=1, page=2, snapshotId=old['snapshotId']), max_age_seconds=MAX_AGE)
    assert first['data'][0]['whYardId'] == '1' and second['data'][0]['whYardId'] == '2'
    assert first['pagination']['snapshotId'] == second['pagination']['snapshotId']


def test_retained_snapshot_without_used_dependencies_remains_readable(tmp_path):
    store = ExportStore(tmp_path / 'export.sqlite3')
    old = publish(store, YARD, [fixture_row(YARD, whYardTypeId=None)])
    publish(store, TYPE)
    publish(store, YARD)
    retained = store.read(YARD, query(snapshotId=old['snapshotId']), max_age_seconds=MAX_AGE)
    assert retained['data'][0]['whYardTypeId'] is None


def test_changed_then_reverted_dependency_still_invalidates_old_snapshot(tmp_path):
    store = ExportStore(tmp_path / 'export.sqlite3')
    publish(store, TYPE)
    old = publish(store, YARD)
    publish(store, YARD, [fixture_row(YARD, whYardTypeId=None)])
    publish(store, TYPE, [])
    publish(store, TYPE)  # Same payload as before does not prove uninterrupted integrity.
    with pytest.raises(CorporateError) as error:
        store.read(YARD, query(snapshotId=old['snapshotId']), max_age_seconds=0)
    assert error.value.status == 409 and error.value.code == 'SNAPSHOT_REFERENCES_CHANGED'

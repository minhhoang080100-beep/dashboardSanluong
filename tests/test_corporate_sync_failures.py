"""A failed/overlapping sync must not report an earlier success or widen scope."""
from datetime import datetime, timezone
import json

import pytest

from backend.corporate_api import sync
from backend.corporate_api.store import ExportStore
from test_corporate_end_to_end import FullSource, full_profile

NOW = datetime(2026, 9, 16, 12, tzinfo=timezone.utc)


def report_of(store):
    return json.loads(store.path.with_suffix('.sync-status.json').read_text(encoding='utf-8'))


@pytest.mark.parametrize('resources', [[], (), set(), {}, 'class', ['not_an_api'], [None], [[]]])
def test_invalid_selection_does_not_expand_to_default_or_read_source(tmp_path, monkeypatch, resources):
    def forbidden(*args, **kwargs):
        pytest.fail('Invalid selection must be rejected before extraction')
    monkeypatch.setattr(sync, 'extract', forbidden)
    with pytest.raises(ValueError, match='resource selection'):
        sync.run_once({}, ExportStore(tmp_path / 'exports.sqlite3'), resources=resources)


@pytest.mark.parametrize('days', [True, False, 0, 32, 1.0, '1', None])
def test_invalid_window_rejected_before_read(tmp_path, monkeypatch, days):
    monkeypatch.setattr(sync, 'extract', lambda *args, **kwargs: pytest.fail('must not read'))
    with pytest.raises(ValueError, match='window'):
        sync.run_once({}, ExportStore(tmp_path / 'exports.sqlite3'), days=days)


@pytest.mark.parametrize('failure_phase', ['extract', 'reconciliation_report'])
def test_failed_run_replaces_old_success_report_under_lock_and_keeps_exports(tmp_path, monkeypatch, failure_phase):
    store = ExportStore(tmp_path / 'exports.sqlite3')
    assert sync.run_once(full_profile(), store, days=1, query_fn=FullSource(), now=NOW)['status'] == 'published'
    before = store.describe()
    original_write = sync.write_report
    writes = []

    def write_while_locked(path, report):
        with pytest.raises(sync.SyncBusy), sync.sync_lock(store.path.with_suffix('.sync.lock')):
            pass
        writes.append(report)
        original_write(path, report)

    def fail(*args, **kwargs):
        raise RuntimeError('private-source-row-and-credentials')

    monkeypatch.setattr(sync, 'write_report', write_while_locked)
    monkeypatch.setattr(sync, failure_phase, fail)
    result = sync.run_once(full_profile(), store, days=1, query_fn=FullSource(), now=NOW)
    assert result['status'] == 'failed' and result['failureCode'] == 'SYNC_FAILED'
    assert result['resources'] == {} and result['checkOnly'] is False
    assert writes == [result] and report_of(store) == result
    assert 'private-source' not in json.dumps(result)
    assert store.describe() == before


def test_check_only_failure_records_mode_without_publishing(tmp_path, monkeypatch):
    store = ExportStore(tmp_path / 'exports.sqlite3')
    monkeypatch.setattr(sync, 'extract', lambda *args, **kwargs: (_ for _ in ()).throw(ValueError('private')))
    result = sync.run_once({}, store, now=NOW, check_only=True)
    assert result['status'] == 'failed' and result['checkOnly'] is True
    assert report_of(store) == result and store.describe() == []


@pytest.mark.parametrize('reported', [{}, {'origins': {'deliveryReady': True}},
                                    {'class': {'deliveryReady': True}, 'origins': {'deliveryReady': True}}])
def test_missing_or_unrequested_resource_report_cannot_be_published(tmp_path, monkeypatch, reported):
    store = ExportStore(tmp_path / 'exports.sqlite3')
    monkeypatch.setattr(sync, 'extract', lambda *args, **kwargs: {})
    monkeypatch.setattr(sync, 'reconciliation_report', lambda *args: {'resources': reported})
    monkeypatch.setattr(sync, 'publish_preview', lambda *args: pytest.fail('invalid report must not publish'))
    result = sync.run_once({}, store, now=NOW, resources=['class'])
    assert result['status'] == 'failed' and report_of(store)['status'] == 'failed'


def test_cli_failure_does_not_overwrite_an_active_job_report(tmp_path, monkeypatch, capsys):
    store = ExportStore(tmp_path / 'exports.sqlite3')
    path = store.path.with_suffix('.sync-status.json')
    sync.write_report(path, {'status': 'published', 'resources': {}})
    before = path.read_bytes()
    monkeypatch.setattr(sync, 'ExportStore', lambda: store)
    profile = tmp_path / 'missing.json'
    with sync.sync_lock(store.path.with_suffix('.sync.lock')):
        assert sync.main(['--profile', str(profile)]) == 1
    assert path.read_bytes() == before
    assert capsys.readouterr().out.strip() == '{"status":"failed","failureCode":"SYNC_FAILED"}'
    assert sync.main(['--profile', str(profile), '--check-only']) == 1
    assert report_of(store)['status'] == 'failed'
    assert report_of(store)['checkOnly'] is True


def test_busy_job_does_not_touch_report_or_query(tmp_path, monkeypatch):
    store = ExportStore(tmp_path / 'exports.sqlite3')
    path = store.path.with_suffix('.sync-status.json')
    sync.write_report(path, {'status': 'published'})
    before = path.read_bytes()
    monkeypatch.setattr(sync, 'extract', lambda *args, **kwargs: pytest.fail('busy job must not query'))
    with sync.sync_lock(store.path.with_suffix('.sync.lock')):
        assert sync.run_once({}, store)['status'] == 'busy'
    assert path.read_bytes() == before


@pytest.mark.parametrize('check_only', [False, True])
def test_resource_only_refresh_validates_catalogs_in_store(tmp_path, check_only):
    store = ExportStore(tmp_path / 'exports.sqlite3')
    assert sync.run_once(full_profile(), store, days=1, query_fn=FullSource(), now=NOW)['status'] == 'published'
    before = store.describe()
    report = sync.run_once(full_profile(), store, days=1, resources=['contQuayVolumesCB'],
                           query_fn=FullSource(), now=NOW, check_only=check_only)
    assert report['status'] == ('checked' if check_only else 'published')
    assert report['actualPublicationValidated'] is True
    assert report['resources']['contQuayVolumesCB']['deliveryReady'] is False
    assert report['resources']['contQuayVolumesCB']['referenceValidationScope'] == 'preview_only'
    if check_only:
        assert store.describe() == before


def test_resource_only_refresh_cannot_bypass_missing_catalogs(tmp_path):
    store = ExportStore(tmp_path / 'exports.sqlite3')
    report = sync.run_once(full_profile(), store, days=1, resources=['contQuayVolumesCB'],
                           query_fn=FullSource(), now=NOW)
    assert report['status'] == 'blocked'
    assert report['failureCode'] == 'PUBLICATION_FAILED'
    assert report['actualPublicationValidated'] is False
    assert store.describe() == []


def test_check_only_rejects_removed_catalog_still_used_by_retained_production(tmp_path, monkeypatch):
    store = ExportStore(tmp_path / 'exports.sqlite3')
    profile = full_profile()
    assert sync.run_once(profile, store, days=1, query_fn=FullSource(), now=NOW)['status'] == 'published'
    before = store.describe()
    preview = sync.extract(profile, NOW.date(), NOW.date(), ['class'], query_fn=FullSource())
    preview['datasets']['class']['rows'] = []
    with store.db() as db:
        versions_before = db.execute('SELECT COUNT(*) FROM export_versions').fetchone()[0]
    monkeypatch.setattr(sync, 'extract', lambda *args, **kwargs: preview)
    result = sync.run_once(profile, store, days=1, resources=['class'], now=NOW, check_only=True)
    assert result['status'] == 'blocked' and result['failureCode'] == 'VALIDATION_FAILED'
    assert result['actualPublicationValidated'] is False
    assert store.describe() == before
    with store.db() as db:
        assert db.execute('SELECT COUNT(*) FROM export_versions').fetchone()[0] == versions_before


def test_successful_check_only_restores_all_rows_versions_and_current_flags(tmp_path):
    store = ExportStore(tmp_path / 'exports.sqlite3')
    profile = full_profile()
    assert sync.run_once(profile, store, days=1, query_fn=FullSource(), now=NOW)['status'] == 'published'
    with store.db() as db:
        before = list(db.iterdump())
    result = sync.run_once(profile, store, days=1, query_fn=FullSource(), now=NOW, check_only=True)
    assert result['status'] == 'checked' and result['actualPublicationValidated'] is True
    with store.db() as db:
        assert list(db.iterdump()) == before


def test_caught_extraction_failure_has_failure_exit_code(tmp_path, monkeypatch, capsys):
    store = ExportStore(tmp_path / 'exports.sqlite3')
    profile_path = tmp_path / 'profile.json'
    profile_path.write_text('{}', encoding='utf-8')
    monkeypatch.setattr(sync, 'ExportStore', lambda: store)
    monkeypatch.setattr(sync, 'extract', lambda *args, **kwargs: (_ for _ in ()).throw(ValueError('private')))
    assert sync.main(['--profile', str(profile_path)]) == 1
    output = capsys.readouterr().out
    assert json.loads(output)['status'] == 'failed'
    assert 'private' not in output

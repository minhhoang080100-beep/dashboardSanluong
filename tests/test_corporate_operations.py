"""No live DB: publication gates, source age, reconciliation and scheduled sync."""
from datetime import datetime, timedelta, timezone
import json

import pytest

from backend.corporate_api.contracts import Query
from backend.corporate_api.errors import CorporateError
from backend.corporate_api.freshness import ensure_fresh, max_age_seconds
from backend.corporate_api.manage_exports import extract, publish_preview
from backend.corporate_api.reconciliation import reconciliation_report
from backend.corporate_api.store import ExportStore
from backend.corporate_api.sync import run_once, sync_lock, SyncBusy
from test_corporate_end_to_end import FullSource, full_profile
from test_corporate_source import fact, run
from test_corporate_store import bulk


@pytest.mark.parametrize('unit', [None, '', 'TAN', 'XE'])
def test_quay_container_requires_count_unit_and_keeps_diagnostic(unit):
    result, _ = run([fact(quantity_unit_code=unit)])
    item = result['contQuayVolumesCB']
    assert not item['ready'] and not item['rows']
    assert item['issue_counts']['CONTAINER_QUANTITY_UNIT_UNCONFIRMED'] == 1
    assert item['issue_samples'][0]['sourceId'] == 1


def test_time_work_is_excluded_from_quay_container_even_when_weight_exists():
    result, _ = run([fact(quantity_unit_code='GIO', quantity=6), fact(source_id=2, quantity=3)])
    item = result['contQuayVolumesCB']
    assert item['ready'] and item['rows'][0]['containerTEU'] == 6
    assert item['excluded_time_rows'] == 1


def test_reconciliation_is_bounded_and_null_fields_block_publication(tmp_path):
    profile = full_profile()
    del profile['accepted_null_fields']
    day = datetime(2026, 9, 16).date()
    preview = extract(profile, day, day, ['contQuayVolumesCB'], FullSource())
    report = reconciliation_report(preview, profile)['resources']['contQuayVolumesCB']
    assert report['extractionReady'] and not report['deliveryReady']
    assert report['nullFieldsAwaitingConfirmation']['originId'] == 1
    assert report['totals']['containerWeight'] == '2.250'
    with pytest.raises(ValueError, match='NULL_FIELD_UNCONFIRMED'):
        publish_preview(preview, profile, ExportStore(tmp_path / 'exports.sqlite3'), ['contQuayVolumesCB'])
    result, _ = run([fact(source_id=i, quantity_unit_code=None) for i in range(1, 102)])
    item = result['contQuayVolumesCB']
    assert len(item['issue_samples']) == 50
    assert item['issue_counts']['CONTAINER_QUANTITY_UNIT_UNCONFIRMED'] == 101


def test_recent_period_does_not_hide_stale_history_or_inherit_its_age(tmp_path):
    store = ExportStore(tmp_path / 'exports.sqlite3')
    old = (datetime.now(timezone.utc) - timedelta(days=3)).isoformat()
    fresh = datetime.now(timezone.utc).isoformat()
    store.publish_period('bulkQuayVolumesCB', 'CNT', [bulk('20260901'), bulk('20260916')],
                         start='20260901', end='20260930', read_at=old, rule_version='v1')
    store.publish_period('bulkQuayVolumesCB', 'CNT', [bulk('20260916')],
                         start='20260916', end='20260916', read_at=fresh, rule_version='v1')
    row = store.read('bulkQuayVolumesCB', Query(companyId='CNT', startDate='20260916', endDate='20260916'),
                     max_age_seconds=86400)
    assert row['pagination']['sourceReadAt'] == fresh
    for start, end in [('20260901', '20260901'), ('20260901', '20260916'), ('20260917', '20260917')]:
        with pytest.raises(CorporateError) as exc:
            store.read('bulkQuayVolumesCB', Query(companyId='CNT', startDate=start, endDate=end), max_age_seconds=86400)
        assert exc.value.code == 'DATASET_STALE'
    # Replacement of the entire range removes old timestamps, even for empty days.
    store.publish_period('bulkQuayVolumesCB', 'CNT', [], start='20260901', end='20260930',
                         read_at=fresh, rule_version='v1')
    assert store.read('bulkQuayVolumesCB', Query(companyId='CNT', startDate='20260901', endDate='20260930'),
                       max_age_seconds=86400)['data'] == []


def test_legacy_snapshot_freshness_and_clock_skew(tmp_path, monkeypatch):
    store = ExportStore(tmp_path / 'exports.sqlite3')
    now = datetime.now(timezone.utc)
    old = (now - timedelta(days=2)).isoformat()
    store.publish('bulkQuayVolumesCB', 'CNT', [], coverage=[['20260901', '20260930']], read_at=old, rule_version='v1')
    with store.db() as db:
        db.execute('UPDATE export_versions SET source_windows=NULL')
    with pytest.raises(CorporateError, match='quá thời hạn'):
        store.read('bulkQuayVolumesCB', Query(companyId='CNT', startDate='20260916', endDate='20260916'),
                   max_age_seconds=86400)
    with pytest.raises(CorporateError) as exc:
        ensure_fresh((now + timedelta(hours=1)).isoformat(), 86400, now=now)
    assert exc.value.code == 'SOURCE_TIME_INVALID'
    monkeypatch.setenv('CORPORATE_MAX_SOURCE_AGE_SECONDS', '-1')
    with pytest.raises(CorporateError):
        max_age_seconds()


def test_sync_publishes_atomically_and_failed_next_run_retains_previous(tmp_path):
    store = ExportStore(tmp_path / 'exports.sqlite3')
    now = datetime(2026, 9, 16, 12, tzinfo=timezone.utc)
    profile = full_profile()
    report = run_once(profile, store, days=1, query_fn=FullSource(), now=now)
    assert report['status'] == 'published' and len(report['publication']) == 12
    before = store.describe()
    source = FullSource()
    source.fail_db = 'SmartTOS_BenThuy'
    report = run_once(profile, store, days=1, query_fn=source, now=now)
    assert report['status'] == 'blocked' and store.describe() == before
    text = store.path.with_suffix('.sync-status.json').read_text(encoding='utf8')
    assert 'must-not-leak-password-or-server' not in text
    assert json.loads(text)['status'] == 'blocked'


def test_sync_check_only_does_not_publish_and_lock_prevents_overlapping_jobs(tmp_path):
    store = ExportStore(tmp_path / 'exports.sqlite3')
    now = datetime(2026, 9, 16, 12, tzinfo=timezone.utc)
    report = run_once(full_profile(), store, days=1, query_fn=FullSource(), now=now, check_only=True)
    assert report['status'] == 'checked' and store.describe() == []
    with sync_lock(store.path.with_suffix('.sync.lock')):
        report = run_once(full_profile(), store, days=1, query_fn=FullSource(), now=now)
        assert report['status'] == 'busy'
    with sync_lock(store.path.with_suffix('.sync.lock')):
        pass  # Lock is reusable after an earlier run.


def test_optional_worker_requires_explicit_enable_and_valid_schedule(tmp_path):
    from backend.corporate_api.service import worker_command
    assert worker_command({}) is None
    with pytest.raises(ValueError):
        worker_command({'CORPORATE_SYNC_ENABLED': 'true'})
    profile = tmp_path / 'profile.json'
    profile.write_text('{}')
    env = {'CORPORATE_SYNC_ENABLED': 'true', 'CORPORATE_SYNC_PROFILE': str(profile)}
    command = worker_command(env)
    assert command[-4:] == ['--days', '7', '--interval', '900']
    with pytest.raises(ValueError):
        worker_command({**env, 'CORPORATE_SYNC_INTERVAL_SECONDS': '1'})


def test_service_stops_worker_when_http_server_exits(monkeypatch):
    from backend.corporate_api import service
    class Process:
        def __init__(self, server=False):
            self.server, self.polls, self.returncode, self.stopped = server, 0, None, False
        def poll(self):
            self.polls += 1
            if self.server and self.polls >= 2:
                self.returncode = 7
            return self.returncode
        def terminate(self):
            self.stopped = True
            self.returncode = -15
        def wait(self, timeout=None):
            return self.returncode
    server, worker = Process(True), Process()
    pending = iter([server, worker])
    monkeypatch.setattr(service, 'worker_command', lambda env: ['synthetic-worker'])
    monkeypatch.setattr(service.subprocess, 'Popen', lambda args: next(pending))
    monkeypatch.setattr(service.signal, 'signal', lambda *args: None)
    monkeypatch.setattr(service.time, 'sleep', lambda _: None)
    assert service.main() == 7
    assert worker.stopped

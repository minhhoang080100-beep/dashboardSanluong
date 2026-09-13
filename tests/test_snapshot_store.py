"""Report retention across RAM eviction/restarts, with synthetic source facts."""
from concurrent.futures import ThreadPoolExecutor
from datetime import date
import json
import sqlite3
import zlib

import pytest

from backend import repository
from backend.reporting import ReportingService, ReportSnapshotNotFound, ReportStorageUnavailable
from backend.snapshot_store import SnapshotStore
from test_reporting import StubRepository, fact


@pytest.fixture(autouse=True)
def today(monkeypatch):
    monkeypatch.setattr(repository, 'vietnam_today', lambda: date(2026, 9, 13))


def test_evicted_ram_report_survives_new_service_and_preserves_source_types(tmp_path):
    path = tmp_path / 'snapshots.sqlite3'
    wall = [1000.0]
    store = SnapshotStore(path, clock=lambda: wall[0])
    repo = StubRepository([fact(1, weight='1.123456'), fact(2, weight=None)])
    service = ReportingService(repo, max_snapshots=1, snapshot_store=store)
    first = service.get_report('2026-09-11', '2026-09-12', 'all')
    identifier = first['meta']['report_id']
    original = service.export_snapshot(identifier)
    service.get_report('2026-09-12', '2026-09-13', 'all')
    assert identifier not in service._snapshots
    repo.rows = [fact(1, weight='999')]
    restarted = ReportingService(repo, snapshot_store=SnapshotStore(path, clock=lambda: wall[0]))
    assert restarted.export_snapshot(identifier) == original
    assert restarted.drilldown(identifier)['summary']['tonnage'] == 1.123
    wall[0] += 901
    expired_process = ReportingService(repo, snapshot_store=SnapshotStore(path, clock=lambda: wall[0]))
    with pytest.raises(ReportSnapshotNotFound):
        expired_process.get_report_snapshot(identifier)


def test_fresh_disk_report_reused_but_explicit_refresh_reads_source(tmp_path):
    path = tmp_path / 'snapshots.sqlite3'
    repo = StubRepository([fact(1)])
    first = ReportingService(repo, snapshot_store=SnapshotStore(path)).get_report('2026-09-11', '2026-09-12', 'all')
    next_process = ReportingService(repo, snapshot_store=SnapshotStore(path))
    result = next_process.get_report('2026-09-11', '2026-09-12', 'all')
    assert result['meta']['report_id'] == first['meta']['report_id']
    assert len(repo.calls) == 1
    refreshed = next_process.get_report('2026-09-11', '2026-09-12', 'all', refresh=True)
    assert refreshed['meta']['report_id'] != first['meta']['report_id']
    assert len(repo.calls) == 2


def test_many_readers_use_one_source_read_and_old_versions_remain_available(tmp_path):
    repo = StubRepository([fact(1), fact(2, terminal='ben_thuy')])
    service = ReportingService(repo, max_snapshots=2, snapshot_store=SnapshotStore(tmp_path / 'snapshots.sqlite3'))
    with ThreadPoolExecutor(max_workers=12) as pool:
        reports = list(pool.map(lambda _: service.get_report('2026-09-11', '2026-09-12', 'all'), range(12)))
    identifiers = {r['meta']['report_id'] for r in reports}
    assert len(identifiers) == 1
    assert len(repo.calls) == 1
    for _ in range(12):
        result = service.get_report('2026-09-11', '2026-09-12', 'all', refresh=True)
        identifiers.add(result['meta']['report_id'])
    assert len(service._snapshots) == 2
    with ThreadPoolExecutor(max_workers=12) as pool:
        totals = list(pool.map(lambda key: service.drilldown(key)['summary']['tonnage'], identifiers))
    assert totals == [20] * 13
    assert service.get_metrics()['storage']['snapshots'] == 13


def test_disk_limits_and_corrupted_payload_do_not_return_fabricated_report(tmp_path):
    path = tmp_path / 'snapshots.sqlite3'
    store = SnapshotStore(path, max_snapshots=2, max_rows=2)
    service = ReportingService(StubRepository([fact(1)]), max_snapshots=1, snapshot_store=store)
    identifiers = [service.get_report('2026-09-11', '2026-09-12', 'all', refresh=True)['meta']['report_id'] for _ in range(3)]
    assert store.get(identifiers[0]) is None
    assert store.stats()['stored_rows'] == 2
    with sqlite3.connect(path) as db:
        db.execute('UPDATE report_snapshots SET payload=? WHERE report_id=?', (b'invalid', identifiers[1]))
    with pytest.raises(ReportStorageUnavailable):
        service.get_report_snapshot(identifiers[1])


def test_single_report_limit_rejects_without_removing_retained_snapshot(tmp_path):
    from backend.reporting import ReportCapacityError
    store = SnapshotStore(tmp_path / 'snapshots.sqlite3', max_rows=1)
    repo = StubRepository([fact(1)])
    service = ReportingService(repo, snapshot_store=store)
    identifier = service.get_report('2026-09-11', '2026-09-12', 'all')['meta']['report_id']
    repo.rows.append(fact(2))
    with pytest.raises(ReportCapacityError):
        service.get_report('2026-09-11', '2026-09-12', 'all', refresh=True)
    assert store.get(identifier) is not None


def test_reduced_runtime_limit_returns_capacity_error_for_persisted_report(tmp_path):
    from backend.reporting import ReportCapacityError
    path = tmp_path / 'snapshots.sqlite3'
    repo = StubRepository([fact(1), fact(2)])
    service = ReportingService(repo, snapshot_store=SnapshotStore(path))
    identifier = service.get_report('2026-09-11', '2026-09-12', 'all')['meta']['report_id']
    smaller = ReportingService(repo, max_snapshot_rows=1, snapshot_store=SnapshotStore(path))
    with pytest.raises(ReportCapacityError):
        smaller.get_report_snapshot(identifier)
    with pytest.raises(ReportCapacityError):
        smaller.get_report('2026-09-11', '2026-09-12', 'all')


@pytest.mark.parametrize('content', [[], {'format': 1, 'report': {'meta': []}, 'rows': []},
    {'__snapshot_type__': 'decimal', 'value': 'invalid'}, {'__snapshot_type__': 'date', 'value': 'invalid'}])
def test_malformed_json_snapshot_is_a_controlled_storage_error(tmp_path, content):
    path = tmp_path / 'snapshots.sqlite3'
    repo = StubRepository([fact(1)])
    service = ReportingService(repo, snapshot_store=SnapshotStore(path))
    identifier = service.get_report('2026-09-11', '2026-09-12', 'all')['meta']['report_id']
    with sqlite3.connect(path) as db:
        db.execute('UPDATE report_snapshots SET payload=? WHERE report_id=?', (zlib.compress(json.dumps(content).encode()), identifier))
    restarted = ReportingService(repo, snapshot_store=SnapshotStore(path))
    with pytest.raises(ReportStorageUnavailable):
        restarted.get_report_snapshot(identifier)

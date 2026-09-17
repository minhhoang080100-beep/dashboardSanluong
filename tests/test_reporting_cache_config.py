"""Configured report freshness never changes source timestamps or retention."""

from datetime import date, datetime, timedelta, timezone

from pydantic import ValidationError
import pytest

from backend import reporting, repository
from backend.config import Settings, settings
from backend.reporting import ReportingService
from backend.snapshot_store import SnapshotStore
from test_reporting import StubRepository, fact


def test_production_cache_setting_defaults_to_120_without_changing_constructor(monkeypatch):
    monkeypatch.delenv("REPORT_CACHE_TTL_SECONDS", raising=False)
    configured = Settings(_env_file=None)
    assert configured.REPORT_CACHE_TTL_SECONDS == 120
    assert reporting.reporting_service.cache_ttl == settings.REPORT_CACHE_TTL_SECONDS
    assert ReportingService().cache_ttl == 30
    assert reporting.reporting_service.snapshot_ttl == 900
    assert reporting.reporting_service.max_snapshot_rows == 100_000


@pytest.mark.parametrize("value", ["1", "120", "300"])
def test_report_cache_setting_reads_bounded_environment_values(monkeypatch, value):
    monkeypatch.setenv("REPORT_CACHE_TTL_SECONDS", value)
    assert Settings(_env_file=None).REPORT_CACHE_TTL_SECONDS == int(value)


@pytest.mark.parametrize("value", ["0", "301", "-1", "nan", "120.5"])
def test_report_cache_setting_rejects_invalid_environment_values(monkeypatch, value):
    monkeypatch.setenv("REPORT_CACHE_TTL_SECONDS", value)
    with pytest.raises(ValidationError):
        Settings(_env_file=None)


def test_configured_cache_reuses_ram_and_disk_but_refresh_reads_new_facts(tmp_path, monkeypatch):
    import pyodbc

    def deny_sql(*args, **kwargs):
        pytest.fail("Synthetic cache regression must not connect to source SQL")

    monkeypatch.setattr(pyodbc, "connect", deny_sql)
    monkeypatch.setattr(repository, "vietnam_today", lambda: date(2026, 9, 13))
    monkeypatch.setenv("REPORT_CACHE_TTL_SECONDS", "120")
    configured = Settings(_env_file=None)
    elapsed = [0.0]
    source_time = [datetime(2026, 9, 13, 1, tzinfo=timezone.utc)]

    class SourceClock(datetime):
        @classmethod
        def now(cls, tz=None):
            return source_time[0]

    monkeypatch.setattr(reporting, "datetime", SourceClock)
    store = SnapshotStore(tmp_path / "cache.sqlite3", clock=lambda: 1000 + elapsed[0])
    repo = StubRepository([fact(1, weight="10")])

    def new_service():
        return ReportingService(repo, cache_ttl=configured.REPORT_CACHE_TTL_SECONDS,
                                clock=lambda: elapsed[0], snapshot_store=store)

    service = new_service()
    first = service.get_report("2026-09-11", "2026-09-12", "all")
    identifier = first["meta"]["report_id"]
    stamp = first["meta"]["source_read_at"]
    stored = store.get(identifier)
    assert stored["fresh_until"] - stored["created"] == 120
    assert stored["expires"] - stored["created"] == 900

    # Beyond the former 30-second TTL, both RAM and a new process still use
    # the same source read. The timestamp is evidence, not a cache-hit clock.
    elapsed[0] = 60
    source_time[0] += timedelta(seconds=60)
    repo.rows = [fact(1, weight="25")]
    ram = service.get_report("2026-09-11", "2026-09-12", "all")
    restarted = new_service()
    disk = restarted.get_report("2026-09-11", "2026-09-12", "all")
    for result in (ram, disk):
        assert result["meta"]["report_id"] == identifier
        assert result["meta"]["source_read_at"] == stamp
        assert result["meta"]["cache"]["ttl_seconds"] == 120
        assert result["overview"]["total_tonnage"] == 10
    assert len(repo.calls) == 1

    refreshed = restarted.get_report("2026-09-11", "2026-09-12", "all", refresh=True)
    assert len(repo.calls) == 2
    assert refreshed["meta"]["report_id"] != identifier
    assert refreshed["meta"]["source_read_at"] != stamp
    assert refreshed["meta"]["source_read_at"] == source_time[0].isoformat()
    assert refreshed["overview"]["total_tonnage"] == 25
    assert restarted.get_report_snapshot(identifier)["meta"]["source_read_at"] == stamp

    # Freshness ends at the configured bound while old snapshots remain
    # available by ID for their separate 900-second retention period.
    elapsed[0] = 180
    source_time[0] += timedelta(seconds=120)
    expired_cache = restarted.get_report("2026-09-11", "2026-09-12", "all")
    assert len(repo.calls) == 3
    assert expired_cache["meta"]["report_id"] != refreshed["meta"]["report_id"]
    assert restarted.get_report_snapshot(identifier)["overview"]["total_tonnage"] == 10

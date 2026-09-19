"""Comparison windows and optimized prior totals retain current source snapshots."""
from collections import defaultdict
from copy import deepcopy
from datetime import date, datetime
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient

from backend import repository
from backend.database import DatabaseQueryError
from backend.report_comparison import comparison_period
from backend.reporting import ReportingService
from backend.snapshot_store import SnapshotStore
from test_reporting import StubRepository, fact


@pytest.fixture(autouse=True)
def today(monkeypatch):
    monkeypatch.setattr(repository, 'vietnam_today', lambda: date(2026, 9, 18))


def dated(identifier, day, **kwargs):
    value = fact(identifier, **kwargs)
    value['operation_day'] = date.fromisoformat(day)
    value['latest_operation_at'] = datetime.combine(value['operation_day'], datetime.min.time())
    return value


@pytest.mark.parametrize('start,end,expected', [
    ('2026-09-01', '2026-09-18', ('2025-09-01', '2025-09-18')),
    ('2024-02-29', '2024-02-29', ('2023-02-28', '2023-02-28')),
    ('2024-02-01', '2024-02-29', ('2023-02-01', '2023-02-28')),
    ('2025-02-01', '2025-02-28', ('2024-02-01', '2024-02-28')),
    ('2024-02-29', '2024-03-01', ('2023-02-28', '2023-03-01')),
    ('2025-12-29', '2026-01-04', ('2024-12-29', '2025-01-04')),
    ('1900-01-01', '1900-01-01', ('1899-01-01', '1899-01-01')),
])
def test_previous_year_uses_calendar_endpoints_with_explicit_february_29_policy(start, end, expected):
    result = comparison_period(date.fromisoformat(start), date.fromisoformat(end), 'previous_year')
    assert (result['start_date'].isoformat(), result['end_date'].isoformat()) == expected
    assert result['mode'] == 'previous_year'
    assert result['day_count'] == (result['end_date'] - result['start_date']).days + 1
    if start.endswith('02-29') or end.endswith('02-29'):
        assert '29/02 quy về 28/02' in result['label']


def test_default_comparison_still_has_the_same_number_of_preceding_days():
    result = comparison_period(date(2026, 9, 1), date(2026, 9, 18))
    assert result == {'start_date': date(2026, 8, 14), 'end_date': date(2026, 8, 31),
                      'mode': 'previous_period', 'label': '18 ngày liền trước', 'day_count': 18}
    for invalid in ['year', '', None, [], True]:
        with pytest.raises(ValueError):
            comparison_period(date(2026, 9, 1), date(2026, 9, 18), invalid)


def summarize_prior(rows):
    """Independent expected SQL grouping for parity with mixed raw source facts."""
    groups = defaultdict(list)
    for row in rows:
        groups[tuple(row[key] for key in ('terminal_id', 'vessel_id', 'source_voyage_id', 'unit_code', 'unit_name', 'tonne_factor'))].append(row)
    result = []
    for group in groups.values():
        item = deepcopy(group[0])
        item.update(kind='comparison', id=None, quantity=None,
                    operation_day=min(row['operation_day'] for row in group))
        weights = [row['native_weight'] for row in group if row['native_weight'] is not None]
        item['native_weight'] = sum(weights, Decimal(0)) if weights else None
        item['teu'] = sum((row['teu'] for row in group), Decimal(0))
        for field in ('record_count', 'known_weight_count', 'missing_weight_count', 'container_row_count', 'missing_quantity_count', 'negative_value_count'):
            item[field] = sum(row[field] for row in group)
        result.append(item)
    return result


def test_grouped_prior_totals_preserve_nulls_units_signed_values_teu_and_distinct_voyages():
    previous = [dated(1, '2025-09-01', weight='100.125'), dated(2, '2025-09-02', weight='-5.125'),
                dated(3, '2025-09-02', weight=None, cargo='20F', quantity=None),
                dated(4, '2025-09-03', weight='10', cargo='40E', quantity='2'),
                dated(5, '2025-09-03', weight='4', unit='M3'),
                dated(6, '2025-09-04', weight='7', voyage=None, source_voyage='999'),
                dated(7, '2025-09-04', weight='3', terminal='ben_thuy'),
                dated(8, '2025-09-04', weight=None, voyage='102')]
    current = [dated(10, '2026-09-01', weight='12'), dated(11, '2026-09-02', weight=None, quantity='1')]
    repo = repository.DashboardRepository()
    raw = repo._dashboard_from_rows(deepcopy(current + previous), date(2026, 9, 1), date(2026, 9, 18), 'all', comparison='previous_year')
    compact = repo._dashboard_from_rows(deepcopy(current + summarize_prior(previous)), date(2026, 9, 1), date(2026, 9, 18), 'all', comparison='previous_year')
    raw['meta'].pop('generated_at')
    compact['meta'].pop('generated_at')
    assert compact == raw
    assert compact['overview']['record_count'] == 2
    assert compact['overview']['trend_tonnage'] is None
    assert compact['meta']['previous_period']['record_count'] == 8
    assert compact['meta']['previous_period']['metric_coverage']['teu']['status'] == 'partial'


def test_noncontiguous_comparison_ignores_gap_and_future_facts_but_keeps_source_watermark():
    repo = StubRepository([dated(1, '2025-09-12', weight='5'), dated(2, '2026-09-12', weight='15'),
                           dated(3, '2026-03-01', weight='999'), dated(4, '2026-09-13', weight='999')])
    report = repo.read_report('2026-09-12', '2026-09-12', 'all', comparison='previous_year')
    assert report['report']['overview']['total_tonnage'] == 15
    assert report['report']['overview']['trend_tonnage'] == 200
    assert report['report']['meta']['previous_period']['record_count'] == 1
    assert [row['id'] for row in report['rows']] == ['2']
    sql, params = repo.calls[0]
    assert params == (date(2025, 9, 12), date(2026, 9, 13), date(2026, 9, 12),
                      date(2025, 9, 12), date(2025, 9, 13)) * 2
    assert sql.count('AND t.shiftDate >= ?') == 2  # Raw current branch excludes the entire gap.
    assert sql.count("'comparison' AS kind") == 2
    assert sql.count('SUM(t.weightNetSum) AS native_weight') == 2
    assert sql.count('t.weightNetSum AS native_weight') == 2
    assert sql.count('?') == len(params)
    assert sql.count('OPTION (RECOMPILE)') == 1
    assert sql.count("sg.statisticsGroupTypeCode = N'SANLUONG-QUACANG'") == 2
    assert 'NOLOCK' not in sql


def test_overlapping_366_day_year_window_does_not_duplicate_current_rows_or_invent_drilldown_trends():
    current = [dated(1, '2025-09-18', weight='5'), dated(2, '2026-09-18', weight='15')]
    previous = [dated(3, '2024-09-18', weight='10'), current[0]]
    repo = repository.DashboardRepository()
    raw = repo._dashboard_from_rows(deepcopy(previous[:1] + current), date(2025, 9, 18), date(2026, 9, 18), 'all', comparison='previous_year')
    grouped = repo._dashboard_from_rows(deepcopy(current + summarize_prior(previous)), date(2025, 9, 18), date(2026, 9, 18), 'all', comparison='previous_year')
    assert raw['overview'] == grouped['overview']
    assert grouped['overview']['total_tonnage'] == 20
    assert grouped['overview']['record_count'] == 2
    assert grouped['overview']['trend_tonnage'] == 33.3
    service = ReportingService(StubRepository(previous[:1] + current))
    report = service.get_report('2025-09-18', '2026-09-18', 'all', comparison='previous_year')
    exported = service.export_drilldown(report['meta']['report_id'])['report']
    assert exported['overview']['total_tonnage'] == 20
    assert exported['overview']['trend_tonnage'] is None
    assert exported['meta']['filters']['comparison'] == 'previous_year'


def test_report_modes_are_isolated_in_memory_disk_and_refresh_preserves_old_details(tmp_path):
    repo = StubRepository([dated(1, '2025-09-12', weight='5'), dated(2, '2026-09-11', weight='10'), dated(3, '2026-09-12', weight='20')])
    store = SnapshotStore(tmp_path / 'comparison.sqlite3')
    service = ReportingService(repo, snapshot_store=store)
    previous = service.get_report('2026-09-12', '2026-09-12', 'all')
    annual = service.get_report('2026-09-12', '2026-09-12', 'all', comparison='previous_year')
    assert previous['overview']['trend_tonnage'] == 100
    assert annual['overview']['trend_tonnage'] == 300
    assert 'comparison' not in previous['meta']['filters']
    assert annual['meta']['filters']['comparison'] == 'previous_year'
    assert previous['meta']['report_id'] != annual['meta']['report_id']
    assert service.get_report('2026-09-12', '2026-09-12', 'all', comparison='previous_year')['meta']['report_id'] == annual['meta']['report_id']
    assert len(repo.calls) == 2
    restarted = ReportingService(repo, snapshot_store=store)
    for mode, original in [('previous_period', previous), ('previous_year', annual)]:
        assert restarted.get_report('2026-09-12', '2026-09-12', 'all', comparison=mode)['meta']['report_id'] == original['meta']['report_id']
    assert len(repo.calls) == 2
    # Corrections, additions and removals are fully reread on explicit refresh.
    repo.rows[:] = [dated(1, '2025-09-12', weight='8'), dated(4, '2026-09-12', weight='24')]
    refreshed = restarted.get_report('2026-09-12', '2026-09-12', 'all', comparison='previous_year', refresh=True)
    assert refreshed['overview']['trend_tonnage'] == 200
    assert refreshed['overview']['total_tonnage'] == 24
    assert [row['id'] for row in restarted.drilldown(annual['meta']['report_id'])['operations']['rows']] == ['3']
    assert restarted.drilldown(annual['meta']['report_id'])['summary']['tonnage'] == 20
    assert [row['id'] for row in restarted.drilldown(refreshed['meta']['report_id'])['operations']['rows']] == ['4']


def test_service_does_not_send_default_mode_to_older_repository_signatures():
    class Legacy(StubRepository):
        def read_report(self, start, end, terminal, *, production_scope='nghe_tinh'):
            return super().read_report(start, end, terminal, production_scope=production_scope)
    service = ReportingService(Legacy([dated(1, '2026-09-12')]))
    assert service.get_report('2026-09-12', '2026-09-12')['overview']['total_tonnage'] == 10
    class WrongMode(StubRepository):
        def read_report(self, *args, **kwargs):
            kwargs.pop('comparison', None)
            return super().read_report(*args, **kwargs)
    with pytest.raises(DatabaseQueryError):
        ReportingService(WrongMode([])).get_report('2026-09-12', '2026-09-12', comparison='previous_year')


def test_http_comparison_validation_and_default_compatibility():
    from backend.main import app
    from backend.control_api import require_user
    from backend.integration import get_reporting
    calls = []
    class Service:
        def get_report(self, **kwargs):
            calls.append(kwargs)
            return {'comparison': kwargs.get('comparison', 'previous_period')}
    old = dict(app.dependency_overrides)
    app.dependency_overrides[require_user] = lambda: {'id': 1, 'terminals': ['cua_lo', 'ben_thuy']}
    app.dependency_overrides[get_reporting] = lambda: Service()
    try:
        client = TestClient(app)
        params = {'start_date': '2026-09-01', 'end_date': '2026-09-18'}
        assert client.get('/api/dashboard', params=params).json()['comparison'] == 'previous_period'
        assert 'comparison' not in calls[-1]
        assert client.get('/api/dashboard', params={**params, 'comparison': 'previous_year'}).json()['comparison'] == 'previous_year'
        assert calls[-1]['comparison'] == 'previous_year'
        assert client.get('/api/dashboard', params={**params, 'comparison': 'invalid'}).status_code == 422
    finally:
        app.dependency_overrides.clear()
        app.dependency_overrides.update(old)

"""Cross-feature route guards use synthetic snapshots and temporary state only."""
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from backend import integration
from test_control_store import state


@pytest.mark.parametrize('stored,current', [('previous_year', 'previous_period'), ('previous_period', 'previous_year')])
def test_closed_comparison_rejects_different_basis_before_export(monkeypatch, stored, current):
    filters = {'terminal': 'cua_lo', 'start_date': '2026-09-01', 'end_date': '2026-09-03'}
    closed = {**filters, 'comparison': stored}
    report = {'meta': {'filters': {**filters, 'comparison': current}}}
    monkeypatch.setattr(integration, 'report_scope', lambda *args: report)
    store = SimpleNamespace(get_closed_report=lambda *args: closed)
    service = SimpleNamespace(export_snapshot=lambda *args: pytest.fail('Mismatched basis must stop before export'))
    with pytest.raises(HTTPException) as failure:
        integration.compare_closed(1, integration.CompareBody(report_id='sample'), user={}, store=store, service=service)
    assert failure.value.status_code == 422
    assert 'cơ sở so sánh' in failure.value.detail


def test_admin_metrics_reads_missing_receipt_without_claiming_backup_success(state):
    store, admin, _, _ = state
    service = SimpleNamespace(get_metrics=lambda: {'requests': {'report': 3}})
    result = integration.metrics(user=admin, store=store, service=service)
    assert result['requests'] == {'report': 3}
    assert result['backup']['status'] == 'not_configured'
    assert result['backup']['offsite_verified'] is False
    assert result['backup']['restore_verified'] is False
    assert 'path' not in result['backup']

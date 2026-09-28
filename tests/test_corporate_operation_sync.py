"""Adding B/C catalogs must not silently change existing S extraction jobs."""
from datetime import date

import pytest

from backend.corporate_api import manage_exports, catalog_source, operation_source
from backend.corporate_api.registry import resources_for_domain, S_MODELS, MODELS
from backend.corporate_api.operation_contracts import OPERATION_MODELS
from backend.corporate_api.service import worker_command


def test_domains_are_explicit_and_partition_registry():
    assert set(resources_for_domain('production')) == set(S_MODELS)
    assert set(resources_for_domain('operations')) == set(OPERATION_MODELS)
    assert set(resources_for_domain('all')) == set(MODELS)
    assert not set(S_MODELS) & set(OPERATION_MODELS)


def test_operation_catalog_extraction_does_not_query_production(monkeypatch):
    calls = []
    selected = ['oprt.vesselType']
    dataset = {'ready': True, 'blockers': [], 'rows': [], 'warnings': []}

    def operations(query_fn, *, profile, resources):
        calls.append((profile, resources))
        return {'oprt.vesselType': dataset}

    def forbidden(*args, **kwargs):
        raise AssertionError('S data must not be read for an operation-only extraction')

    monkeypatch.setattr(operation_source, 'extract_operation_catalogs', operations)
    monkeypatch.setattr(catalog_source, 'extract_catalogs', forbidden)
    result = manage_exports.extract({'terminals': ['cua_lo']}, date(2025, 1, 1),
                                     date(2026, 12, 31), selected, query_fn=forbidden)
    assert result['datasets'] == {'oprt.vesselType': dataset}
    assert calls == [({'terminals': ['cua_lo']}, selected)]


def test_unknown_resource_is_rejected_before_query():
    with pytest.raises(ValueError, match='Unknown'):
        manage_exports.extract({}, date(2026, 1, 1), date(2026, 1, 2), ['unknown'])


def test_service_operations_domain_requires_explicit_setting(tmp_path):
    profile = tmp_path / 'profile.json'
    profile.write_text('{}')
    env = {'CORPORATE_SYNC_ENABLED': 'true', 'CORPORATE_SYNC_PROFILE': str(profile)}
    assert '--domain' not in worker_command(env)
    assert worker_command({**env, 'CORPORATE_SYNC_DOMAIN': 'operations'})[-2:] == ['--domain', 'operations']
    with pytest.raises(ValueError, match='domain'):
        worker_command({**env, 'CORPORATE_SYNC_DOMAIN': 'arbitrary'})


def test_publication_does_not_trust_ready_flag_for_missing_operation_dates(tmp_path):
    from backend.corporate_api.store import ExportStore
    from test_corporate_operation_api import fixture_row, _preview
    profile = {'approved': True}
    resource = 'oprt.vesselType'
    preview = _preview({resource: [fixture_row(resource, createdDate=None, modifiedDate=None)]}, profile)
    store = ExportStore(tmp_path / 'exports.sqlite3')
    with pytest.raises(ValueError, match='missing change dates'):
        manage_exports.publish_preview(preview, profile, store, [resource])
    assert store.describe() == []

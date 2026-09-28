"""Private source diagnostics and the public API boundary; no live SQL."""
from datetime import date

import pytest

from backend.corporate_api.manage_exports import extract, publish_preview
from backend.corporate_api.reconciliation import reconciliation_report
from backend.corporate_api.store import ExportStore
from test_corporate_operation_source import Query
from test_corporate_operation_api import api, query


RESOURCE = 'oprt.cargoDirect'
PERIOD = date(2026, 9, 1), date(2026, 9, 30)
PROFILE = {'approved': True}


def preview(source):
    return extract(PROFILE, *PERIOD, [RESOURCE], source)


def report(source):
    return reconciliation_report(preview(source), PROFILE)['resources'][RESOURCE]


def test_private_report_counts_all_read_rows_and_keeps_both_error_groups():
    source = Query()
    base = source.data['SmartTOS']['CargoDirect'][0]
    source.data['SmartTOS']['CargoDirect'] = [
        dict(base, cargoDirectId=1),
        dict(base, cargoDirectId=2, rowDeleted=2),
        dict(base, cargoDirectId=3, createTime=None, updateTime=None),
        dict(base, cargoDirectId=4, rowDeleted=2),
    ]
    result = report(source)
    assert not result['extractionReady'] and not result['deliveryReady']
    assert result['rows'] == 0  # No partial API payload.
    assert result['sourceRows'] == 5 and result['sourceRowsKnown'] == 5
    assert result['sourceCountComplete'] and result['sourceCountUnknownTerminals'] == []
    assert result['sourceCountBasis'] == 'raw_rows'
    left, right = result['sourceDiagnostics']
    assert (left['raw_row_count'], left['valid_row_count'], left['invalid_row_count']) == (4, 1, 3)
    assert right['raw_row_count'] == 1
    assert [(g['code'], g['count']) for g in left['issue_groups']] == [
        ('SOURCE_DATA', 2), ('SOURCE_CHANGE_DATE_MISSING', 1)]


@pytest.mark.parametrize('failure', ['query', 'schema'])
def test_mixed_success_and_failed_source_has_unknown_total_not_false_zero(failure):
    source = Query()
    if failure == 'query':
        source.fail_db = 'SmartTOS_BenThuy'
    else:
        del source.schema['SmartTOS_BenThuy']['CargoDirect']['cargoDirectId']
    result = report(source)
    assert result['sourceRows'] is None
    assert result['sourceRowsKnown'] == 1
    assert not result['sourceCountComplete']
    assert result['sourceCountUnknownTerminals'] == ['ben_thuy']
    assert result['sourceDiagnostics'][1]['raw_row_count'] is None
    assert 'secret-connection-must-not-leak' not in str(result)


def test_verified_empty_source_and_missing_mapping_are_distinct():
    source = Query()
    for db in source.data:
        source.data[db]['CargoDirect'] = []
    empty = report(source)
    assert empty['sourceRows'] == 0 and empty['sourceCountComplete']
    blocked = extract({'operation_sources': {RESOURCE: {'columns': {'cargoDirectName': 'name'}}}},
                      *PERIOD, [RESOURCE], Query())
    unknown = reconciliation_report(blocked, {})['resources'][RESOURCE]
    assert unknown['sourceRows'] is None and not unknown['sourceCountComplete']
    assert unknown['sourceCountUnknownTerminals'] == ['cua_lo', 'ben_thuy']


def test_private_diagnostics_never_enter_successful_or_blocked_public_json(api, tmp_path):
    draft = preview(Query())
    private = reconciliation_report(draft, PROFILE)
    assert private['resources'][RESOURCE]['sourceDiagnostics']
    publish_preview(draft, PROFILE, api['exports'], [RESOURCE])
    response = api['client'].get('/api/oprt/catalog/cargoDirect', params=query(startDate='20250101'),
                                 headers=api['headers'])
    assert response.status_code == 200, response.text
    assert set(response.json()) == {'data', 'code', 'message'}
    assert 'sourceDiagnostics' not in response.text and 'raw_row_count' not in response.text
    source = Query()
    source.data['SmartTOS']['CargoDirect'][0]['rowDeleted'] = 2
    failed = preview(source)
    assert reconciliation_report(failed, PROFILE)['resources'][RESOURCE]['sourceDiagnostics'][0]['issue_groups']
    with pytest.raises(ValueError, match='not ready'):
        publish_preview(failed, PROFILE, api['exports'], [RESOURCE])
    # The existing published rows remain intact after the rejected update.
    again = api['client'].get('/api/oprt/catalog/cargoDirect', params=query(startDate='20250101'),
                              headers=api['headers'])
    assert again.status_code == 200 and again.json()['data'] == response.json()['data']
    assert 'issue_groups' not in again.text and 'sourceDiagnostics' not in again.text
    api['app'].state.corporate_exports = ExportStore(tmp_path / 'unpublished.sqlite3')
    unavailable = api['client'].get('/api/oprt/catalog/cargoDirect', params=query(), headers=api['headers'])
    assert unavailable.status_code == 503
    assert set(unavailable.json()) == {'data', 'code', 'message'}
    assert unavailable.json()['code'] == '0'
    assert unavailable.headers['X-Error-Code'] == 'DATASET_NOT_READY'
    assert 'sourceDiagnostics' not in unavailable.text and 'issue_groups' not in unavailable.text

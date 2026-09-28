"""Standalone operation previews must prove their own rows before readiness."""
from copy import deepcopy
import json

import pytest

from backend.corporate_api.reconciliation import reconciliation_report


RESOURCE = 'oprt.portOpTeam'
BASE = {'companyId': 'CNT', 'reportDate': '2026-09-27', 'createdDate': '2026-01-01T00:00:00',
        'teamId': 'team-sensitive-id', 'status': 1}


def draft(*rows):
    return {'companyId': 'CNT', 'sourceReadAt': '2026-09-27T01:00:00+00:00',
            'startDate': '20260901', 'endDate': '20260927',
            'datasets': {RESOURCE: {'rows': list(rows), 'ready': True, 'blockers': []}}}


def report(preview):
    return reconciliation_report(preview, {'approved': True})['resources'][RESOURCE]


def test_valid_standalone_operation_does_not_need_production_coverage_or_period_change_date():
    result = report(draft(dict(BASE)))
    assert result['deliveryReady'] and result['rowValidation']['schemaReady']
    assert result['rowValidation']['coverageReady'] is None
    assert result['rowValidation']['rowsOutsidePeriod'] == 0
    assert result['referenceChecks'] == []


@pytest.mark.parametrize('change,counter', [
    ({'status': True}, 'invalidRows'),
    ({'status': 9}, 'invalidRows'),
    ({'companyId': 'OTHER'}, 'otherCompanyRows'),
    ({'teamId': None}, 'rowsWithoutIdentity'),
    ({'teamId': '   '}, 'rowsWithoutIdentity'),
    ({'reportDate': '2026-02-30'}, 'invalidDateRows'),
    ({'createdDate': '2026-02-30T00:00:00'}, 'invalidDateRows'),
    ({'createdDate': None}, 'rowsMissingChangeDate'),
    ({'createdDate': None, 'modifiedDate': 'sensitive-invalid-date'}, 'invalidDateRows'),
])
def test_ready_flag_cannot_hide_invalid_standalone_row(change, counter):
    preview = draft({**BASE, **change})
    before = deepcopy(preview)
    result = report(preview)
    assert result['extractionReady'] and not result['deliveryReady']
    assert not result['rowValidation']['schemaReady']
    assert result['rowValidation'][counter] == 1
    assert result['referenceReady'] and result['referenceChecks'] == []
    assert 'sensitive' not in json.dumps(result)
    assert preview == before


def test_modified_date_alone_is_sufficient_but_invalid_other_date_is_not_ignored():
    preview = draft({**BASE, 'createdDate': None, 'modifiedDate': '2026-09-27T00:00:00'})
    assert report(preview)['deliveryReady']
    preview['datasets'][RESOURCE]['rows'][0]['createdDate'] = '2026-02-30T00:00:00'
    result = report(preview)
    assert not result['deliveryReady']
    assert result['rowValidation']['invalidDateRows'] == 1


def test_duplicate_standalone_identity_blocks_readiness():
    result = report(draft(dict(BASE), dict(BASE)))
    assert not result['deliveryReady']
    assert result['rowValidation']['duplicateIdentityRows'] == 1


def test_non_object_standalone_row_is_counted_without_exposing_payload():
    result = report(draft('sensitive-invalid-row'))
    assert not result['deliveryReady']
    assert result['rowValidation']['invalidRows'] == 1
    assert 'sensitive' not in json.dumps(result)


def test_verified_empty_operation_catalog_has_no_artificial_date_or_coverage_error():
    result = report(draft())
    assert result['deliveryReady'] and result['rowValidation']['schemaReady']
    assert result['rowValidation']['coverageReady'] is None
    assert result['rowValidation']['rowsMissingChangeDate'] == 0

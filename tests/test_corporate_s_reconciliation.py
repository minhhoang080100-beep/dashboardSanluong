"""S delivery diagnostics prove schema, period and preview-only FK readiness."""
from copy import deepcopy
from datetime import date
import json

import pytest

from backend.corporate_api.contracts import MODELS, IDENTITY
from backend.corporate_api.manage_exports import extract, references_for
from backend.corporate_api.reconciliation import reconciliation_report, _s_references
from test_corporate_end_to_end import FullSource, full_profile


RESOURCE = 'contQuayVolumesCB'
PERIOD = date(2026, 9, 16), date(2026, 9, 16)


@pytest.fixture
def draft():
    profile = full_profile()
    return extract(profile, *PERIOD, list(MODELS), FullSource()), profile


def report(draft, resource=RESOURCE):
    preview, profile = draft
    return reconciliation_report(preview, profile)['resources'][resource]


def test_complete_s_preview_has_valid_contract_coverage_and_resolved_catalogs(draft):
    all_rows = reconciliation_report(*draft)['resources']
    assert len(all_rows) == 12
    assert all(item['deliveryReady'] for item in all_rows.values())
    production = all_rows[RESOURCE]
    assert production['rowValidation']['schemaReady']
    assert production['rowValidation']['coverageReady']
    assert production['referenceReady'] and production['referenceValidationScope'] == 'preview_only'
    assert all(item['status'] in {'ready', 'not_required'} for item in production['referenceChecks'])


def test_s_preview_checks_cannot_drift_from_publication_reference_contracts():
    for resource, model in MODELS.items():
        expected = {field: target for field, (target, _) in references_for(resource).items()
                    if field in model.model_fields and field != IDENTITY.get(resource)}
        assert _s_references(resource) == expected


def test_production_without_catalogs_does_not_claim_delivery_ready(draft):
    preview, _ = draft
    preview['datasets'] = {RESOURCE: preview['datasets'][RESOURCE]}
    result = report(draft)
    assert result['extractionReady'] and not result['deliveryReady']
    assert result['referenceReady'] is False
    checks = {item['field']: item for item in result['referenceChecks']}
    assert checks['shipId']['status'] == 'target_not_in_preview'
    assert checks['shipId']['targetResource'] == 'shipDetails'
    assert checks['shipId']['targetIdentityField'] == 'shipId'
    assert checks['shipId']['missingReferenceCount'] is None
    assert checks['originId']['status'] == 'not_required'


@pytest.mark.parametrize('change,field,counter', [
    ({'containerTEU': -1}, 'invalidRows', 1),
    ({'containerWeight': None}, 'invalidRows', 1),
    ({'shipId': ''}, 'invalidRows', 1),
    ({'companyId': 'OTHER'}, 'otherCompanyRows', 1),
    ({'finishDate': '20260917'}, 'rowsOutsidePeriod', 1),
    ({'finishDate': '20260230'}, 'invalidDateRows', 1),
    ({'finishDate': '20260230', 'reportDate': '20269999'}, 'invalidDateRows', 1),
])
def test_bad_s_payload_cannot_inherit_ready_flag_or_contribute_to_totals(draft, change, field, counter):
    preview, _ = draft
    preview['datasets'][RESOURCE]['rows'][0].update(change)
    result = report(draft)
    assert result['extractionReady'] and not result['deliveryReady']
    assert not result['rowValidation']['schemaReady']
    assert result['rowValidation'][field] == counter
    assert result['totals'] == {} and result['byDayShip'] == []


@pytest.mark.parametrize('coverage', [None, [], [['20260901', '20260930']], [['20260916', '20260917']]])
def test_missing_or_mismatched_coverage_blocks_delivery_even_if_rows_are_valid(draft, coverage):
    preview, _ = draft
    preview['datasets'][RESOURCE]['coverage'] = coverage
    result = report(draft)
    assert result['rowValidation']['schemaReady'] and not result['rowValidation']['coverageReady']
    assert not result['deliveryReady']
    assert result['totals']['containerWeight'] == '2.250'


def test_invalid_preview_calendar_period_cannot_prove_complete_coverage(draft):
    preview, _ = draft
    preview['startDate'] = preview['endDate'] = '20260230'
    preview['datasets'][RESOURCE]['coverage'] = [['20260230', '20260230']]
    result = report(draft)
    assert not result['rowValidation']['coverageReady'] and not result['deliveryReady']


@pytest.mark.parametrize('state,status', [
    ('blocked', 'target_not_ready'), ('missing_id', 'missing_identity'),
    ('invalid_row', 'target_invalid'), ('duplicate_id', 'target_invalid'),
])
def test_s_dependency_must_itself_be_ready_valid_and_have_unique_identity(draft, state, status):
    preview, _ = draft
    dependency = preview['datasets']['containerSize']
    if state == 'blocked':
        dependency['blockers'] = ['SOURCE_DATA_INCOMPLETE']
    elif state == 'missing_id':
        dependency['rows'] = []
    elif state == 'invalid_row':
        dependency['rows'][0]['localSzTp'] = None
    else:
        dependency['rows'].append(deepcopy(dependency['rows'][0]))
    result = report(draft)
    check = next(item for item in result['referenceChecks'] if item['field'] == 'containerSizeId')
    assert check['status'] == status
    assert not result['referenceReady'] and not result['deliveryReady']


def test_s_catalog_checks_its_own_contract_and_catalog_relations(draft):
    preview, _ = draft
    catalog = preview['datasets']['cargoCategory']
    catalog['rows'][0]['cargoTypeId'] = 'sensitive-missing-type'
    result = report(draft, 'cargoCategory')
    assert not result['deliveryReady'] and not result['referenceReady']
    assert any(item['status'] == 'missing_identity' for item in result['referenceChecks'])
    assert 'sensitive-missing-type' not in json.dumps(result)
    catalog['rows'].append(deepcopy(catalog['rows'][0]))
    result = report(draft, 'cargoCategory')
    assert not result['rowValidation']['schemaReady']
    assert result['rowValidation']['duplicateIdentityRows'] == 1


def test_verified_empty_production_requires_period_but_no_unused_catalogs(draft):
    preview, _ = draft
    data = preview['datasets'][RESOURCE]
    data['rows'] = []
    preview['datasets'] = {RESOURCE: data}
    result = report(draft)
    assert result['deliveryReady'] and result['referenceReady']
    assert all(item['status'] == 'not_required' for item in result['referenceChecks'])
    data['coverage'] = []
    assert not report(draft)['deliveryReady']


def test_partial_diagnostics_keep_valid_totals_and_do_not_mutate_preview(draft):
    preview, _ = draft
    data = preview['datasets'][RESOURCE]
    data['rows'].append({**data['rows'][0], 'containerTEU': '-1'})
    data.update(ready=False, blockers=['SOURCE_DATA_INCOMPLETE'])
    before = deepcopy(preview)
    result = report(draft)
    assert not result['deliveryReady']
    assert result['rowValidation']['invalidRows'] == 1
    assert result['totals']['containerWeight'] == '2.250'
    assert preview == before


def test_non_object_s_row_is_counted_without_exposing_input(draft):
    preview, _ = draft
    preview['datasets'][RESOURCE]['rows'] = ['sensitive-invalid-payload']
    result = report(draft)
    assert not result['deliveryReady'] and result['rowValidation']['invalidRows'] == 1
    assert 'sensitive-invalid-payload' not in json.dumps(result)

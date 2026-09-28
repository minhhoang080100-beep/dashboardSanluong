"""Preview-only FK readiness matches publication, without SQL or source payloads."""
from copy import deepcopy
from datetime import datetime, timezone
import json

import pytest

from backend.corporate_api.manage_exports import FORMAT, profile_digest, validate_preview
from backend.corporate_api.reconciliation import reconciliation_report
from backend.corporate_api.store import ExportStore


PROFILE = {'approved': True, 'terminals': ['cua_lo'], 'operation_sources': {
    'oprt.portOpTeam': {'table': 'WorkTeam'}, 'oprt.portOpStaff': {'table': 'Staff'}}}
BASE = {'companyId': 'CNT', 'reportDate': '2026-09-25', 'createdDate': '2026-09-01T00:00:00'}
YARD = 'oprt.portWHYard'
TYPE = 'oprt.portWHYardType'


def dataset(*rows, ready=True, blockers=None):
    return {'rows': list(rows), 'ready': ready, 'blockers': blockers or []}


def preview(datasets):
    return {'format': FORMAT, 'companyId': 'CNT', 'sourceReadAt': '2026-09-25T01:00:00+00:00',
            'startDate': '20260901', 'endDate': '20260925', 'profileDigest': profile_digest(PROFILE),
            'datasets': datasets}


def yard(kind='type-1', **overrides):
    return dict(BASE, whYardId='yard-1', whYardCode='Y1', whYardTypeId=kind, **overrides)


def yard_type(**overrides):
    return {**BASE, 'whTypeId': 'type-1', 'whYardTypeCode': 'WT1', **overrides}


def result(draft, resource=YARD, profile=PROFILE):
    return reconciliation_report(draft, profile)['resources'][resource]


@pytest.mark.parametrize('resource', [YARD, 'oprt.contwhYards'])
def test_blocked_target_prevents_false_delivery_readiness_even_if_identity_exists(resource):
    draft = preview({resource: dataset(yard()),
                     TYPE: dataset(yard_type(), ready=False, blockers=['SOURCE_CHANGE_DATE_MISSING'])})
    reported = result(draft, resource)
    assert reported['extractionReady'] and not reported['deliveryReady']
    assert reported['referenceReady'] is False
    check, = reported['referenceChecks']
    assert check['status'] == 'target_not_ready'
    assert check['targetIdentityField'] == 'whTypeId'
    assert check['referenceCount'] == 1 and check['missingReferenceCount'] == 0


def test_target_not_in_preview_is_uncertain_even_when_previously_published(tmp_path):
    store = ExportStore(tmp_path / 'export.sqlite3')
    store.publish(TYPE, 'CNT', [yard_type()], read_at=datetime.now(timezone.utc).isoformat(), rule_version='test')
    draft = preview({YARD: dataset(yard())})
    # Actual publication may use an existing catalog; the private preview report
    # must not claim to have validated that external state or query it itself.
    validate_preview(draft, PROFILE, store, [YARD])
    reported = result(draft)
    assert not reported['deliveryReady']
    assert reported['referenceValidationScope'] == 'preview_only'
    check, = reported['referenceChecks']
    assert check['status'] == 'target_not_in_preview'
    assert check['missingReferenceCount'] is None


def test_ready_catalog_missing_referenced_identity_matches_publication_gate(tmp_path):
    draft = preview({YARD: dataset(yard('missing')), TYPE: dataset(yard_type())})
    check, = result(draft)['referenceChecks']
    assert check['status'] == 'missing_identity'
    assert check['missingReferenceCount'] == check['missingDistinctReferenceCount'] == 1
    assert not result(draft)['deliveryReady']
    with pytest.raises(ValueError, match='unresolved reference whYardTypeId'):
        validate_preview(draft, PROFILE, ExportStore(tmp_path / 'export.sqlite3'), list(draft['datasets']))


def test_lists_count_each_reference_and_each_affected_row_without_identifier_disclosure():
    equipment = {**BASE, 'equipmentId': 'equipment-sensitive-id', 'equipmentCode': 'EQ',
                 'equipmentTypeId': 'type-sensitive-id', 'serialCode': 'serial-sensitive',
                 'registrationCode': 'registration-sensitive', 'isRent': 0,
                 'staffCd': 'staff-sensitive', 'operationLocationTypeId': ['loc-1', 'missing-sensitive', 'loc-1']}
    equipment_type = {**BASE, 'equipmentTypeId': 'type-sensitive-id', 'equipmentTypeCode': 'ET'}
    location = {**BASE, 'operationLocationTypeId': 'loc-1', 'operationLocationTypeCode': 'L1'}
    draft = preview({'oprt.portEquipment': dataset(equipment, {**equipment, 'equipmentId': 'EQ2'}),
                     'oprt.portEquipType': dataset(equipment_type),
                     'oprt.operationLocationType': dataset(location)})
    reported = result(draft, 'oprt.portEquipment')
    checks = {check['field']: check for check in reported['referenceChecks']}
    assert checks['equipmentTypeId']['status'] == 'ready'
    loc = checks['operationLocationTypeId']
    assert loc['status'] == 'missing_identity'
    assert (loc['rowsWithReference'], loc['referenceCount'], loc['distinctReferenceCount']) == (2, 6, 2)
    assert (loc['missingReferenceCount'], loc['missingDistinctReferenceCount']) == (2, 1)
    assert not reported['deliveryReady']
    assert 'sensitive' not in json.dumps(reported)


@pytest.mark.parametrize('kind', [None, []])
def test_absent_optional_reference_does_not_require_unrelated_target(kind):
    # [] is the optional equipment list shape; scalar yard FK uses None.
    if kind is None:
        draft = preview({YARD: dataset(yard(None))})
        reported = result(draft)
    else:
        equipment = {**BASE, 'equipmentId': 'E1', 'equipmentCode': 'E1', 'equipmentTypeId': 'T1',
                     'serialCode': 'S1', 'registrationCode': 'R1', 'isRent': 0,
                     'operationLocationTypeId': []}
        draft = preview({'oprt.portEquipment': dataset(equipment),
                         'oprt.portEquipType': dataset({**BASE, 'equipmentTypeId': 'T1', 'equipmentTypeCode': 'T1'})})
        reported = result(draft, 'oprt.portEquipment')
    assert reported['deliveryReady'] and reported['referenceReady']
    assert reported['referenceChecks'][-1]['status'] == 'not_required'


def test_soft_deleted_target_is_retained_and_resolves_like_publication(tmp_path):
    draft = preview({YARD: dataset(yard(isDeleted=1)), TYPE: dataset(yard_type(isDeleted=1))})
    assert result(draft)['deliveryReady']
    validate_preview(draft, PROFILE, ExportStore(tmp_path / 'export.sqlite3'), list(draft['datasets']))


@pytest.mark.parametrize('target_row,counter', [
    (yard_type(whTypeId=None), 'targetRowsWithoutIdentity'),
    (yard_type(whYardTypeCode=None), 'targetInvalidRows'),
    (yard_type(createdDate=None), 'targetRowsMissingChangeDate'),
    (yard_type(companyId='OTHER'), 'targetOtherCompanyRows'),
])
def test_ready_flag_cannot_make_an_invalid_target_proof_of_readiness(target_row, counter):
    draft = preview({YARD: dataset(yard()), TYPE: dataset(target_row)})
    check, = result(draft)['referenceChecks']
    assert check['status'] == 'target_invalid' and check[counter] == 1
    assert not result(draft)['deliveryReady']


def test_duplicate_target_identity_blocks_proof_and_preview_is_not_mutated():
    draft = preview({YARD: dataset(yard()), TYPE: dataset(yard_type(), yard_type())})
    before = deepcopy(draft)
    reported = result(draft)
    assert not reported['deliveryReady']
    assert reported['referenceChecks'][0]['targetDuplicateIdentityRows'] == 1
    assert draft == before


def test_profile_and_dataset_blockers_still_gate_delivery_when_references_resolve():
    draft = preview({YARD: dataset(yard()), TYPE: dataset(yard_type())})
    assert result(draft)['deliveryReady']
    assert not result(draft, profile={'approved': False})['deliveryReady']
    draft['datasets'][YARD]['blockers'] = ['INCOMPLETE_SOURCE']
    assert result(draft)['referenceReady']
    assert not result(draft)['deliveryReady']


def test_empty_catalog_has_no_references_to_prove():
    reported = result(preview({YARD: dataset()}))
    assert reported['deliveryReady']
    assert reported['referenceChecks'][0]['status'] == 'not_required'


@pytest.mark.parametrize('resource,source_row,target,target_row,field', [
    ('oprt.portOpStaff', {**BASE, 'staffId': 'staff-1', 'teamId': 'team-1', 'status': 1},
     'oprt.portOpTeam', {**BASE, 'teamId': 'team-1', 'status': 1}, 'teamId'),
    ('oprt.cargoItems', {**BASE, 'cargoItemId': 'cargo-1', 'cargoItemCode': 'C1',
                        'cargoGroupId': 'group-1', 'dangerousGoodsCheck': 0},
     'oprt.cargoGroups', {**BASE, 'cargoGroupId': 'group-1', 'cargoGroupCode': 'G1'}, 'cargoGroupId'),
])
def test_remaining_catalog_dependencies_use_their_own_native_identity(
        tmp_path, resource, source_row, target, target_row, field):
    draft = preview({resource: dataset(source_row), target: dataset(target_row)})
    assert result(draft, resource)['deliveryReady']
    validate_preview(draft, PROFILE, ExportStore(tmp_path / 'export.sqlite3'), list(draft['datasets']))
    draft['datasets'][target]['rows'] = []
    check, = result(draft, resource)['referenceChecks']
    assert check['field'] == field and check['status'] == 'missing_identity'
    assert not result(draft, resource)['deliveryReady']


def test_target_blocker_is_not_overridden_by_true_ready_flag():
    draft = preview({YARD: dataset(yard()), TYPE: dataset(yard_type(), blockers=['INCOMPLETE_SOURCE'])})
    check, = result(draft)['referenceChecks']
    assert check['status'] == 'target_not_ready'
    assert not result(draft)['deliveryReady']


def test_private_identity_difference_aggregates_are_preserved_in_report():
    differences = [{'kind': 'dates_only', 'count': 4, 'fields': ['createdDate'], 'samples': []}]
    draft = preview({TYPE: {**dataset(yard_type()), 'identity_differences': differences}})
    assert result(draft, TYPE)['identityDifferences'] == differences

"""Contract checks for operations catalogs against Bulk/Container v1.5 tables."""
from datetime import date
from decimal import Decimal

import pytest
from pydantic import ValidationError

from backend.corporate_api.contracts import MODELS as S_MODELS
from backend.corporate_api.errors import CorporateError
from backend.corporate_api.operation_contracts import (
    OPERATION_DETAIL_PATHS, OPERATION_FILTERS, OPERATION_IDENTITY,
    OPERATION_MODELS, OPERATION_PATHS, OPERATION_REFERENCES, OperationQuery,
)


BASE = {'reportDate': '2026-09-25', 'companyId': 'CNT'}
EXPECTED_REQUIRED = {
    'portEquipment': {'equipmentId', 'equipmentCode', 'equipmentTypeId', 'serialCode', 'registrationCode', 'isRent'},
    'portEquipType': {'equipmentTypeId', 'equipmentTypeCode'},
    'portWHYard': {'whYardId', 'whYardCode'},
    'portWHYardType': {'whTypeId', 'whYardTypeCode'},
    'berths': {'berthId', 'berthCode'},
    'jobType': {'jobTypeId', 'jobTypeCode'},
    'jobMethod': {'jobMethodId', 'jobMethodCode'},
    'deliveryMethod': {'deliveryMethodId', 'deliveryMethodCode'},
    'serviceType': {'serviceTypeId', 'serviceTypeCode'},
    'cargoItems': {'cargoItemId', 'cargoItemCode', 'cargoGroupId', 'dangerousGoodsCheck'},
    'cargoGroups': {'cargoGroupId', 'cargoGroupCode'},
    'unitMeasurement': {'unitId', 'unitCode'},
    'cargoDirect': {'cargoDirectId', 'cargoDirectCode'},
    'operationLocationType': {'operationLocationTypeId', 'operationLocationTypeCode'},
    'portOpTeam': {'teamId', 'status'},
    'portOpStaff': {'staffId', 'teamId', 'status'},
    'vesselType': {'vesselTypeId', 'vesselTypeCode'},
    'equipments': {'equipmentId', 'equipmentCode', 'equipmentName', 'equipmentTypeCode1',
                   'equipmentTypeName1', 'equipmentTypeCode2', 'equipmentTypeName2',
                   'manufacturerName', 'serialCode', 'registrationCode', 'installDate',
                   'currentRunningHour', 'isRent'},
    'contwhYards': {'whYardId', 'whYardCode'},
    'contSizeType': {'contSizeTypeId', 'contSizeTypeCode'},
}


def minimal_row(tail):
    row = {**BASE, **{field: '17' for field in EXPECTED_REQUIRED[tail]}}
    for field in ('isRent', 'dangerousGoodsCheck'):
        if field in row:
            row[field] = False
    if 'status' in row:
        row['status'] = 1
    if 'installDate' in row:
        row['installDate'] = '2025-01-01'
    if 'currentRunningHour' in row:
        row['currentRunningHour'] = 0
    return row


def test_scope_namespaces_and_exact_paths():
    assert len(OPERATION_MODELS) == 20
    assert set(OPERATION_MODELS).isdisjoint(S_MODELS)
    assert set(OPERATION_MODELS) == set(OPERATION_IDENTITY) == set(OPERATION_PATHS) == set(OPERATION_FILTERS)
    assert OPERATION_PATHS['oprt.portOpTeam'] == '/api/oprt/portOpTeam'
    assert OPERATION_PATHS['oprt.portOpStaff'] == '/api/oprt/portOpStaff'
    assert OPERATION_PATHS['oprt.berths'] == '/api/oprt/catalog/berths'
    assert OPERATION_PATHS['oprt.contwhYards'] == '/api/oprt/catalog/contwhYards'
    assert OPERATION_DETAIL_PATHS == {}


def test_berth_signed_depth_preserved_and_nonfinite_depth_rejected():
    model = OPERATION_MODELS['oprt.berths']
    value = model.model_validate({**minimal_row('berths'), 'berthDepth': Decimal('-13.000')})
    assert value.model_dump(mode='json')['berthDepth'] == -13.0
    for depth in ('NaN', 'Infinity', '-Infinity'):
        with pytest.raises(ValidationError):
            model.model_validate({**minimal_row('berths'), 'berthDepth': depth})


@pytest.mark.parametrize('tail', ['portOpTeam', 'portOpStaff'])
@pytest.mark.parametrize('status', [True, False])
def test_roster_status_boolean_is_not_a_business_state(tail, status):
    with pytest.raises(ValidationError, match='status'):
        OPERATION_MODELS['oprt.' + tail].model_validate({**minimal_row(tail), 'status': status})


@pytest.mark.parametrize('tail', ['portOpTeam', 'portOpStaff'])
@pytest.mark.parametrize('status', [1, 2, 3, Decimal('1'), Decimal('2'), Decimal('3'), 1.0])
def test_roster_status_preserves_supported_numeric_codes(tail, status):
    row = OPERATION_MODELS['oprt.' + tail].model_validate({**minimal_row(tail), 'status': status})
    assert row.model_dump(mode='json')['status'] == int(status)


@pytest.mark.parametrize('tail', EXPECTED_REQUIRED)
def test_required_table_fields_and_native_identifier_round_trip(tail):
    model = OPERATION_MODELS['oprt.' + tail]
    assert {name for name, field in model.model_fields.items() if field.is_required()} == EXPECTED_REQUIRED[tail] | set(BASE)
    row = minimal_row(tail)
    id_field = OPERATION_IDENTITY['oprt.' + tail]
    row[id_field] = '00017'
    output = model.model_validate(row).model_dump(mode='json')
    assert output[id_field] == '00017'  # Do not prefix or coerce away leading zeroes.
    assert output['reportDate'] == '2026-09-25'
    assert output['isDeleted'] is None  # Absence is not evidence of not-deleted.
    for field in EXPECTED_REQUIRED[tail]:
        with pytest.raises(ValidationError):
            model.model_validate({**row, field: None})


@pytest.mark.parametrize('tail', EXPECTED_REQUIRED)
def test_internal_and_undeclared_fields_never_leak(tail):
    model = OPERATION_MODELS['oprt.' + tail]
    for unexpected in ('sourceDatabase', 'Metadata', 'rowguid'):
        with pytest.raises(ValidationError):
            model.model_validate({**minimal_row(tail), unexpected: 'internal'})


def test_bulk_and_container_equipment_are_distinct_contracts():
    bulk = OPERATION_MODELS['oprt.portEquipment']
    container = OPERATION_MODELS['oprt.equipments']
    assert bulk is not container
    assert 'equipmentTypeId' in bulk.model_fields and 'equipmentTypeId' not in container.model_fields
    assert 'maxCapacity' in bulk.model_fields and 'maxWGT' in container.model_fields
    for old in ('equipmentTypeCode2', 'equipmentTypeName1', 'bayCode', 'rsCheck', 'maxJob', 'quayYTCheck'):
        with pytest.raises(ValidationError):
            bulk.model_validate({**minimal_row('portEquipment'), old: 'old'})
    with pytest.raises(ValidationError):
        container.model_validate(minimal_row('portEquipment'))


def test_deleted_bulk_yard_and_berth_fields_not_in_wire_contract():
    fields = OPERATION_MODELS['oprt.portWHYard'].model_fields
    for old in ('equipment', 'blockCode', 'bayFrom', 'maxStackTier', 'importChk', 'occupancyRate'):
        assert old not in fields
        assert old in OPERATION_MODELS['oprt.contwhYards'].model_fields
    assert 'equipment' not in OPERATION_MODELS['oprt.berths'].model_fields


def test_cargo_table_duplicates_and_sample_only_metadata_are_not_invented():
    group = OPERATION_MODELS['oprt.cargoGroups']
    item = OPERATION_MODELS['oprt.cargoItems']
    assert 'isUpdated' not in group.model_fields
    assert 'isUpdated' not in item.model_fields
    assert 'cargoGroupShortName' not in group.model_fields
    assert 'cargoItemShortName' in item.model_fields


def test_reference_target_identity_mismatch_and_lists_are_explicit():
    assert OPERATION_IDENTITY['oprt.portWHYardType'] == 'whTypeId'
    assert OPERATION_REFERENCES['oprt.portWHYard']['whYardTypeId'] == 'oprt.portWHYardType'
    equipment = OPERATION_MODELS['oprt.portEquipment']
    output = equipment.model_validate({**minimal_row('portEquipment'), 'operationLocationTypeId': ['1', '2']})
    assert output.operationLocationTypeId == ['1', '2']
    with pytest.raises(ValidationError):
        equipment.model_validate({**minimal_row('portEquipment'), 'operationLocationTypeId': '1'})
    for source, refs in OPERATION_REFERENCES.items():
        for field, target in refs.items():
            assert field in OPERATION_MODELS[source].model_fields
            assert target in OPERATION_MODELS


def test_numeric_units_dates_and_flags_serialize_without_reclassification():
    model = OPERATION_MODELS['oprt.contSizeType']
    output = model.model_validate({**minimal_row('contSizeType'), 'sizeCode': '20', 'heightCode': '86',
                                  'teu': Decimal('1'), 'contWeight': Decimal('2.250'),
                                  'isUpdated': 1, 'isDeleted': 0,
                                  'createdDate': '2025-01-01T00:00:00+07:00'}).model_dump(mode='json')
    assert output['contWeight'] == 2.25
    assert output['sizeCode'] == '20'
    assert output['heightCode'] == '86'
    assert type(output['isDeleted']) is int and output['isDeleted'] == 0
    assert output['createdDate'] == '2025-01-01T00:00:00+07:00'
    for invalid in ('NaN', 'Infinity', '-1'):
        with pytest.raises(ValidationError):
            model.model_validate({**minimal_row('contSizeType'), 'contWeight': invalid})


@pytest.mark.parametrize('values', [
    {'startDate': '20260230', 'endDate': '20260925'},
    {'startDate': '19691231', 'endDate': '20260925'},
    {'startDate': '2026-09-01', 'endDate': '2026-09-25'},
    {'startDate': None, 'endDate': '20260925'},
    {'startDate': '20260901', 'endDate': None},
    {'startDate': '20260901', 'endDate': '20260925', 'limit': 101},
    {'startDate': '20260901', 'endDate': '20260925', 'page': 0},
    {'startDate': '20260901', 'endDate': '20260925', 'shipId': '1'},
])
def test_invalid_change_window_and_unsupported_filters_rejected(values):
    with pytest.raises(ValidationError):
        OperationQuery(companyId='CNT', **values)


def test_query_change_dates_are_required_and_ordered():
    with pytest.raises(ValidationError):
        OperationQuery(companyId='CNT')
    query = OperationQuery(companyId='CNT', startDate='19700101', endDate='20260925')
    assert query.page == 1 and query.limit == 20
    query.check_resource('oprt.contSizeType')
    with pytest.raises(CorporateError) as error:
        query.check_resource('contQuayVolumesCB')
    assert error.value.code == 'UNSUPPORTED_RESOURCE'
    reverse = OperationQuery(companyId='CNT', startDate='20260925', endDate='20260924')
    with pytest.raises(CorporateError) as error:
        reverse.check_resource('oprt.contSizeType')
    assert error.value.code == 'INVALID_PERIOD'

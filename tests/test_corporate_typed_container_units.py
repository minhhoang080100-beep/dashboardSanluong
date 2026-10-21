"""Native yard container units resolve size only through an exact source ID."""
from datetime import date
from decimal import Decimal

import pytest

from test_corporate_source import fact, profile, run, yard_fact, yard_profile


def config(yard=True):
    value = yard_profile() if yard else profile()
    value['container_size_source'] = 'native_cargo'
    value['cargo_kind_by_cargo']['cua_lo']['2055'] = 'container'
    return value


def typed_fact(code='CONT 20E', size_id=2127, **updates):
    return yard_fact(**{'cargo_id': 2055, 'cargo_name': 'Container',
                        'cargo_group_id': 1034,
                        'quantity_unit_code': code, 'quantity': 10,
                        'native_weight': Decimal('22.5'), 'weight_unit_code': 'TAN',
                        'resolved_size_cargo_id': size_id,
                        'resolved_size_cargo_name': '20F' if code == 'CONT 20F' else '20E',
                        'resolved_size_cargo_group_id': 1034, **updates})


@pytest.mark.parametrize('unit,size_id,quantity,weight', [
    ('CONT 20E', 2127, 10, Decimal('22.500')),
    ('CONT 20F', 2125, 16, Decimal('400.000')),
    ('CONT 20F', 2125, 7, Decimal('0.000')),
])
def test_explicit_source_typed_unit_resolves_yard_size_without_recomputing_mass(unit, size_id, quantity, weight):
    result, _ = run([typed_fact(unit, size_id, quantity=quantity, native_weight=weight)], config())
    data = result['contGateVolumesCB']
    assert data['ready'] and data['issue_counts'] == {} and len(data['rows']) == 1
    row = data['rows'][0]
    assert row['containerSizeId'] == str(size_id)
    assert row['containerTEU'] == quantity
    assert row['containerWeight'] == weight
    assert 'resolved_size_cargo_id' not in row


@pytest.mark.parametrize('size_id', [None, 0, -1, False, Decimal('1.5'), '20E'])
def test_typed_unit_without_unique_valid_source_size_id_is_blocked(size_id):
    result, _ = run([typed_fact(size_id=size_id)], config())
    data = result['contGateVolumesCB']
    assert not data['ready'] and data['rows'] == []
    assert any(code in data['blockers'] for code in
               ('CONTAINER_SIZE_UNMAPPED', 'CONTAINER_SIZE_RELATION_UNCONFIRMED'))


@pytest.mark.parametrize('quantity', [None, False, -1, Decimal('1.5'), Decimal('NaN')])
def test_typed_unit_does_not_resolve_missing_or_invalid_container_count(quantity):
    result, _ = run([typed_fact(quantity=quantity)], config())
    assert result['contGateVolumesCB']['rows'] == []
    assert 'CONTAINER_QUANTITY_UNAVAILABLE' in result['contGateVolumesCB']['blockers']


def test_typed_unit_does_not_fill_missing_actual_weight_from_unit_tone():
    result, _ = run([typed_fact(native_weight=None)], config())
    assert result['contGateVolumesCB']['rows'] == []
    assert 'WEIGHT_OR_UNIT_UNAVAILABLE' in result['contGateVolumesCB']['blockers']


@pytest.mark.parametrize('unit', ['CONT', 'CONT 40E', 'CONT 20', 'BAO20', 'GIO', None])
def test_unproven_unit_codes_do_not_use_resolved_size_id(unit):
    result, _ = run([typed_fact(code=unit)], config())
    data = result['contGateVolumesCB']
    if unit == 'GIO':
        assert data['rows'] == [] and data['excluded_time_rows'] == 1
    else:
        assert not data['ready'] and data['rows'] == []


@pytest.mark.parametrize('name', ['Khac', '2055', None, 'container', ' Container '])
def test_typed_resolution_does_not_replace_other_cargo_names(name):
    result, _ = run([typed_fact(cargo_name=name)], config())
    assert not result['contGateVolumesCB']['ready']
    assert result['contGateVolumesCB']['rows'] == []


@pytest.mark.parametrize('updates', [
    {'resolved_size_cargo_name': None}, {'resolved_size_cargo_name': '20F'},
    {'resolved_size_cargo_name': '20e'}, {'resolved_size_cargo_name': ' 20E '},
    {'resolved_size_cargo_group_id': None}, {'resolved_size_cargo_group_id': False},
    {'resolved_size_cargo_group_id': 0}, {'resolved_size_cargo_group_id': -1},
    {'resolved_size_cargo_group_id': 55}, {'cargo_group_id': None},
    {'cargo_group_id': False}, {'cargo_group_id': 0},
])
def test_size_candidate_must_match_exact_source_name_and_same_positive_cargo_group(updates):
    result, _ = run([typed_fact(**updates)], config())
    assert not result['contGateVolumesCB']['ready']
    assert result['contGateVolumesCB']['rows'] == []


@pytest.mark.parametrize('cargo', [None, 0, -1, False, Decimal('0.5')])
def test_typed_unit_does_not_supply_a_missing_or_invalid_original_cargo_id(cargo):
    value = config()
    value['cargo_kind_by_group'] = {'cua_lo': {'1034': 'container'}}
    result, _ = run([typed_fact(cargo_id=cargo)], value)
    assert not result['contGateVolumesCB']['ready']
    assert result['contGateVolumesCB']['rows'] == []


def test_typed_unit_must_not_override_conflicting_canonical_cargo_size():
    result, _ = run([typed_fact(cargo_name='40F')], config())
    assert not result['contGateVolumesCB']['ready']
    assert result['contGateVolumesCB']['rows'] == []


def test_ordinary_cont_canonical_size_remains_based_on_its_own_native_id():
    result, _ = run([yard_fact(cargo_id=1, cargo_name='40F', quantity_unit_code='CONT',
                              resolved_size_cargo_id=2127)], config())
    data = result['contGateVolumesCB']
    assert data['ready'] and data['rows'][0]['containerSizeId'] == '1'
    assert data['rows'][0]['containerTEU'] == 2


def test_typed_yard_size_resolution_never_reclassifies_bulk():
    value = config()
    value['cargo_kind_by_cargo']['cua_lo']['2055'] = 'bulk'
    value['cargo_type_by_cargo']['cua_lo']['2055'] = 'BULK'
    result, _ = run([typed_fact()], value)
    assert result['contGateVolumesCB']['rows'] == []
    row = result['bulkGateVolumesCB']['rows'][0]
    assert row['cargoCategoryId'] == '2055' and row['bulkWeight'] == Decimal('22.5')
    assert 'containerSizeId' not in row


def test_typed_yard_resolution_is_not_applied_to_quay_activity():
    row = fact(cargo_id=2055, cargo_name='Container', quantity_unit_code='CONT 20E',
               resolved_size_cargo_id=2127)
    result, _ = run([row], config(yard=False))
    assert not result['contQuayVolumesCB']['ready']
    assert result['contQuayVolumesCB']['rows'] == []


def test_typed_yard_resolution_is_not_applied_to_profile_mapped_sizes():
    value = config()
    value.pop('container_size_source')
    result, _ = run([typed_fact()], value)
    assert not result['contGateVolumesCB']['ready']
    assert result['contGateVolumesCB']['rows'] == []


def test_typed_yard_resolution_does_not_hide_invalid_dates_or_missing_methods():
    for updates in ({'business_date': date(2026, 10, 1)}, {'method_id': None}):
        result, _ = run([typed_fact(**updates)], config())
        assert not result['contGateVolumesCB']['ready'] and not result['contGateVolumesCB']['rows']

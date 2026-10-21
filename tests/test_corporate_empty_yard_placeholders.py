"""Known empty yard source slots must not force fake cargo or method IDs."""
from datetime import date
from decimal import Decimal

import pytest

from test_corporate_source import fact, profile, run, yard_fact, yard_profile
from backend.corporate_api.source import SourceError


def native_yard_profile():
    return {**yard_profile(), 'container_size_source': 'native_cargo'}


@pytest.mark.parametrize('cargo,endpoint,counter', [
    (0, 'contGateVolumesCB', 'excluded_placeholder_rows'),
    (0, 'bulkGateVolumesCB', 'excluded_placeholder_rows'),
    (1, 'contGateVolumesCB', 'excluded_empty_container_rows'),
    (2, 'bulkGateVolumesCB', 'excluded_empty_bulk_rows'),
])
@pytest.mark.parametrize('method', [0, None, Decimal('0.0')])
def test_exact_empty_yard_slot_needs_no_invented_method(cargo, endpoint, counter, method):
    result, _ = run([yard_fact(cargo_id=cargo, method_id=method,
                              quantity=0, native_weight=Decimal('0.000'),
                              quantity_unit_code=None, tonne_factor=None)], native_yard_profile())
    data = result[endpoint]
    assert data['ready'] and data['rows'] == [] and data['issue_counts'] == {}
    assert data[counter] == 1


@pytest.mark.parametrize('cargo', [0, 1, 2])
@pytest.mark.parametrize('updates', [
    {'quantity': None}, {'quantity': False}, {'quantity': -1}, {'quantity': 1},
    {'native_weight': None}, {'native_weight': False}, {'native_weight': -1},
    {'native_weight': Decimal('0.001')}, {'native_weight': Decimal('NaN')},
    {'business_date': None}, {'business_date': date(2026, 10, 1)},
    {'method_id': False}, {'method_id': -1}, {'method_id': Decimal('NaN')},
])
def test_yard_empty_rule_preserves_incomplete_invalid_or_positive_rows(cargo, updates):
    row = yard_fact(**{'cargo_id': cargo, 'method_id': 0, 'quantity': 0,
                      'native_weight': 0, 'quantity_unit_code': None, **updates})
    results, _ = run([row], native_yard_profile())
    endpoints = (['contGateVolumesCB', 'bulkGateVolumesCB'] if cargo == 0 else
                 ['contGateVolumesCB' if cargo == 1 else 'bulkGateVolumesCB'])
    for endpoint in endpoints:
        data = results[endpoint]
        assert not data['ready'] and data['blockers'] and data['rows'] == []
        assert data['excluded_placeholder_rows'] == 0
        assert data['excluded_empty_container_rows'] == data['excluded_empty_bulk_rows'] == 0


@pytest.mark.parametrize('cargo', [None, False, -1, 999])
def test_exact_empty_yard_rule_does_not_reclassify_missing_invalid_or_unknown_cargo(cargo):
    result, _ = run([yard_fact(cargo_id=cargo, method_id=0, quantity=0,
                              native_weight=0)], native_yard_profile())
    for endpoint in ('contGateVolumesCB', 'bulkGateVolumesCB'):
        assert result[endpoint]['blockers'] == ['CARGO_KIND_UNMAPPED']
        assert result[endpoint]['excluded_placeholder_rows'] == 0


def test_empty_yard_rule_does_not_hide_duplicate_or_missing_source_identity():
    row = yard_fact(method_id=0, quantity=0, native_weight=0)
    for rows in ([{**row, 'source_id': None}], [row, dict(row)]):
        with pytest.raises(SourceError) as caught:
            run(rows, native_yard_profile())
        assert caught.value.code == 'SOURCE_DUPLICATE'


def test_positive_totals_unchanged_when_empty_yard_slots_have_no_method():
    existing = [yard_fact(source_id=100), yard_fact(source_id=101, cargo_id=2)]
    before, _ = run(existing, native_yard_profile())
    after, _ = run(existing + [yard_fact(source_id=200 + cargo, cargo_id=cargo,
                                       method_id=0, quantity=0, native_weight=0)
                              for cargo in [0, 1, 2]], native_yard_profile())
    for endpoint in ('contGateVolumesCB', 'bulkGateVolumesCB'):
        assert before[endpoint]['ready'] and after[endpoint]['ready']
        assert after[endpoint]['rows'] == before[endpoint]['rows']


def test_quay_exact_empty_rule_still_requires_method():
    config = {**profile(), 'quay_selection': 'source_statistics',
              'container_size_source': 'native_cargo'}
    for cargo in (0, 1, 2):
        result, _ = run([fact(cargo_id=cargo, method_id=0, quantity=0,
                              native_weight=0)], config)
        endpoint = 'bulkQuayVolumesCB' if cargo == 2 else 'contQuayVolumesCB'
        assert not result[endpoint]['ready'] and result[endpoint]['blockers']


def test_method_selected_gate_does_not_gain_yard_exemption():
    config = profile()
    config['gate_method_ids'] = {'cua_lo': [20], 'ben_thuy': [20]}
    row = fact(cargo_id=2, method_id=0, quantity=0, native_weight=0,
               quay_eligible=0, vessel_type_code='Bulk Yard')
    result, _ = run([row], config)
    assert result['bulkGateVolumesCB']['unselected_source_row_count'] == 1
    assert result['bulkGateVolumesCB']['excluded_empty_bulk_rows'] == 0

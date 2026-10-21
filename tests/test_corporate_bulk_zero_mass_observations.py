"""Offline source diagnostics: explicit zero mass stays zero, never inferred."""
from copy import deepcopy
from datetime import date
from decimal import Decimal

import pytest

from backend.corporate_api.contracts import MODELS
from backend.corporate_api.router import public_result
from test_corporate_source import fact, profile, run, yard_fact, yard_profile


COUNT = 'zero_mass_positive_quantity_rows'
SAMPLES = 'zero_mass_positive_quantity_samples'
TRUNCATED = 'zero_mass_positive_quantity_samples_truncated'
CODE = 'BULK_ZERO_MASS_POSITIVE_QUANTITY'


def bulk_fact(**updates):
    return fact(**{'cargo_id': 2, 'cargo_name': 'Synthetic bulk',
                   'native_weight': Decimal('0.000'), 'quantity': Decimal('164'),
                   'weight_unit_code': 'TAN', 'quantity_unit_code': 'BEN C', **updates})


def assert_unobserved(item):
    assert item[COUNT] == 0
    assert item[SAMPLES] == []
    assert item[TRUNCATED] is False
    assert not any('trọng lượng nguồn bằng 0 nhưng số lượng dương' in text
                   for text in item['warnings'])


@pytest.mark.parametrize('gate', [False, True])
def test_valid_zero_mass_is_observed_without_filling_or_blocking(gate):
    row = bulk_fact()
    config = profile()
    name = 'bulkQuayVolumesCB'
    if gate:
        row = yard_fact(**{**row, 'method_id': 999, 'physical_voyage': 0,
                           'quay_eligible': 0, 'production_scope': 'unclassified',
                           'vessel_type_code': 'Bulk Yard'})
        config = yard_profile()
        name = 'bulkGateVolumesCB'
    before = deepcopy(row)
    results, _ = run([row], config)
    item = results[name]
    assert item['ready'] is True and item['status'] == 'ready'
    assert item['blockers'] == [] and item['issue_counts'] == {} and item['issue_samples'] == []
    assert item['rows'][0]['bulkWeight'] == 0
    assert item[COUNT] == 1 and item[TRUNCATED] is False
    assert item[SAMPLES] == [{
        'code': CODE, 'terminal': 'cua_lo', 'sourceId': '1', 'date': '2026-09-16',
        'cargoId': '2', 'methodId': '999' if gate else '10',
        'quantityUnit': 'BEN C', 'weightUnit': 'TAN',
        'nativeWeight': Decimal('0.000'), 'quantity': Decimal('164'),
    }]
    assert any('1 phiếu hàng rời' in text
               and 'trọng lượng nguồn bằng 0 nhưng số lượng dương' in text
               and 'giữ nguyên 0 tấn' in text for text in item['warnings'])
    assert row == before
    for other_name, other in results.items():
        if other_name != name:
            assert_unobserved(other)


def test_both_zero_remains_empty_and_positive_mass_has_no_warning():
    result, _ = run([bulk_fact(quantity=0), bulk_fact(source_id=2, native_weight=Decimal('17'))])
    item = result['bulkQuayVolumesCB']
    assert item['ready'] and item['excluded_empty_bulk_rows'] == 1
    assert item['rows'][0]['bulkWeight'] == 17
    assert_unobserved(item)


@pytest.mark.parametrize('updates,blocker', [
    ({'native_weight': None}, 'WEIGHT_OR_UNIT_UNAVAILABLE'),
    ({'native_weight': False}, 'WEIGHT_OR_UNIT_UNAVAILABLE'),
    ({'tonne_factor': None}, 'WEIGHT_OR_UNIT_UNAVAILABLE'),
    ({'tonne_factor': Decimal('0')}, 'WEIGHT_OR_UNIT_UNAVAILABLE'),
    ({'tonne_factor': Decimal('-1')}, 'WEIGHT_OR_UNIT_UNAVAILABLE'),
    ({'tonne_factor': Decimal('NaN')}, 'WEIGHT_OR_UNIT_UNAVAILABLE'),
    ({'business_date': date(2026, 8, 31)}, 'SOURCE_DATE_INVALID'),
    ({'business_date': date(2026, 10, 1)}, 'SOURCE_DATE_INVALID'),
    ({'direction_id': None}, 'PHYSICAL_SHIP_OR_DIRECTION_UNAVAILABLE'),
    ({'ship_id': None}, 'PHYSICAL_SHIP_OR_DIRECTION_UNAVAILABLE'),
])
def test_invalid_or_out_of_period_fact_never_creates_a_zero_mass_observation(updates, blocker):
    result, _ = run([bulk_fact(**updates)])
    item = result['bulkQuayVolumesCB']
    assert item['ready'] is False and item['blockers'] == [blocker]
    assert item['rows'] == []
    assert_unobserved(item)


def test_unknown_cargo_type_does_not_create_an_observation_before_validation():
    config = profile()
    config['cargo_type_by_cargo'] = {}
    result, _ = run([bulk_fact()], config)
    item = result['bulkQuayVolumesCB']
    assert item['blockers'] == ['CARGO_TYPE_UNMAPPED'] and item['rows'] == []
    assert_unobserved(item)


@pytest.mark.parametrize('quantity', [None, 0, -1, False, Decimal('NaN'), 'unknown'])
def test_quantity_must_be_explicit_finite_positive_without_new_quantity_policy(quantity):
    result, _ = run([bulk_fact(quantity=quantity)])
    item = result['bulkQuayVolumesCB']
    assert item['ready'] and item['blockers'] == []
    assert_unobserved(item)


@pytest.mark.parametrize('updates', [
    {'production_scope': 'vietsun'},
    {'production_scope': 'unclassified'},
    {'method_id': 777},
    {'physical_voyage': 0},
])
def test_excluded_scope_method_or_nonphysical_fact_does_not_warn(updates):
    config = profile()
    config['production_scope'] = 'nghe_tinh'
    results, _ = run([bulk_fact(**updates)], config)
    for item in results.values():
        assert item['ready'] and item['rows'] == []
        assert_unobserved(item)


def test_container_zero_mass_is_not_a_bulk_observation():
    results, _ = run([fact(native_weight=0, quantity=1)])
    item = results['contQuayVolumesCB']
    assert item['ready'] and item['rows'][0]['containerWeight'] == 0
    for data in results.values():
        assert_unobserved(data)


@pytest.mark.parametrize('count', [50, 53])
def test_observations_count_source_facts_before_aggregation_and_bound_samples(count):
    rows = [bulk_fact(source_id=n) for n in range(1, count + 1)]
    rows.append(bulk_fact(source_id=count + 1, native_weight=Decimal('25.125')))
    result, _ = run(rows)
    item = result['bulkQuayVolumesCB']
    assert item['ready'] and item['blockers'] == [] and item['issue_counts'] == {}
    assert item['source_row_count'] == count + 1
    assert len(item['rows']) == 1 and item['rows'][0]['bulkWeight'] == Decimal('25.125')
    assert item[COUNT] == count and len(item[SAMPLES]) == 50
    assert {sample['sourceId'] for sample in item[SAMPLES]} == {str(n) for n in range(1, 51)}
    assert item[TRUNCATED] is (count > 50)
    assert sum('trọng lượng nguồn bằng 0 nhưng số lượng dương' in text
               for text in item['warnings']) == 1


def test_private_observations_never_change_public_slg_row_or_envelope():
    results, _ = run([bulk_fact()])
    item = results['bulkQuayVolumesCB']
    # Same private-key boundary used by ExportStore.publish, without opening a store.
    row = {key: value for key, value in item['rows'][0].items() if not key.startswith('_')}
    public_row = MODELS['bulkQuayVolumesCB'].model_validate(row).model_dump(mode='json')
    expected_row = {
        'reportDate': '20260921', 'companyId': 'CNT', 'finishDate': '20260916',
        'handlingMethodId': '10', 'shipId': '9', 'shipClassId': '1',
        'cargoTypeId': 'BULK', 'cargoCategoryId': '2', 'bulkOriginId': None,
        'bulkWeight': 0, 'shipAgentId': None,
    }
    assert public_row == expected_row
    internal = {**item, 'data': [public_row], 'code': '1', 'message': 'Lấy dữ liệu thành công'}
    assert public_result(internal) == {
        'data': [expected_row], 'code': '1', 'message': 'Lấy dữ liệu thành công',
    }
    assert item[COUNT] == 1


def test_samples_keep_only_bounded_source_context_and_no_notes():
    result, _ = run([bulk_fact(quantity_unit_code='X' * 10000,
                              weight_unit_code='Y' * 10000,
                              remark='PRIVATE_NOTE_SENTINEL', password='SECRET_SENTINEL')])
    item = result['bulkQuayVolumesCB']
    assert item['ready'] and item[COUNT] == 1
    sample = item[SAMPLES][0]
    assert sample['quantityUnit'] is None and sample['weightUnit'] is None
    assert 'remark' not in sample and 'password' not in sample
    assert 'PRIVATE_NOTE_SENTINEL' not in str(sample) and 'SECRET_SENTINEL' not in str(sample)

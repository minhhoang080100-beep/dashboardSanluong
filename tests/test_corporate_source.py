from copy import deepcopy
from datetime import date
from decimal import Decimal

import pytest

from backend.corporate_api.source import (
    ProductionSource, SourceError, REQUIRED_COLUMNS, SCHEMA_SQL, RESOURCES, _facts_sql, _berths_sql,
)
from backend.corporate_api.errors import CorporateError


def profile():
    return {
        'company_id': 'CNT', 'terminals': ['cua_lo', 'ben_thuy'], 'date_basis': 'shiftDate', 'approved': True,
        'production_scope': 'all_activity', 'report_date': '2026-09-21',
        'quay_method_ids': {'cua_lo': [10], 'ben_thuy': [10]},
        'gate_method_ids': {'cua_lo': [20], 'ben_thuy': [20]},
        'cargo_kind_by_cargo': {'cua_lo': {'1': 'container', '2': 'bulk', '3': 'roro'}},
        'cargo_types': {'BULK': 'Hang ngoai container'},
        'cargo_type_by_cargo': {'cua_lo': {'2': 'BULK'}},
        'container_sizes_by_cargo': {'cua_lo': {'1': {'localSzTp': '40F'}}},
    }


def fact(**updates):
    row = {'source_id': 1, 'business_date': date(2026, 9, 16), 'cargo_id': 1,
           'cargo_name': '40F', 'method_id': 10, 'direction_id': 1,
           'native_weight': Decimal('30.125'), 'quantity': 1, 'customer_id': 7,
           'ship_id': 9, 'physical_voyage': 1, 'quay_eligible': 1,
           'tonne_factor': Decimal('1'), 'production_scope': 'nghe_tinh', 'quantity_unit_code': 'CONT'}
    row.update(updates)
    row.setdefault('voyage_id', row['source_id'])
    return row


def berth_rows_for(rows):
    assignments = {row['voyage_id']: row.get('production_scope', 'unclassified')
                   for row in rows if row.get('voyage_id')}
    return [{'voyage_id': voyage, 'production_scope': scope} for voyage, scope in assignments.items()]


class Query:
    def __init__(self, rows=(), omit=None, berths=None):
        self.rows = list(rows)
        self.omit = omit
        self.berths = berths
        self.calls = []

    def __call__(self, database, sql, params):
        self.calls.append((database, sql, params))
        assert sql.lstrip().startswith(('SELECT', 'WITH'))
        if sql == SCHEMA_SQL:
            return [{'table_name': table, 'column_name': column}
                    for table, columns in REQUIRED_COLUMNS.items() for column in columns
                    if (table, column) != self.omit]
        if 'berth_scope.vesselVoyageId AS voyage_id' in sql:
            return (deepcopy(self.berths) if self.berths is not None else berth_rows_for(self.rows)) if database == 'SmartTOS' else []
        return deepcopy(self.rows) if database == 'SmartTOS' else []


def run(rows, config=None):
    query = Query(rows)
    result = ProductionSource(query).extract(date(2026, 9, 1), date(2026, 9, 30),
                                             profile() if config is None else config)
    return result, query


@pytest.mark.parametrize('bad_rows', [{}, None, '', [None]])
def test_malformed_query_rows_cannot_be_published_as_complete_zero(bad_rows):
    base = Query()
    def query(database, sql, params):
        if sql == SCHEMA_SQL:
            return base(database, sql, params)
        return bad_rows if database == 'SmartTOS' else []
    results = ProductionSource(query).extract(date(2026, 9, 1), date(2026, 9, 30), profile())
    assert all(not item['ready'] and item['coverage'] == [] for item in results.values())
    assert all('SOURCE_DATA' in item['blockers'] for item in results.values())


@pytest.mark.parametrize('metadata', [{}, [{'table_name': 'TallyShift'}],
                                      [{'table_name': 'TallyShift', 'column_name': None}]])
def test_malformed_source_schema_is_a_sanitized_blocker(metadata):
    results = ProductionSource(lambda *args: metadata).extract(
        date(2026, 9, 1), date(2026, 9, 30), profile())
    assert all(not item['ready'] and 'SOURCE_SCHEMA' in item['blockers'] for item in results.values())


def yard_profile():
    p = profile()
    p.update(gate_selection='vessel_type', gate_method_ids={}, production_scope='nghe_tinh')
    return p


def yard_fact(**updates):
    return fact(**{'vessel_type_code': 'Container Yard', 'quantity_unit_code': 'CONT',
                   'physical_voyage': 0, 'quay_eligible': 0,
                   'production_scope': 'unclassified', 'method_id': 999, **updates})


def test_yard_selection_preserves_method_without_using_method_allowlist_or_berth():
    result, _ = run([yard_fact()], yard_profile())
    assert result['contGateVolumesCB']['ready']
    row = result['contGateVolumesCB']['rows'][0]
    assert row['handlingMethodId'] == '999'
    assert row['containerWeight'] == Decimal('30.125')
    assert row['containerTEU'] == 2
    assert result['contQuayVolumesCB']['rows'] == []


def test_hour_rows_never_become_containers_even_with_container_cargo():
    result, _ = run([yard_fact(quantity_unit_code='GIO', quantity=6, native_weight=None)], yard_profile())
    data = result['contGateVolumesCB']
    assert data['ready'] and not data['rows']
    assert data['excluded_time_rows'] == 1


def test_zero_quantity_does_not_turn_unknown_weight_into_zero():
    result, _ = run([yard_fact(quantity=0, native_weight=None)], yard_profile())
    assert 'WEIGHT_OR_UNIT_UNAVAILABLE' in result['contGateVolumesCB']['blockers']


@pytest.mark.parametrize('unit', [None, 'XE', 'TAN'])
def test_container_count_unit_must_be_cont(unit):
    result, _ = run([yard_fact(quantity_unit_code=unit)], yard_profile())
    assert 'CONTAINER_QUANTITY_UNIT_UNCONFIRMED' in result['contGateVolumesCB']['blockers']


def test_warehouse_bulk_includes_any_method_but_excludes_service_cargo():
    p = yard_profile()
    p['cargo_kind_by_cargo']['cua_lo']['4'] = 'exclude'
    result, _ = run([yard_fact(vessel_type_code='Warehouse', cargo_id=2, quantity_unit_code='TAN'),
                     yard_fact(source_id=2, vessel_type_code='Bulk Yard', cargo_id=4)], p)
    assert result['bulkGateVolumesCB']['ready']
    assert len(result['bulkGateVolumesCB']['rows']) == 1


def test_roro_yard_and_physical_ships_do_not_enter_gate_results():
    result, _ = run([yard_fact(vessel_type_code='Ro-Ro Yard', method_id=10, quay_eligible=1),
                     yard_fact(source_id=2, vessel_type_code='Container Ship')], yard_profile())
    assert not result['contGateVolumesCB']['rows']
    assert not result['contQuayVolumesCB']['rows']


def test_yard_is_not_counted_again_as_quay_when_method_is_shared():
    result, _ = run([yard_fact(method_id=10, quay_eligible=1)], yard_profile())
    assert len(result['contGateVolumesCB']['rows']) == 1
    assert not result['contQuayVolumesCB']['rows']


def test_missing_location_type_stays_outside_the_explicit_yard_scope():
    result, _ = run([yard_fact(vessel_type_code=None)], yard_profile())
    data = result['contGateVolumesCB']
    assert data['ready'] and data['rows'] == []
    assert data['excluded_location_rows'] == data['excluded_unknown_location_rows'] == 1
    assert any('không tự gán thành kho/bãi' in warning for warning in data['warnings'])
    assert result['bulkGateVolumesCB']['excluded_location_rows'] == 0


def test_unknown_yard_location_does_not_remove_an_independently_valid_physical_quay_row():
    result, _ = run([fact(vessel_type_code=None)], yard_profile())
    assert result['contQuayVolumesCB']['ready']
    assert result['contQuayVolumesCB']['rows'][0]['containerWeight'] == Decimal('30.125')
    assert result['contGateVolumesCB']['ready'] and not result['contGateVolumesCB']['rows']
    assert result['contGateVolumesCB']['excluded_unknown_location_rows'] == 1


def test_unknown_bulk_location_and_berth_do_not_block_container_results():
    p = yard_profile()
    result, _ = run([fact(), fact(source_id=2, cargo_id=2, vessel_type_code=None,
                                 production_scope='unclassified', native_weight=None)], p)
    assert result['contQuayVolumesCB']['ready'] and len(result['contQuayVolumesCB']['rows']) == 1
    assert result['contGateVolumesCB']['ready']
    assert result['bulkQuayVolumesCB']['ready']
    assert result['bulkQuayVolumesCB']['excluded_unknown_scope_rows'] == 1
    assert result['bulkGateVolumesCB']['excluded_unknown_location_rows'] == 1


def test_all_activity_keeps_valid_physical_rows_regardless_of_initial_berth():
    result, _ = run([fact(), fact(source_id=2, production_scope='vietsun'),
                     fact(source_id=3, production_scope='unclassified')])
    data = result['contQuayVolumesCB']
    assert data['ready'] and data['excluded_scope_rows'] == 0
    assert data['rows'][0]['containerWeight'] == Decimal('90.375')
    assert data['rows'][0]['containerTEU'] == 6


def test_nonphysical_quay_activity_is_excluded_before_measurements_or_cargo_mapping():
    result, _ = run([fact(physical_voyage=0, ship_id=None, native_weight=None),
                     fact(source_id=2, physical_voyage=0, cargo_id=999, native_weight=None)])
    for name in ('contQuayVolumesCB', 'bulkQuayVolumesCB'):
        assert result[name]['ready'] and not result[name]['rows']
    assert result['contQuayVolumesCB']['excluded_nonphysical_rows'] == 2
    assert result['bulkQuayVolumesCB']['excluded_nonphysical_rows'] == 1


@pytest.mark.parametrize('physical', [None, True, False, 2, -1, '0', '1', 1.0])
def test_invalid_physical_flag_still_blocks_only_its_known_cargo_kind(physical):
    result, _ = run([fact(), fact(source_id=2, cargo_id=2, physical_voyage=physical)])
    assert result['contQuayVolumesCB']['ready'] and len(result['contQuayVolumesCB']['rows']) == 1
    assert result['bulkQuayVolumesCB']['blockers'] == ['PHYSICAL_SHIP_OR_DIRECTION_UNAVAILABLE']
    assert result['bulkQuayVolumesCB']['excluded_nonphysical_rows'] == 0


@pytest.mark.parametrize('weight', [None, 0, Decimal('0.000')])
@pytest.mark.parametrize('side', ['quay', 'yard'])
def test_native_cont_explicit_zero_activity_is_excluded_without_inventing_weight(weight, side):
    p = yard_profile() if side == 'yard' else profile()
    p['container_size_source'] = 'native_cargo'
    make_fact = yard_fact if side == 'yard' else fact
    result, _ = run([make_fact(cargo_name='Khác', quantity=Decimal('0'), native_weight=weight)], p)
    data = result['contGateVolumesCB' if side == 'yard' else 'contQuayVolumesCB']
    assert data['ready'] and data['rows'] == [] and data['source_row_count'] == 1
    assert data['excluded_empty_container_rows'] == 1
    assert any('không quy đổi trọng lượng thiếu thành 0' in warning for warning in data['warnings'])


@pytest.mark.parametrize('quantity', [1, 2, None, -1, True, Decimal('NaN')])
def test_native_cont_missing_weight_with_nonzero_or_unknown_count_stays_blocked(quantity):
    p = {**profile(), 'container_size_source': 'native_cargo'}
    result, _ = run([fact(cargo_name='Khác', quantity=quantity, native_weight=None)], p)
    data = result['contQuayVolumesCB']
    assert data['blockers'] == ['WEIGHT_OR_UNIT_UNAVAILABLE']
    assert data['excluded_empty_container_rows'] == 0


@pytest.mark.parametrize('weight', [-1, True, Decimal('NaN'), 'invalid'])
def test_native_cont_zero_count_cannot_hide_invalid_weight(weight):
    p = {**profile(), 'container_size_source': 'native_cargo'}
    result, _ = run([fact(quantity=0, native_weight=weight)], p)
    data = result['contQuayVolumesCB']
    assert data['blockers'] == ['WEIGHT_OR_UNIT_UNAVAILABLE']
    assert data['excluded_empty_container_rows'] == 0


def test_native_cont_zero_count_with_positive_weight_retains_weight_and_size_validation():
    p = {**profile(), 'container_size_source': 'native_cargo'}
    result, _ = run([fact(quantity=0, native_weight=Decimal('3.5'))], p)
    data = result['contQuayVolumesCB']
    assert data['ready'] and data['rows'][0]['containerWeight'] == Decimal('3.5')
    assert data['rows'][0]['containerTEU'] == 0 and data['excluded_empty_container_rows'] == 0
    result, _ = run([fact(cargo_name='Khác', quantity=0, native_weight=Decimal('3.5'))], p)
    assert result['contQuayVolumesCB']['blockers'] == ['CONTAINER_SIZE_UNMAPPED']


@pytest.mark.parametrize('updates,resource,blocker', [
    ({'quantity_unit_code': 'TAN'}, 'contQuayVolumesCB', 'CONTAINER_QUANTITY_UNIT_UNCONFIRMED'),
    ({'quantity_unit_code': None}, 'contQuayVolumesCB', 'CONTAINER_QUANTITY_UNIT_UNCONFIRMED'),
    ({'cargo_id': 2}, 'bulkQuayVolumesCB', 'WEIGHT_OR_UNIT_UNAVAILABLE'),
])
def test_zero_activity_rule_is_not_extended_to_non_cont_units_or_bulk(updates, resource, blocker):
    p = {**profile(), 'container_size_source': 'native_cargo'}
    result, _ = run([fact(quantity=0, native_weight=None, **updates)], p)
    assert result[resource]['blockers'] == [blocker]
    assert result[resource]['excluded_empty_container_rows'] == 0


@pytest.mark.parametrize('unit', [None, '', 'TAN', 'XE', 'CONT'])
def test_native_container_explicit_zero_count_and_weight_needs_no_quantity_unit(unit):
    p = {**yard_profile(), 'container_size_source': 'native_cargo'}
    result, _ = run([yard_fact(cargo_name='Container', quantity=0,
                              native_weight=Decimal('0.00'), quantity_unit_code=unit)], p)
    data = result['contGateVolumesCB']
    assert data['ready'] and data['rows'] == []
    assert data['excluded_empty_container_rows'] == 1


@pytest.mark.parametrize('unit', [None, '', 'TAN', 'XE'])
def test_zero_native_container_count_without_actual_zero_weight_cannot_hide_missing_unit(unit):
    p = {**yard_profile(), 'container_size_source': 'native_cargo'}
    for weight in (None, Decimal('1.25'), Decimal('NaN'), -1):
        result, _ = run([yard_fact(quantity=0, native_weight=weight, quantity_unit_code=unit)], p)
        data = result['contGateVolumesCB']
        assert data['blockers'] == ['CONTAINER_QUANTITY_UNIT_UNCONFIRMED']
        assert data['excluded_empty_container_rows'] == 0


@pytest.mark.parametrize('updates,blocker', [
    ({'method_id': None}, 'METHOD_ID_UNAVAILABLE'),
    ({'business_date': None}, 'SOURCE_DATE_INVALID'),
    ({'business_date': date(2026, 10, 1)}, 'SOURCE_DATE_INVALID'),
])
def test_zero_native_container_activity_still_validates_method_and_business_date(updates, blocker):
    p = {**yard_profile(), 'container_size_source': 'native_cargo'}
    result, _ = run([yard_fact(quantity=0, native_weight=0, quantity_unit_code=None, **updates)], p)
    data = result['contGateVolumesCB']
    assert data['blockers'] == [blocker] and data['excluded_empty_container_rows'] == 0


@pytest.mark.parametrize('updates', [{'ship_id': None}, {'direction_id': None}, {'physical_voyage': None}])
def test_zero_native_container_activity_still_validates_physical_ship_data(updates):
    p = {**profile(), 'container_size_source': 'native_cargo'}
    result, _ = run([fact(quantity=0, native_weight=0, quantity_unit_code=None, **updates)], p)
    data = result['contQuayVolumesCB']
    assert data['blockers'] == ['PHYSICAL_SHIP_OR_DIRECTION_UNAVAILABLE']
    assert data['excluded_empty_container_rows'] == 0


def test_zero_activity_does_not_hide_invalid_source_id_or_unclassified_cargo():
    p = {**yard_profile(), 'container_size_source': 'native_cargo'}
    with pytest.raises(SourceError) as caught:
        run([yard_fact(source_id=None, quantity=0, native_weight=0, quantity_unit_code=None)], p)
    assert caught.value.code == 'SOURCE_DUPLICATE'
    result, _ = run([yard_fact(cargo_id=999, quantity=0, native_weight=0, quantity_unit_code=None)], p)
    for name in ('contGateVolumesCB', 'bulkGateVolumesCB'):
        assert result[name]['blockers'] == ['CARGO_KIND_UNMAPPED']
        assert result[name]['excluded_empty_container_rows'] == 0


def test_native_cargo_group_reference_is_used_in_production_instead_of_manual_map():
    p = yard_profile()
    p.update(cargo_catalog_source='native_groups', cargo_types={'55': 'Native group'})
    result, _ = run([yard_fact(cargo_id=2, cargo_group_id=55, quantity_unit_code='TAN')], p)
    row = result['bulkGateVolumesCB']['rows'][0]
    assert row['cargoTypeId'] == '55' and row['cargoCategoryId'] == '2'
    assert result['bulkGateVolumesCB']['ready']


def test_native_cargo_group_does_not_require_a_second_static_type_allowlist():
    p = yard_profile()
    p.update(cargo_catalog_source='native_groups', cargo_types={})
    result, _ = run([yard_fact(cargo_id=2, cargo_group_id=55, quantity_unit_code='TAN')], p)
    data = result['bulkGateVolumesCB']
    assert data['ready'] and data['rows'][0]['cargoTypeId'] == '55'
    # Native catalog membership remains a downstream FK requirement.
    assert data['rows'][0]['cargoCategoryId'] == '2'


@pytest.mark.parametrize('group', [None, 0, -1, True, 'not-an-id'])
def test_native_group_mode_still_requires_a_valid_native_group_id(group):
    p = yard_profile()
    p.update(cargo_catalog_source='native_groups', cargo_types={})
    result, _ = run([yard_fact(cargo_id=2, cargo_group_id=group, quantity_unit_code='TAN')], p)
    assert result['bulkGateVolumesCB']['blockers'] == ['CARGO_TYPE_UNMAPPED']


def test_configured_cargo_type_still_requires_configured_type_membership():
    p = yard_profile()
    p['cargo_types'] = {}
    result, _ = run([yard_fact(cargo_id=2, cargo_group_id=55, quantity_unit_code='TAN')], p)
    assert result['bulkGateVolumesCB']['blockers'] == ['CARGO_TYPE_UNMAPPED']


def test_group_classification_is_explicit_and_cargo_override_takes_priority():
    p = yard_profile()
    p.update(cargo_catalog_source='native_groups', cargo_types={},
             cargo_kind_by_group={'cua_lo': {'55': 'bulk'}})
    p['cargo_kind_by_cargo']['cua_lo']['4'] = 'exclude'
    rows = [yard_fact(cargo_id=999, cargo_group_id=55, quantity_unit_code='TAN'),
            yard_fact(source_id=2, cargo_id=4, cargo_group_id=55, quantity_unit_code='TAN')]
    result, _ = run(rows, p)
    data = result['bulkGateVolumesCB']
    assert data['ready'] and len(data['rows']) == 1
    assert data['rows'][0]['cargoCategoryId'] == '999'
    assert data['rows'][0]['cargoTypeId'] == '55'


@pytest.mark.parametrize('kind', ['roro', 'exclude'])
def test_group_exclusions_do_not_become_bulk(kind):
    p = yard_profile()
    p['cargo_kind_by_group'] = {'cua_lo': {'55': kind}}
    result, _ = run([yard_fact(cargo_id=999, cargo_group_id=55, quantity_unit_code='TAN')], p)
    assert result['bulkGateVolumesCB']['ready'] and not result['bulkGateVolumesCB']['rows']
    assert result['contGateVolumesCB']['ready'] and not result['contGateVolumesCB']['rows']
    assert result['bulkGateVolumesCB']['excluded_roro_rows'] == int(kind == 'roro')


def test_unmapped_group_does_not_make_unknown_cargo_bulk():
    p = yard_profile()
    p['cargo_kind_by_group'] = {'cua_lo': {'55': 'bulk'}}
    result, _ = run([yard_fact(cargo_id=999, cargo_group_id=56)], p)
    assert result['bulkGateVolumesCB']['blockers'] == ['CARGO_KIND_UNMAPPED']
    assert result['contGateVolumesCB']['blockers'] == ['CARGO_KIND_UNMAPPED']


@pytest.mark.parametrize('key,mapping', [
    ('cargo_kind_by_group', {'cua_lo': {'55': 'anything'}}),
    ('cargo_kind_by_group', {'cua_lo': {'55': True}}),
    ('cargo_kind_by_group', {'cua_lo': {'55': []}}),
    ('cargo_kind_by_group', {'cua_lo': {'0': 'bulk'}}),
    ('cargo_kind_by_group', {'unknown-terminal': {'55': 'bulk'}}),
    ('cargo_kind_by_group', {'cua_lo': []}),
    ('cargo_kind_by_group', []),
    ('cargo_kind_by_cargo', {'cua_lo': {'1': 'anything'}}),
])
def test_invalid_classification_profile_is_rejected_before_reading_source(key, mapping):
    p = profile()
    p[key] = mapping
    query = Query()
    with pytest.raises(SourceError) as caught:
        ProductionSource(query).extract(date(2026, 9, 1), date(2026, 9, 30), p)
    assert caught.value.code == 'SOURCE_PROFILE' and not query.calls


def test_aggregation_retains_all_source_terminals_without_changing_public_dimensions():
    p = profile()
    p['cargo_kind_by_cargo']['ben_thuy'] = dict(p['cargo_kind_by_cargo']['cua_lo'])
    p['container_sizes_by_cargo']['ben_thuy'] = deepcopy(p['container_sizes_by_cargo']['cua_lo'])
    fixture = Query()
    def query(database, sql, params):
        if sql == SCHEMA_SQL:
            return fixture(database, sql, params)
        if 'berth_scope.vesselVoyageId AS voyage_id' in sql:
            return berth_rows_for([fact(), fact(source_id=2)])
        return [fact(), fact(source_id=2)]
    data = ProductionSource(query).extract(date(2026, 9, 1), date(2026, 9, 30), p)['contQuayVolumesCB']
    assert data['ready'] and len(data['rows']) == 1
    row, = data['rows']
    assert row['_sourceTerminals'] == ['ben_thuy', 'cua_lo']
    assert row['containerWeight'] == Decimal('120.500') and row['containerTEU'] == 8


def test_dimensionful_aggregation_uses_actual_weight_and_physical_ship_id():
    result, query = run([fact(), fact(source_id=2, native_weight=Decimal('4.5'), quantity=2),
                         fact(source_id=3, direction_id=2)])
    data = result['contQuayVolumesCB']
    assert data['ready'] and data['coverage'] == [['20260901', '20260930']]
    assert len(data['rows']) == 2
    assert data['rows'][0]['containerWeight'] == Decimal('34.625')
    assert data['rows'][0]['containerTEU'] == 6
    assert data['rows'][0]['shipId'] == '9'
    assert data['rows'][0]['containerSizeId'] == '1'
    assert data['rows'][0]['originId'] is None
    assert data['rows'][0]['shipOperatorId'] is None
    assert query.calls[-1][2] == (date(2026, 9, 1), date(2026, 10, 1))


def test_gate_is_explicit_and_does_not_include_other_non_quay_jobs():
    result, _ = run([fact(method_id=20, quay_eligible=0),
                     fact(source_id=2, method_id=999, quay_eligible=0)])
    assert result['contGateVolumesCB']['ready']
    assert len(result['contGateVolumesCB']['rows']) == 1
    assert result['contGateVolumesCB']['rows'][0]['containerTEU'] == 2
    assert result['contGateVolumesCB']['unselected_source_row_count'] == 1
    assert result['contQuayVolumesCB']['rows'] == []


def test_bulk_keeps_customer_fk_and_roro_is_explicitly_excluded():
    result, _ = run([fact(cargo_id=2, method_id=20, quay_eligible=0),
                     fact(source_id=2, cargo_id=3, method_id=20, quay_eligible=0)])
    data = result['bulkGateVolumesCB']
    assert data['ready'] and len(data['rows']) == 1
    assert data['rows'][0]['customerCode'] == '7'
    assert data['rows'][0]['cargoCategoryId'] == '2'
    assert data['excluded_roro_rows'] == 1


@pytest.mark.parametrize('update,issue', [
    ({'native_weight': None}, 'WEIGHT_OR_UNIT_UNAVAILABLE'),
    ({'native_weight': Decimal('NaN')}, 'WEIGHT_OR_UNIT_UNAVAILABLE'),
    ({'tonne_factor': None}, 'WEIGHT_OR_UNIT_UNAVAILABLE'),
    ({'quantity': None}, 'CONTAINER_QUANTITY_UNAVAILABLE'),
    ({'quantity': Decimal('1.5')}, 'CONTAINER_QUANTITY_UNAVAILABLE'),
    ({'ship_id': None}, 'PHYSICAL_SHIP_OR_DIRECTION_UNAVAILABLE'),
])
def test_missing_measurements_do_not_turn_into_zero_or_complete_coverage(update, issue):
    result, _ = run([fact(**update)])
    data = result['contQuayVolumesCB']
    assert not data['ready'] and issue in data['blockers']
    assert data['coverage'] == [] and data['rows'] == []


def test_unknown_cargo_is_not_silently_classified_as_bulk():
    result, _ = run([fact(cargo_id=999)])
    assert 'CARGO_KIND_UNMAPPED' in result['bulkQuayVolumesCB']['blockers']
    assert 'CARGO_KIND_UNMAPPED' in result['contQuayVolumesCB']['blockers']


def test_no_approved_method_mapping_blocks_empty_dataset_as_well():
    p = profile()
    del p['gate_method_ids']
    result, _ = run([], p)
    assert result['contGateVolumesCB']['blockers'] == ['GATE_METHODS_UNCONFIRMED']
    assert result['bulkGateVolumesCB']['coverage'] == []


def test_unconfirmed_date_and_scope_can_be_previewed_but_not_published():
    p = profile()
    del p['date_basis']
    del p['production_scope']
    result, _ = run([fact()], p)
    data = result['contQuayVolumesCB']
    assert len(data['rows']) == 1 and not data['ready']
    assert set(data['blockers']) == {'DATE_BASIS_UNCONFIRMED', 'PRODUCTION_SCOPE_UNCONFIRMED'}


def test_scope_filter_does_not_reassign_cau_5_or_missing_berth():
    p = profile()
    p['production_scope'] = 'nghe_tinh'
    result, _ = run([fact(), fact(source_id=2, production_scope='vietsun'),
                     fact(source_id=3, production_scope='unclassified')], p)
    data = result['contQuayVolumesCB']
    assert len(data['rows']) == 1
    assert data['rows'][0]['containerWeight'] == Decimal('30.125')
    assert data['ready'] and data['blockers'] == []
    assert data['excluded_scope_rows'] == 2 and data['excluded_unknown_scope_rows'] == 1
    assert any('không gán lại' in warning for warning in data['warnings'])
    assert result['bulkQuayVolumesCB']['excluded_scope_rows'] == 0


def test_metadata_is_verified_before_query_and_failure_does_not_guess_a_column():
    query = Query([fact()], omit=('TallyShift', 'shiftDate'))
    result = ProductionSource(query).extract(date(2026, 9, 1), date(2026, 9, 30), profile())
    assert result['contQuayVolumesCB']['blockers'] == ['SOURCE_SCHEMA'] and len(query.calls) == 2
    assert 'TallyShift.shiftDate' in result['contQuayVolumesCB']['source_errors'][0]['message']


def test_one_to_many_join_or_source_id_collision_is_rejected():
    with pytest.raises(SourceError) as caught:
        run([fact(), fact()])
    assert caught.value.code == 'SOURCE_DUPLICATE'


def test_quay_guard_reuses_statistics_group_and_never_adds_hatch_jobs():
    sql = _facts_sql('cua_lo')
    assert 'SANLUONG-QUACANG' in sql
    assert 't.cargoDirectId IN (1, 2)' in sql
    result, _ = run([fact(quay_eligible=0)])
    assert result['contQuayVolumesCB']['rows'] == []


def test_source_statistics_selects_quay_from_native_scope_without_static_method_list():
    p = yard_profile()
    p['quay_selection'] = 'source_statistics'
    p.pop('quay_method_ids')
    result, _ = run([fact(method_id=123, vessel_type_code='Container Ship')], p)
    data = result['contQuayVolumesCB']
    assert data['ready'] and data['rows'][0]['handlingMethodId'] == '123'
    assert data['rows'][0]['containerWeight'] == Decimal('30.125')


def test_source_statistics_ignores_stale_method_list_but_keeps_throughput_and_cau_5_rules():
    p = yard_profile()
    p['quay_selection'] = 'source_statistics'
    rows = [fact(method_id=123, vessel_type_code='Container Ship'),
            fact(source_id=2, method_id=123, quay_eligible=0, vessel_type_code='Container Ship'),
            fact(source_id=3, method_id=123, production_scope='vietsun', vessel_type_code='Container Ship')]
    result, _ = run(rows, p)
    data = result['contQuayVolumesCB']
    assert data['ready'] and len(data['rows']) == 1
    assert data['rows'][0]['containerWeight'] == Decimal('30.125')


def test_legacy_quay_profile_still_requires_method_list_even_for_empty_period():
    p = yard_profile()
    p.pop('quay_method_ids')
    result, _ = run([], p)
    assert result['contQuayVolumesCB']['blockers'] == ['QUAY_METHODS_UNCONFIRMED']
    assert result['bulkQuayVolumesCB']['blockers'] == ['QUAY_METHODS_UNCONFIRMED']


def test_source_statistics_with_yard_gate_does_not_count_one_row_in_both_resources():
    p = yard_profile()
    p['quay_selection'] = 'source_statistics'
    p.pop('quay_method_ids')
    result, _ = run([yard_fact(method_id=123, quay_eligible=1)], p)
    assert result['contGateVolumesCB']['ready'] and len(result['contGateVolumesCB']['rows']) == 1
    assert result['contQuayVolumesCB']['ready'] and not result['contQuayVolumesCB']['rows']


def test_source_statistics_rejects_gate_method_that_overlaps_native_quay_scope():
    p = profile()
    p['quay_selection'] = 'source_statistics'
    with pytest.raises(SourceError) as caught:
        run([fact(method_id=20, quay_eligible=1)], p)
    assert caught.value.code == 'SOURCE_PROFILE'


@pytest.mark.parametrize('mode', [None, 'unknown', [], True])
def test_invalid_quay_selection_is_rejected(mode):
    p = profile()
    p['quay_selection'] = mode
    with pytest.raises(SourceError) as caught:
        run([], p)
    assert caught.value.code == 'SOURCE_PROFILE'


def test_window_and_profile_reject_unbounded_or_ambiguous_requests():
    with pytest.raises(SourceError) as caught:
        ProductionSource(Query()).extract(date(2026, 1, 1), date(2026, 12, 31), profile())
    assert caught.value.code == 'SOURCE_RANGE'
    p = profile()
    p['gate_method_ids']['cua_lo'] = [10]
    with pytest.raises(SourceError) as caught:
        run([], p)
    assert caught.value.code == 'SOURCE_PROFILE'


def test_unapproved_profile_can_produce_review_rows_but_never_ready():
    p = profile()
    p['approved'] = False
    result, _ = run([fact()], p)
    assert len(result['contQuayVolumesCB']['rows']) == 1
    assert result['contQuayVolumesCB']['blockers'] == ['SOURCE_PROFILE_UNAPPROVED']
    assert result['contQuayVolumesCB']['coverage'] == []


def test_source_failure_is_reviewable_and_redacts_driver_details():
    def failure(*args):
        raise RuntimeError('UID=private;PWD=do-not-expose')
    result = ProductionSource(failure).extract(date(2026, 9, 1), date(2026, 9, 30), profile())
    for data in result.values():
        assert not data['ready'] and data['coverage'] == []
        assert data['blockers'] == ['SOURCE_UNAVAILABLE']
    assert 'private' not in repr(result) and 'do-not-expose' not in repr(result)


def test_empty_method_allowlist_is_unconfirmed_not_complete_zero():
    p = profile()
    p['gate_method_ids']['ben_thuy'] = []
    result, _ = run([], p)
    assert not result['contGateVolumesCB']['ready']
    assert result['contGateVolumesCB']['blockers'] == ['GATE_METHODS_UNCONFIRMED']


def test_single_terminal_preview_cannot_claim_complete_company_coverage():
    p = profile()
    p['terminals'] = ['cua_lo']
    result, _ = run([fact()], p)
    assert len(result['contQuayVolumesCB']['rows']) == 1
    assert not result['contQuayVolumesCB']['ready']
    assert result['contQuayVolumesCB']['blockers'] == ['SOURCE_TERMINALS_INCOMPLETE']
    assert result['contQuayVolumesCB']['coverage'] == []


@pytest.mark.parametrize('resources', [[], ['unknown'], ['bulkGateVolumesCB', 'bulkGateVolumesCB'],
                                        'bulkGateVolumesCB', [None], [True]])
def test_invalid_resource_selection_fails_before_source_reads(resources):
    source = Query()
    with pytest.raises(SourceError) as caught:
        ProductionSource(source).extract(date(2026, 9, 1), date(2026, 9, 30), profile(), resources=resources)
    assert caught.value.code == 'SOURCE_RESOURCE' and source.calls == []


def test_bulk_only_extraction_skips_native_size_catalogs_and_unselected_container_work(monkeypatch):
    def forbidden_reader(*args, **kwargs):
        pytest.fail('Bulk-only extraction must not read container size metadata or rows')
    monkeypatch.setattr('backend.corporate_api.source._Reader', forbidden_reader)
    p = profile()
    p['container_size_source'] = 'native_domestic'
    source = Query([fact(cargo_id=2), fact(source_id=2, native_weight=None)])
    result = ProductionSource(source).extract(date(2026, 9, 1), date(2026, 9, 30), p,
                                             resources=['bulkQuayVolumesCB'])
    assert list(result) == ['bulkQuayVolumesCB']
    assert result['bulkQuayVolumesCB']['ready']
    assert result['bulkQuayVolumesCB']['rows'][0]['bulkWeight'] == Decimal('30.125')
    assert [database for database, sql, _ in source.calls if sql == SCHEMA_SQL] == [
        'SmartTOS', 'SmartTOS_BenThuy']


def test_selected_production_still_blocks_when_unknown_cargo_may_belong_to_it():
    result = ProductionSource(Query([fact(cargo_id=999)])).extract(
        date(2026, 9, 1), date(2026, 9, 30), profile(), resources=['bulkQuayVolumesCB'])
    assert result['bulkQuayVolumesCB']['blockers'] == ['CARGO_KIND_UNMAPPED']


def test_selected_container_reads_only_its_size_table_metadata():
    from test_corporate_native_sizes import native_source, TABLE
    catalogs = native_source()
    facts = Query([fact()])
    def query(database, sql, params):
        if sql == SCHEMA_SQL or 't.tallyShiftId AS source_id' in sql or 'berth_scope.vesselVoyageId AS voyage_id' in sql:
            return facts(database, sql, params)
        return catalogs(database, sql, params)
    p = {**profile(), 'container_size_source': 'native_domestic',
         'container_size_ids_by_cargo': {'cua_lo': {'1': 617}}}
    result = ProductionSource(query).extract(date(2026, 9, 1), date(2026, 9, 30), p,
                                            resources=['contQuayVolumesCB'])
    assert list(result) == ['contQuayVolumesCB'] and result['contQuayVolumesCB']['ready']
    metadata = [params for _, sql, params in catalogs.calls if 'INFORMATION_SCHEMA' in sql]
    assert metadata == [(TABLE,), (TABLE,)]


@pytest.mark.parametrize('code', ['SOURCE_TIMEOUT', 'SOURCE_UNAVAILABLE', 'SOURCE_SCHEMA', 'SOURCE_ROW_LIMIT'])
def test_selected_source_preserves_allowlisted_typed_errors_without_private_messages(code):
    base = Query([fact(cargo_id=2)])
    def query(database, sql, params):
        if database == 'SmartTOS_BenThuy':
            raise CorporateError(503, code, 'Password=do-not-expose;Server=private-host')
        return base(database, sql, params)
    result = ProductionSource(query).extract(date(2026, 9, 1), date(2026, 9, 30), profile(),
                                            resources=['bulkQuayVolumesCB'])
    data = result['bulkQuayVolumesCB']
    assert not data['ready'] and data['coverage'] == [] and len(data['rows']) == 1
    assert data['blockers'] == [code] and data['source_errors'][0]['terminal'] == 'ben_thuy'
    assert 'Password' not in str(result) and 'private-host' not in str(result)


def test_unknown_typed_error_code_is_not_echoed_or_treated_as_a_known_source_failure():
    def query(*args):
        raise CorporateError(503, 'private-arbitrary-code', 'private-message')
    result = ProductionSource(query).extract(date(2026, 9, 1), date(2026, 9, 30), profile(),
                                            resources=['bulkQuayVolumesCB'])
    assert result['bulkQuayVolumesCB']['blockers'] == ['SOURCE_UNAVAILABLE']
    assert 'private' not in str(result)


def test_default_extraction_remains_all_four_production_resources():
    result, _ = run([])
    assert tuple(result) == RESOURCES


def test_manage_extract_passes_only_selected_production_resources(monkeypatch):
    from backend.corporate_api.manage_exports import extract
    calls = []
    def production(self, start, end, config, *, resources=None):
        calls.append(resources)
        return {resource: {'rows': [], 'ready': True, 'blockers': []} for resource in resources}
    monkeypatch.setattr(ProductionSource, 'extract', production)
    result = extract(profile(), date(2026, 9, 1), date(2026, 9, 30), ['bulkGateVolumesCB'], Query())
    assert calls == [['bulkGateVolumesCB']] and list(result['datasets']) == ['bulkGateVolumesCB']


@pytest.mark.parametrize('berths', [
    [{'voyage_id': 1, 'production_scope': 'nghe_tinh'}, {'voyage_id': 1, 'production_scope': 'nghe_tinh'}],
    [{'voyage_id': 999, 'production_scope': 'nghe_tinh'}],
    [{'voyage_id': None, 'production_scope': 'nghe_tinh'}],
    [{'voyage_id': 1, 'production_scope': 'unknown'}],
    [{'voyage_id': 1, 'production_scope': []}],
    [{'voyage_id': 1}],
    [None], {},
])
def test_invalid_duplicated_or_unrequested_berth_assignment_blocks_complete_result(berths):
    source = Query([fact()], berths=berths)
    result = ProductionSource(source).extract(date(2026, 9, 1), date(2026, 9, 30), profile(),
                                             resources=['contQuayVolumesCB'])
    assert not result['contQuayVolumesCB']['ready']
    assert result['contQuayVolumesCB']['blockers'] == ['SOURCE_DATA']


def test_berth_lookup_failure_cannot_be_reported_as_empty_or_nghe_tinh():
    base = Query([fact()])
    def query(database, sql, params):
        if 'berth_scope.vesselVoyageId AS voyage_id' in sql:
            raise CorporateError(503, 'SOURCE_TIMEOUT', 'private-driver-message')
        return base(database,sql,params)
    result = ProductionSource(query).extract(date(2026, 9, 1), date(2026, 9, 30), profile(),
                                            resources=['contQuayVolumesCB'])
    data = result['contQuayVolumesCB']
    assert not data['ready'] and data['coverage'] == [] and data['rows'] == []
    assert data['blockers'] == ['SOURCE_TIMEOUT']
    assert 'private-driver-message' not in str(result)


def test_missing_assignment_is_excluded_from_scoped_quay_without_reassignment():
    p = profile()
    p['production_scope'] = 'nghe_tinh'
    source = Query([fact()], berths=[])
    result = ProductionSource(source).extract(date(2026, 9, 1), date(2026, 9, 30), p,
                                             resources=['contQuayVolumesCB'])
    data = result['contQuayVolumesCB']
    assert data['ready'] and data['rows'] == []
    assert data['excluded_scope_rows'] == data['excluded_unknown_scope_rows'] == 1


def test_empty_facts_skip_unneeded_berth_lookup():
    source = Query([])
    assert ProductionSource(source)._read_terminal('cua_lo',date(2026,9,1),date(2026,9,30)) == []
    assert len(source.calls) == 2


@pytest.mark.parametrize('voyage', [None, 0])
def test_explicit_missing_voyage_stays_unclassified_without_berth_query(voyage):
    source = Query([fact(voyage_id=voyage)])
    rows = ProductionSource(source)._read_terminal('cua_lo',date(2026,9,1),date(2026,9,30))
    assert rows[0]['production_scope'] == 'unclassified' and len(source.calls) == 2


def test_berth_lookup_has_an_independent_row_limit(monkeypatch):
    monkeypatch.setattr('backend.corporate_api.source.MAX_BERTH_ROWS', 1)
    source = Query([fact(),fact(source_id=2)])
    with pytest.raises(SourceError) as caught:
        ProductionSource(source)._read_terminal('cua_lo',date(2026,9,1),date(2026,9,30))
    assert caught.value.code == 'SOURCE_ROW_LIMIT'


@pytest.mark.parametrize('name,teu', [('20F',1),('20E',1),('20R',1),('40F',2),('40E',2),
                                     ('40R',2),('45F',2),('45E',2)])
def test_native_cargo_sizes_keep_native_id_and_actual_weight_without_size_catalog_read(monkeypatch, name, teu):
    def forbidden_reader(*args, **kwargs):
        pytest.fail('Native cargo mode must not read domestic size metadata')
    monkeypatch.setattr('backend.corporate_api.source._Reader', forbidden_reader)
    p = {**profile(), 'container_size_source': 'native_cargo', 'container_sizes_by_cargo': {}}
    result = ProductionSource(Query([fact(cargo_name=name)])).extract(
        date(2026, 9, 1), date(2026, 9, 30), p, resources=['contQuayVolumesCB'])
    data = result['contQuayVolumesCB']
    assert data['ready'] and data['rows'][0]['containerSizeId'] == '1'
    assert data['rows'][0]['containerWeight'] == Decimal('30.125')
    assert data['rows'][0]['containerTEU'] == teu


@pytest.mark.parametrize('name', ['Container','Khác','GP','40f',' 40F',None])
def test_native_cargo_size_does_not_guess_unknown_or_noncanonical_container_codes(name):
    p = {**profile(), 'container_size_source': 'native_cargo'}
    result = ProductionSource(Query([fact(cargo_name=name)])).extract(
        date(2026, 9, 1), date(2026, 9, 30), p, resources=['contQuayVolumesCB'])
    assert result['contQuayVolumesCB']['blockers'] == ['CONTAINER_SIZE_UNMAPPED']


class DescribeQuery(Query):
    def __init__(self, rows=()):
        super().__init__(rows)
        self.descriptions = []
        self.schema = {table: [{'column_name': column} for column in sorted(columns)]
                       for table, columns in REQUIRED_COLUMNS.items()}

    def describe_table(self, database, table):
        self.descriptions.append((database, table))
        return deepcopy(self.schema[table])

    def __call__(self, database, sql, params):
        assert sql != SCHEMA_SQL, 'Runtime capability must avoid broad INFORMATION_SCHEMA queries'
        return super().__call__(database,sql,params)


def test_fast_metadata_capability_reads_known_tables_without_information_schema():
    source = DescribeQuery([fact(cargo_id=2)])
    result = ProductionSource(source).extract(date(2026,9,1),date(2026,9,30),profile(),
                                             resources=['bulkQuayVolumesCB'])
    assert result['bulkQuayVolumesCB']['ready']
    assert source.descriptions == [(database,table) for database in ('SmartTOS','SmartTOS_BenThuy')
                                   for table in REQUIRED_COLUMNS]
    assert all(sql != SCHEMA_SQL for _,sql,_ in source.calls)
    assert len(source.calls) == 3  # Cửa Lò facts + berths; Bến Thủy empty facts


def test_fast_metadata_missing_required_column_blocks_before_fact_reads():
    source = DescribeQuery([fact()])
    source.schema['TallyShift'] = [row for row in source.schema['TallyShift']
                                  if row['column_name'] != 'shiftDate']
    result = ProductionSource(source).extract(date(2026,9,1),date(2026,9,30),profile(),
                                             resources=['contQuayVolumesCB'])
    assert result['contQuayVolumesCB']['blockers'] == ['SOURCE_SCHEMA']
    assert 'TallyShift.shiftDate' in result['contQuayVolumesCB']['source_errors'][0]['message']
    assert source.calls == []


@pytest.mark.parametrize('description', [None, {}, [None], [{'column_name': None}],
                                       [{'column_name': ''}], [{'data_type': 'int'}],
                                       [{'column_name': 'shiftDate'}, {'column_name': 'shiftDate'}]])
def test_fast_metadata_invalid_descriptor_is_not_treated_as_verified_schema(description):
    source = DescribeQuery([fact()])
    source.schema['TallyShift'] = description
    result = ProductionSource(source).extract(date(2026,9,1),date(2026,9,30),profile(),
                                             resources=['contQuayVolumesCB'])
    assert result['contQuayVolumesCB']['blockers'] == ['SOURCE_SCHEMA']
    assert source.calls == []


@pytest.mark.parametrize('typed', [True,False])
def test_fast_metadata_failure_is_preserved_safely_without_facts_or_partial_coverage(typed):
    source = DescribeQuery([fact()])
    def failed_description(database,table):
        if typed:
            raise CorporateError(503,'SOURCE_TIMEOUT','Password=hidden;Server=private')
        raise RuntimeError('Password=hidden;Server=private')
    source.describe_table = failed_description
    result = ProductionSource(source).extract(date(2026,9,1),date(2026,9,30),profile(),
                                             resources=['contQuayVolumesCB'])
    data = result['contQuayVolumesCB']
    assert data['blockers'] == ['SOURCE_TIMEOUT' if typed else 'SOURCE_UNAVAILABLE']
    assert data['coverage'] == [] and not data['rows'] and source.calls == []
    assert 'Password' not in str(result) and 'private' not in str(result)


def test_non_callable_metadata_attribute_uses_compatible_information_schema_fallback():
    source = Query([])
    source.describe_table = None
    result = ProductionSource(source).extract(date(2026,9,1),date(2026,9,30),profile(),
                                             resources=['bulkQuayVolumesCB'])
    assert result['bulkQuayVolumesCB']['ready']
    assert sum(sql == SCHEMA_SQL for _,sql,_ in source.calls) == 2

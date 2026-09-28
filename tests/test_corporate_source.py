from copy import deepcopy
from datetime import date
from decimal import Decimal

import pytest

from backend.corporate_api.source import (
    ProductionSource, SourceError, REQUIRED_COLUMNS, SCHEMA_SQL, _facts_sql,
)


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
    return row


class Query:
    def __init__(self, rows=(), omit=None):
        self.rows = list(rows)
        self.omit = omit
        self.calls = []

    def __call__(self, database, sql, params):
        self.calls.append((database, sql, params))
        assert sql.lstrip().startswith('SELECT')
        if sql == SCHEMA_SQL:
            return [{'table_name': table, 'column_name': column}
                    for table, columns in REQUIRED_COLUMNS.items() for column in columns
                    if (table, column) != self.omit]
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


def test_missing_location_type_blocks_gate_completeness():
    result, _ = run([yard_fact(vessel_type_code=None)], yard_profile())
    assert 'VESSEL_TYPE_UNAVAILABLE' in result['contGateVolumesCB']['blockers']


def test_native_cargo_group_reference_is_used_in_production_instead_of_manual_map():
    p = yard_profile()
    p.update(cargo_catalog_source='native_groups', cargo_types={'55': 'Native group'})
    result, _ = run([yard_fact(cargo_id=2, cargo_group_id=55, quantity_unit_code='TAN')], p)
    row = result['bulkGateVolumesCB']['rows'][0]
    assert row['cargoTypeId'] == '55' and row['cargoCategoryId'] == '2'
    assert result['bulkGateVolumesCB']['ready']


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
    assert data['blockers'] == ['SOURCE_SCOPE_UNKNOWN']


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

"""Source-backed volume-to-mass conversion is narrow, opt-in and fail-closed.

All database responses are local fixtures. No test opens SmartTOS or changes a
profile. Coefficients mirror the verified catalog shape, not MISBI targets.
"""
from copy import deepcopy
from datetime import date, datetime
from decimal import Decimal

import pytest

from backend.corporate_api.errors import CorporateError
from backend.corporate_api.source import (
    BULK_CONVERSION_COLUMNS, BULK_CONVERSION_SCHEMA_SQL, MAX_BULK_CONVERSION_FACTS,
    REQUIRED_COLUMNS, SCHEMA_SQL, ProductionSource, SourceError,
    _bulk_conversion_factor,
)


START, END = date(2026, 1, 1), date(2026, 1, 31)
BULK, CONT = 'bulkQuayVolumesCB', 'contQuayVolumesCB'


def config():
    return {
        'company_id': 'CNT', 'terminals': ['cua_lo', 'ben_thuy'],
        'approved': True, 'date_basis': 'shiftDate', 'production_scope': 'nghe_tinh',
        'report_date': '2026-10-15', 'gate_selection': 'vessel_type',
        'quay_selection': 'source_statistics', 'cargo_catalog_source': 'native_groups',
        'cargo_kind_by_cargo': {'cua_lo': {'2078': 'bulk', '2': 'bulk', '3': 'container'}},
        'bulk_unit_conversion_cargo_ids': {'cua_lo': ['2078']},
        'container_sizes_by_cargo': {'cua_lo': {'3': {'localSzTp': '40F'}}},
    }


def wood(**updates):
    row = {
        'source_id': 101, 'business_date': date(2026, 1, 12),
        'cargo_id': 2078, 'cargo_group_id': 8, 'cargo_name': 'Gỗ tròn',
        'voyage_id': 8000, 'method_id': 10, 'direction_id': 1,
        'native_weight': Decimal('251.631'), 'weight_unit_code': 'M3',
        'quantity': Decimal('251.631'), 'quantity_unit_code': 'M3',
        'tonne_factor': None, 'quay_eligible': 1, 'physical_voyage': 1,
        'ship_id': 91, 'vessel_type_code': 'Cargo Ship', 'production_scope': 'nghe_tinh',
    }
    row.update(updates)
    return row


def manifest(row=None, **updates):
    row = wood() if row is None else row
    linked = {
        'source_id': row['source_id'], 'fact_cargo_id': row['cargo_id'],
        'fact_voyage_id': row['voyage_id'], 'fact_business_date': row['business_date'],
        'fact_weight': row['native_weight'], 'fact_weight_unit_id': 42,
        'fact_deleted': None, 'manifest_id': 10080, 'manifest_cargo_id': row['cargo_id'],
        'manifest_voyage_id': row['voyage_id'], 'manifest_deleted': False,
        'specification_id': 18, 'specification_deleted': None,
    }
    linked.update(updates)
    return linked


def conversion_edges():
    common = {
        'specification_id': 18, 'edge_deleted': None, 'edge_hidden': False,
        'source_unit_deleted': None, 'target_unit_deleted': None,
        'created_at': datetime(2022, 1, 1), 'updated_at': datetime(2025, 1, 9),
        'source_unit_tone': 0, 'target_unit_tone': 0,
    }
    return [
        {**common, 'conversion_id': 57, 'cargo_id': 2078, 'factor': Decimal('1000'),
         'source_unit_id': 42, 'target_unit_id': 14, 'source_unit_code': 'M3', 'target_unit_code': 'KG'},
        {**common, 'conversion_id': 61, 'cargo_id': 0, 'factor': Decimal('0.001'),
         'source_unit_id': 14, 'target_unit_id': 1, 'source_unit_code': 'KG', 'target_unit_code': 'TAN',
         'target_unit_tone': 1, 'updated_at': None},
        {**common, 'conversion_id': 41, 'cargo_id': 2078, 'factor': Decimal('1'),
         'source_unit_id': 1, 'target_unit_id': 42, 'source_unit_code': 'TAN', 'target_unit_code': 'M3',
         'source_unit_tone': 1},
    ]


class Query:
    def __init__(self, rows=None):
        self.rows = [wood()] if rows is None else rows
        self.links = [manifest(row) for row in self.rows]
        self.edges = conversion_edges()
        self.calls = []
        self.failure = None
        self.omit_column = None

    def __call__(self, database, sql, params):
        self.calls.append((database, sql, params))
        assert sql.lstrip().startswith(('SELECT', 'WITH'))
        if sql == SCHEMA_SQL:
            return [{'table_name': table, 'column_name': name}
                    for table, names in REQUIRED_COLUMNS.items() for name in names]
        if sql == BULK_CONVERSION_SCHEMA_SQL:
            if self.failure:
                raise self.failure
            return [{'table_name': table, 'column_name': name}
                    for table, names in BULK_CONVERSION_COLUMNS.items() for name in names
                    if (table, name) != self.omit_column]
        if 'berth_scope.vesselVoyageId AS voyage_id' in sql:
            scopes = {row['voyage_id']: row['production_scope'] for row in self.rows}
            return ([{'voyage_id': voyage, 'production_scope': scope}
                     for voyage, scope in scopes.items()] if database == 'SmartTOS' else [])
        if 't.tallyShiftId AS source_id,t.cargoId AS fact_cargo_id' in sql:
            return deepcopy([row for row in self.links if str(row['source_id']) in params])
        if 'cu.conversionUnitId AS conversion_id' in sql:
            return deepcopy(self.edges)
        return deepcopy(self.rows) if database == 'SmartTOS' else []

    @property
    def support_calls(self):
        return [call for call in self.calls if call[1] == BULK_CONVERSION_SCHEMA_SQL
                or 'fact_cargo_id' in call[1] or 'cu.conversionUnitId AS conversion_id' in call[1]]


def run(query=None, profile=None, resources=None):
    query = Query() if query is None else query
    result = ProductionSource(query).extract(START, END, config() if profile is None else profile,
                                             resources=resources)
    return result, query


def test_exact_active_catalog_path_converts_volume_without_rounding_or_join_multiplication():
    row2 = wood(source_id=102, native_weight=Decimal('2.125'))
    query = Query([wood(), row2])
    result, _ = run(query)
    assert result[BULK]['ready'] and result[BULK]['blockers'] == []
    assert len(result[BULK]['rows']) == 1
    assert result[BULK]['rows'][0]['bulkWeight'] == Decimal('253.756')
    assert len(query.support_calls) == 3
    links, edges = query.support_calls[1:]
    assert links[2] == ('101', '102') and 'IN (CAST(? AS BIGINT),CAST(? AS BIGINT))' in links[1]
    assert edges[2] == ('2078',) and 'IN (CAST(? AS BIGINT))' in edges[1]
    assert all(call[0] == 'SmartTOS' for call in query.support_calls)


@pytest.mark.parametrize('profile_update', [
    {'bulk_unit_conversion_cargo_ids': {}},
    {'bulk_unit_conversion_cargo_ids': {'ben_thuy': ['2078']}},
    {'bulk_unit_conversion_cargo_ids': {'cua_lo': ['2']}},
])
def test_source_conversion_never_activates_without_matching_terminal_and_cargo(profile_update):
    profile = {**config(), **profile_update}
    result, query = run(profile=profile)
    assert result[BULK]['blockers'] == ['WEIGHT_OR_UNIT_UNAVAILABLE']
    assert query.support_calls == []


def test_missing_opt_in_key_retains_previous_behavior():
    profile = config()
    profile.pop('bulk_unit_conversion_cargo_ids')
    result, query = run(profile=profile)
    assert result[BULK]['blockers'] == ['WEIGHT_OR_UNIT_UNAVAILABLE']
    assert query.support_calls == []


@pytest.mark.parametrize('value', [None, [], {'unknown': ['2078']}, {'cua_lo': '2078'},
                                    {'cua_lo': [2078]}, {'cua_lo': ['02078']},
                                    {'cua_lo': ['2078', '2078']}, {'cua_lo': ['-1']}])
def test_invalid_opt_in_is_rejected_before_any_source_read(value):
    query = Query()
    with pytest.raises(SourceError) as exc:
        run(query, {**config(), 'bulk_unit_conversion_cargo_ids': value})
    assert exc.value.code == 'SOURCE_PROFILE' and query.calls == []


@pytest.mark.parametrize('updates', [
    {'production_scope': 'vietsun'}, {'production_scope': 'unclassified'},
    {'quay_eligible': 0}, {'physical_voyage': 0}, {'ship_id': None},
    {'direction_id': 3}, {'method_id': None}, {'native_weight': None},
    {'business_date': date(2025, 12, 31)}, {'native_weight': 0, 'quantity': 0},
    {'weight_unit_code': 'GAU'}, {'tonne_factor': Decimal('1')},
    {'cargo_id': 2},
    {'vessel_type_code': 'Bulk Yard', 'physical_voyage': 0, 'quay_eligible': 0},
])
def test_ineligible_or_unselected_facts_never_request_conversion_data(updates):
    result, query = run(Query([wood(**updates)]), resources=[BULK])
    assert query.support_calls == []
    assert not any('SOURCE_UNAVAILABLE' in item['blockers'] for item in result.values())


def test_container_only_request_ignores_bulk_conversion_even_if_source_unavailable():
    query = Query()
    query.failure = RuntimeError('driver secret')
    result, _ = run(query, resources=[CONT])
    assert query.support_calls == [] and result[CONT]['ready']


@pytest.mark.parametrize('failure,code', [
    (RuntimeError('secret host and password'), 'SOURCE_UNAVAILABLE'),
    (CorporateError(503, 'SOURCE_TIMEOUT', 'secret host and password'), 'SOURCE_TIMEOUT'),
    (SourceError('SOURCE_SCHEMA', 'secret host and password'), 'SOURCE_SCHEMA'),
])
def test_conversion_source_failure_blocks_only_relevant_bulk_and_sanitizes_errors(failure, code):
    query = Query([wood(), wood(source_id=102, cargo_id=3, cargo_name='40F', quantity=1,
                               native_weight=30, tonne_factor=1, weight_unit_code='TAN',
                               quantity_unit_code='CONT')])
    query.failure = failure
    result, _ = run(query)
    assert code in result[BULK]['blockers']
    assert 'WEIGHT_OR_UNIT_UNAVAILABLE' in result[BULK]['blockers']
    assert result[CONT]['ready'] and result[CONT]['rows'][0]['containerWeight'] == 30
    assert result['bulkGateVolumesCB']['ready']
    assert len(query.support_calls) == 1
    assert 'secret' not in str(result)


def test_blocked_wood_does_not_discard_other_accepted_bulk_subtotal():
    query = Query([wood(), wood(source_id=102, cargo_id=2, native_weight=100, tonne_factor=1)])
    query.failure = RuntimeError('private')
    result, _ = run(query)
    assert not result[BULK]['ready']
    assert sum(row['bulkWeight'] for row in result[BULK]['rows']) == 100


@pytest.mark.parametrize('change', [
    {'manifest_cargo_id': 2}, {'fact_cargo_id': 2}, {'manifest_voyage_id': 8001},
    {'fact_voyage_id': 8001}, {'manifest_id': None}, {'specification_id': 19},
    {'specification_id': None}, {'fact_weight_unit_id': 14}, {'fact_deleted': True},
    {'manifest_deleted': True}, {'specification_deleted': True},
    {'fact_business_date': date(2026, 1, 13)}, {'fact_weight': Decimal('251.632')},
])
def test_manifest_must_prove_exact_unchanged_fact_cargo_voyage_specification(change):
    assert _bulk_conversion_factor(wood(), manifest(**change), conversion_edges()) is None


@pytest.mark.parametrize('key', ['fact_deleted', 'manifest_deleted', 'specification_deleted'])
def test_absent_active_flags_are_not_assumed_active(key):
    link = manifest()
    link.pop(key)
    assert _bulk_conversion_factor(wood(), link, conversion_edges()) is None


@pytest.mark.parametrize('index,key,value', [
    (0, 'edge_deleted', True), (0, 'edge_hidden', True),
    (0, 'source_unit_deleted', True), (0, 'target_unit_deleted', True),
    (1, 'source_unit_deleted', True), (1, 'target_unit_deleted', True),
    (2, 'source_unit_deleted', True), (2, 'target_unit_deleted', True),
    (0, 'factor', 0), (0, 'factor', -1), (0, 'factor', Decimal('NaN')),
    (0, 'factor', Decimal('Infinity')), (0, 'factor', None), (0, 'factor', True),
    (0, 'factor', Decimal('1200')), (1, 'factor', Decimal('0.01')),
    (2, 'factor', Decimal('2')), (0, 'cargo_id', 0), (0, 'cargo_id', 2),
    (2, 'cargo_id', 0), (1, 'cargo_id', 2078), (0, 'specification_id', 19),
    (1, 'specification_id', 19), (2, 'specification_id', 19),
    (1, 'target_unit_tone', 0), (2, 'source_unit_tone', 0),
    (0, 'target_unit_id', 15), (1, 'target_unit_id', 2), (2, 'target_unit_id', 43),
    (0, 'conversion_id', None), (0, 'source_unit_id', None),
    (0, 'source_unit_code', 'GAU'), (0, 'target_unit_code', 'TAN'),
    (1, 'created_at', None), (1, 'created_at', '2022-01-01'),
    (0, 'created_at', datetime(1899, 1, 1)),
    (0, 'updated_at', datetime(2026, 1, 13)),
    (0, 'updated_at', datetime(2026, 1, 12)),
    (0, 'updated_at', datetime(2021, 1, 1)),
    (0, 'updated_at', '2025-01-09'),
])
def test_invalid_or_nonunique_conversion_path_remains_unavailable(index, key, value):
    edges = conversion_edges()
    edges[index][key] = value
    assert _bulk_conversion_factor(wood(), manifest(), edges) is None


@pytest.mark.parametrize('index', [0, 1, 2])
def test_missing_or_duplicate_active_edges_are_never_silently_chosen(index):
    edges = conversion_edges()
    assert _bulk_conversion_factor(wood(), manifest(), edges[:index] + edges[index + 1:]) is None
    duplicate = {**edges[index], 'conversion_id': 900}
    assert _bulk_conversion_factor(wood(), manifest(), edges + [duplicate]) is None
    duplicate['factor'] = None
    assert _bulk_conversion_factor(wood(), manifest(), edges + [duplicate]) is None
    duplicate['edge_deleted'] = True
    assert _bulk_conversion_factor(wood(), manifest(), edges + [duplicate]) == Decimal('1')


def test_generic_m3_edge_is_not_a_substitute_for_missing_cargo_specific_conversion():
    edges = conversion_edges()
    edges[0]['cargo_id'] = 0
    assert _bulk_conversion_factor(wood(), manifest(), edges) is None


def test_missing_link_is_still_a_weight_blocker_not_zero_production():
    query = Query()
    query.links = []
    result, _ = run(query)
    assert result[BULK]['blockers'] == ['WEIGHT_OR_UNIT_UNAVAILABLE']
    assert result[BULK]['rows'] == [] and not result[BULK]['ready']


def test_duplicate_manifest_links_cannot_multiply_volume():
    query = Query()
    query.links *= 2
    result, _ = run(query)
    assert 'SOURCE_DATA' in result[BULK]['blockers'] and result[BULK]['rows'] == []


def test_missing_source_schema_is_typed_safe_failure():
    query = Query()
    query.omit_column = ('BaseUnit', 'rowDeleted')
    result, _ = run(query)
    assert 'SOURCE_SCHEMA' in result[BULK]['blockers'] and result[BULK]['rows'] == []


@pytest.mark.parametrize('candidates', [[], [wood(source_id=None)], [wood(cargo_id=None)],
                                           [wood(), wood()],
                                           [wood(source_id=n + 1) for n in range(MAX_BULK_CONVERSION_FACTS + 1)]])
def test_invalid_or_oversized_conversion_batch_is_rejected_without_sql(candidates):
    query = Query()
    with pytest.raises(SourceError) as exc:
        ProductionSource(query)._read_bulk_conversion_support('cua_lo', candidates)
    assert exc.value.code == 'SOURCE_ROW_LIMIT' and query.calls == []


def kaly(**updates):
    return wood(**{'cargo_id': 2079, 'cargo_name': 'Kaly bịch', 'weight_unit_code': 'VỎ BỊCH',
                   'quantity': 0, 'quantity_unit_code': None, 'native_weight': Decimal('758'), **updates})


def kaly_edge(**updates):
    return {**conversion_edges()[0], 'conversion_id': 70, 'cargo_id': 2079, 'factor': Decimal('1'),
            'source_unit_id': 54, 'target_unit_id': 1, 'source_unit_code': 'VỎ BỊCH',
            'target_unit_code': 'TAN', 'source_unit_tone': 1, 'target_unit_tone': 1,
            'created_at': datetime(2023, 7, 24), 'updated_at': datetime(2025, 1, 9), **updates}


def kaly_manifest(**updates):
    return manifest(kaly(), **{'fact_weight_unit_id': 54, **updates})


def kaly_config():
    profile = config()
    profile['cargo_kind_by_cargo']['cua_lo']['2079'] = 'bulk'
    profile['bulk_unit_conversion_cargo_ids']['cua_lo'].append('2079')
    return profile


class KalyQuery(Query):
    def __init__(self, rows=None):
        super().__init__([kaly()] if rows is None else rows)
        self.links = [manifest(row, fact_weight_unit_id=54 if row['cargo_id'] == 2079 else 42)
                      for row in self.rows]
        self.edges = [kaly_edge(), *conversion_edges()]


def test_direct_native_mass_path_requires_both_catalogs_and_needs_no_inverse():
    assert _bulk_conversion_factor(kaly(), kaly_manifest(), [kaly_edge()]) == 1
    query = KalyQuery([kaly(), kaly(source_id=102, native_weight=Decimal('1.057'))])
    result, _ = run(query, kaly_config(), resources=[BULK])
    assert result[BULK]['ready'] and result[BULK]['rows'][0]['bulkWeight'] == Decimal('759.057')
    assert len(query.support_calls) == 3
    assert "su.baseUnitCode=N'VỎ BỊCH' AND tu.baseUnitCode=N'TAN'" in query.support_calls[-1][1]


def test_direct_mass_and_m3_paths_coexist_without_cross_applying_edges():
    result, query = run(KalyQuery([kaly(), wood(source_id=102)]), kaly_config(), resources=[BULK])
    assert result[BULK]['ready']
    by_cargo = {row['cargoCategoryId']: row['bulkWeight'] for row in result[BULK]['rows']}
    assert by_cargo == {'2079': Decimal('758'), '2078': Decimal('251.631')}
    assert len(query.support_calls) == 3 and query.support_calls[-1][2] == ('2078', '2079')


def test_direct_mass_evidence_cannot_replace_a_missing_m3_reciprocal_path():
    direct_for_wood = kaly_edge(cargo_id=2078)
    assert _bulk_conversion_factor(wood(), manifest(), [direct_for_wood]) is None
    edges = conversion_edges()
    assert _bulk_conversion_factor(wood(), manifest(), edges + [direct_for_wood]) == 1
    assert _bulk_conversion_factor(wood(), manifest(), edges[:2] + [direct_for_wood]) is None


def test_wood_only_opt_in_does_not_enable_fertilizer_direct_conversion():
    profile = kaly_config()
    profile['bulk_unit_conversion_cargo_ids'] = {'cua_lo': ['2078']}
    result, query = run(KalyQuery(), profile, resources=[BULK])
    assert query.support_calls == []
    assert result[BULK]['blockers'] == ['WEIGHT_OR_UNIT_UNAVAILABLE']


@pytest.mark.parametrize('unit', ['CAI', 'CONT', 'LAN', 'GÀU', 'BAO', 'M2', 'KG', 'TAN'])
def test_direct_native_mass_rule_cannot_generalize_to_other_source_units(unit):
    # Even mutually agreeing TONE/factor metadata cannot authorize another unit.
    row = kaly(weight_unit_code=unit)
    edge = kaly_edge(source_unit_code=unit)
    assert _bulk_conversion_factor(row, kaly_manifest(), [edge]) is None
    result, query = run(KalyQuery([row]), kaly_config(), resources=[BULK])
    assert query.support_calls == []
    assert result[BULK]['blockers'] == ['WEIGHT_OR_UNIT_UNAVAILABLE']


@pytest.mark.parametrize('key,value', [
    ('source_unit_tone', None), ('source_unit_tone', 0), ('source_unit_tone', 2),
    ('source_unit_tone', True), ('target_unit_tone', None), ('target_unit_tone', 0),
    ('factor', 0), ('factor', -1), ('factor', Decimal('NaN')), ('factor', Decimal('Infinity')),
    ('factor', None), ('factor', True), ('factor', Decimal('0.5')), ('factor', 2),
    ('cargo_id', 0), ('cargo_id', 2078), ('specification_id', 19),
    ('edge_deleted', True), ('edge_hidden', True),
    ('source_unit_deleted', True), ('target_unit_deleted', True),
    ('conversion_id', None), ('source_unit_id', None), ('source_unit_id', 1),
    ('source_unit_id', 55), ('target_unit_id', None),
    ('source_unit_code', 'CAI'), ('target_unit_code', 'KG'),
    ('created_at', None), ('created_at', datetime(2026, 1, 12)),
    ('updated_at', datetime(2026, 1, 12)), ('updated_at', datetime(2026, 1, 13)),
    ('updated_at', datetime(2022, 1, 1)), ('updated_at', '2025-01-09'),
])
def test_direct_mass_conversion_rejects_ambiguous_unconfirmed_or_inactive_evidence(key, value):
    assert _bulk_conversion_factor(kaly(), kaly_manifest(), [kaly_edge(**{key: value})]) is None


@pytest.mark.parametrize('key', ['source_unit_tone', 'target_unit_tone', 'source_unit_deleted',
                               'target_unit_deleted', 'edge_deleted', 'edge_hidden'])
def test_direct_mass_conversion_does_not_default_missing_metadata(key):
    edge = kaly_edge()
    edge.pop(key)
    assert _bulk_conversion_factor(kaly(), kaly_manifest(), [edge]) is None


@pytest.mark.parametrize('change', [
    {'manifest_cargo_id': 2078}, {'fact_cargo_id': 2078}, {'manifest_voyage_id': 8001},
    {'fact_voyage_id': 8001}, {'manifest_id': None}, {'specification_id': 19},
    {'specification_id': None}, {'fact_weight_unit_id': 42}, {'fact_deleted': True},
    {'manifest_deleted': True}, {'specification_deleted': True},
    {'fact_business_date': date(2026, 1, 13)}, {'fact_weight': Decimal('757')},
])
def test_direct_mass_path_keeps_exact_manifest_link_and_unchanged_fact_checks(change):
    assert _bulk_conversion_factor(kaly(), kaly_manifest(**change), [kaly_edge()]) is None


def test_direct_mass_conversion_never_chooses_an_active_duplicate_even_with_identical_factor():
    assert _bulk_conversion_factor(kaly(), kaly_manifest(), []) is None
    edges = [kaly_edge(), kaly_edge(conversion_id=900)]
    assert _bulk_conversion_factor(kaly(), kaly_manifest(), edges) is None
    edges[1]['factor'] = None
    assert _bulk_conversion_factor(kaly(), kaly_manifest(), edges) is None
    edges[1]['edge_deleted'] = True
    assert _bulk_conversion_factor(kaly(), kaly_manifest(), edges) == 1


def test_direct_mass_conversion_failure_does_not_restore_or_invent_a_tonne_value():
    query = KalyQuery()
    query.failure = RuntimeError('private driver detail')
    result, _ = run(query, kaly_config(), resources=[BULK])
    assert set(result[BULK]['blockers']) == {'SOURCE_UNAVAILABLE', 'WEIGHT_OR_UNIT_UNAVAILABLE'}
    assert result[BULK]['rows'] == [] and 'private' not in str(result)


def test_direct_mass_path_does_not_bypass_quay_method_selection_or_scope():
    query = KalyQuery([kaly(), kaly(source_id=102, quay_eligible=0),
                       kaly(source_id=103, voyage_id=8001, production_scope='vietsun')])
    result, _ = run(query, kaly_config(), resources=[BULK])
    assert result[BULK]['ready'] and result[BULK]['rows'][0]['bulkWeight'] == 758
    assert query.support_calls[1][2] == ('101',)

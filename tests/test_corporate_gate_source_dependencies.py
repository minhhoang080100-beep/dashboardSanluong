"""Gate-by-yard extraction must not depend on unused vessel berth history.

Exercise the real source reader with synthetic query/metadata adapters. These
checks do not connect to SQL Server or establish live performance.
"""
from datetime import date

import pytest

from backend.corporate_api.errors import CorporateError
from backend.corporate_api.source import ProductionSource, REQUIRED_COLUMNS, SCHEMA_SQL
from test_corporate_source import Query, fact, profile, yard_fact, yard_profile


START, END = date(2026, 9, 1), date(2026, 9, 30)
GATES = ('contGateVolumesCB', 'bulkGateVolumesCB')


class BerthFailureQuery(Query):
    def __init__(self, rows, *, failure=None):
        super().__init__(rows)
        self.failure = failure
        self.descriptions = []

    def describe_table(self, database, table):
        self.descriptions.append((database, table))
        if table == self.failure:
            raise CorporateError(503, 'SOURCE_TIMEOUT', 'private-driver-detail')
        return [{'column_name': column} for column in REQUIRED_COLUMNS[table]]

    def __call__(self, database, sql, params):
        if self.failure == 'query' and 'berth_scope.vesselVoyageId AS voyage_id' in sql:
            self.calls.append((database, sql, params))
            raise CorporateError(503, 'SOURCE_TIMEOUT', 'private-driver-detail')
        return super().__call__(database, sql, params)


def extract(query, resources, config=None):
    return ProductionSource(query).extract(START, END, config or yard_profile(), resources=resources)


def yard_rows():
    return [yard_fact(source_id=1), yard_fact(source_id=2, cargo_id=2, quantity_unit_code='TAN')]


@pytest.mark.parametrize('resources', [[GATES[0]], [GATES[1]], list(GATES)])
@pytest.mark.parametrize('failure', ['DoBerth', 'Berth', 'query'])
def test_yard_gate_only_has_no_berth_metadata_or_query_dependency(resources, failure):
    baseline = extract(BerthFailureQuery(yard_rows()), resources)
    source = BerthFailureQuery(yard_rows(), failure=failure)
    result = extract(source, resources)
    for resource in resources:
        assert result[resource]['ready'], result[resource]['blockers']
        assert result[resource]['rows'] == baseline[resource]['rows']
        assert result[resource]['coverage'] == [['20260901', '20260930']]
    assert not any(table in {'DoBerth', 'Berth'} for _, table in source.descriptions)
    assert not any('berth_scope.vesselVoyageId AS voyage_id' in sql for _, sql, _ in source.calls)


@pytest.mark.parametrize('resources', [[GATES[0]], [GATES[1]], list(GATES)])
def test_fallback_metadata_need_not_contain_berth_tables_for_yard_gates(resources):
    base = Query(yard_rows())

    def query(database, sql, params):
        rows = base(database, sql, params)
        if sql == SCHEMA_SQL:
            return [row for row in rows if row['table_name'] not in {'DoBerth', 'Berth'}]
        assert 'berth_scope.vesselVoyageId AS voyage_id' not in sql
        return rows

    result = extract(query, resources)
    assert all(result[resource]['ready'] for resource in resources)


@pytest.mark.parametrize('mode', ['quay', 'mixed', 'all', 'method_gate'])
@pytest.mark.parametrize('failure', ['DoBerth', 'Berth', 'query'])
def test_quay_mixed_and_method_gate_keep_berth_dependencies(mode, failure):
    config = profile() if mode == 'method_gate' else yard_profile()
    resources = {'quay': ['contQuayVolumesCB'], 'mixed': [GATES[0], 'contQuayVolumesCB'],
                 'all': None, 'method_gate': [GATES[0]]}[mode]
    source = BerthFailureQuery([fact(method_id=20 if mode == 'method_gate' else 10)], failure=failure)
    result = extract(source, resources, config)
    checked = resources or list(result)
    for resource in checked:
        assert result[resource]['blockers'] == ['SOURCE_TIMEOUT']
        assert not result[resource]['rows'] and not result[resource]['coverage']
    assert 'private-driver-detail' not in str(result)


@pytest.mark.parametrize('table,column', [('TallyShift', 'shiftDate'), ('Vessel', 'vesselTypeId'),
                                         ('VesselVoyage', 'vesselId'), ('VesselType', 'vesselTypeCode')])
def test_yard_gate_still_validates_its_fact_and_location_schema(table, column):
    source = BerthFailureQuery(yard_rows())
    original = source.describe_table

    def describe(database, requested_table):
        rows = original(database, requested_table)
        return [row for row in rows if (requested_table, row['column_name']) != (table, column)]

    source.describe_table = describe
    result = extract(source, list(GATES))
    assert all(result[resource]['blockers'] == ['SOURCE_SCHEMA'] for resource in GATES)
    assert source.calls == []


@pytest.mark.parametrize('row_changes', [{'voyage_id': -1}, {'voyage_id': True}, {'voyage_id': 'bad'}])
def test_yard_gate_still_rejects_malformed_fact_lineage(row_changes):
    source = BerthFailureQuery([yard_fact(**row_changes)])
    result = extract(source, [GATES[0]])[GATES[0]]
    assert result['blockers'] == ['SOURCE_DATA']
    assert result['rows'] == [] and result['coverage'] == []


@pytest.mark.parametrize('scope', ['nghe_tinh', 'vietsun', 'all_activity'])
def test_gate_rows_stay_independent_of_berth_assignment_and_configured_quay_scope(scope):
    config = {**yard_profile(), 'production_scope': scope}
    source = BerthFailureQuery(yard_rows(), failure='query')
    result = extract(source, list(GATES), config)
    baseline = extract(BerthFailureQuery(yard_rows()), list(GATES), config)
    assert all(result[name]['ready'] and result[name]['rows'] == baseline[name]['rows'] for name in GATES)


def test_mixed_request_retains_first_berth_scope_filtering_for_quay():
    rows = [yard_fact(source_id=1), fact(source_id=2, production_scope='vietsun'),
            fact(source_id=3, production_scope='nghe_tinh')]
    source = BerthFailureQuery(rows)
    result = extract(source, [GATES[0], 'contQuayVolumesCB'])
    assert result[GATES[0]]['ready'] and len(result[GATES[0]]['rows']) == 1
    assert result['contQuayVolumesCB']['ready'] and len(result['contQuayVolumesCB']['rows']) == 1
    assert result['contQuayVolumesCB']['rows'][0]['containerWeight'] == rows[2]['native_weight']
    assert result['contQuayVolumesCB']['excluded_scope_rows'] == 1
    assert any('berth_scope.vesselVoyageId AS voyage_id' in sql for _, sql, _ in source.calls)


def test_gate_only_preserves_exact_fact_queries_and_rows_from_mixed_reader():
    rows = yard_rows() + [fact(source_id=3), fact(source_id=4, production_scope='vietsun')]
    mixed, gate = BerthFailureQuery(rows), BerthFailureQuery(rows)
    before = extract(mixed, [*GATES, 'contQuayVolumesCB'])
    after = extract(gate, list(GATES))
    for resource in GATES:
        assert after[resource]['rows'] == before[resource]['rows']
        assert after[resource]['source_row_count'] == before[resource]['source_row_count']
        assert after[resource]['ready'] and not after[resource]['issue_counts']
    fact_calls = lambda query: [call for call in query.calls if 't.tallyShiftId AS source_id' in call[1]]
    assert fact_calls(gate) == fact_calls(mixed)
    assert len(fact_calls(gate)) == 2  # Both terminals are still read.
    assert len(mixed.calls) - len(gate.calls) == 1  # One populated terminal's unused berth SELECT.
    assert len(mixed.descriptions) - len(gate.descriptions) == 4  # Two berth tables per terminal.

"""Production filters narrow relational source rows before bounded TOP reads.

SQLite executes the generated period CTE and historical berth query after the
minimal SQL Server dialect changes. This does not test SQL Server query plans.
"""
from copy import deepcopy
from datetime import date
import re
import sqlite3

import pytest

from backend.corporate_api import source as module
from backend.corporate_api.source import ProductionSource, REQUIRED_COLUMNS, SCHEMA_SQL
from test_corporate_source import fact, profile


DAY = date(2026, 9, 16)


class RelationalSource:
    def __init__(self):
        self.calls = []
        self.rows = {'SmartTOS': {}, 'SmartTOS_BenThuy': {}}
        self.databases = {name: sqlite3.connect(':memory:') for name in self.rows}
        for db in self.databases.values():
            db.row_factory = sqlite3.Row
            db.executescript('''
                CREATE TABLE TallyShift(tallyShiftId INTEGER, shiftDate TEXT, cargoId INTEGER,
                    jobMethodId INTEGER, cargoDirectId INTEGER, weightNetSum NUMERIC,
                    quantityTotalSum INTEGER, consigneeId INTEGER, vesselVoyageId INTEGER,
                    quantityUnitId INTEGER, weightUnitId INTEGER, rowDeleted INTEGER);
                CREATE TABLE VesselVoyage(vesselVoyageId INTEGER PRIMARY KEY,vesselId INTEGER);
                CREATE TABLE DoBerth(vesselVoyageId INTEGER,berthId INTEGER,ATB TEXT,
                    ATA TEXT,rowDeleted INTEGER,isArrival INTEGER);
                CREATE TABLE Berth(berthId INTEGER PRIMARY KEY,berthCode TEXT);
                INSERT INTO Berth VALUES(12,'C4'),(13,'C5');
            ''')

    def add(self, database='SmartTOS', *, berth=12, **changes):
        row = fact(**changes)
        self.rows[database][row['source_id']] = row
        db = self.databases[database]
        db.execute('INSERT INTO TallyShift VALUES(?,?,?,?,?,?,?,?,?,1,1,0)', (
            row['source_id'], row['business_date'].isoformat(), row['cargo_id'], row['method_id'],
            row['direction_id'], str(row['native_weight']), row['quantity'], row['customer_id'], row['voyage_id']))
        db.execute('INSERT INTO VesselVoyage VALUES(?,?)', (row['voyage_id'], row['ship_id']))
        db.execute("INSERT INTO DoBerth VALUES(?,?, '2026-08-01T08:00:00',NULL,0,1)",
                   (row['voyage_id'], berth))

    def __call__(self, database, sql, params):
        self.calls.append((database, sql, params))
        if sql == SCHEMA_SQL:
            return [{'table_name': table, 'column_name': column}
                    for table, columns in REQUIRED_COLUMNS.items() for column in columns]
        db = self.databases[database]
        portable = sql.replace(database + '.dbo.', '').replace('ISNULL(', 'COALESCE(')
        params = tuple(value.isoformat() if type(value) is date else value for value in params)
        if 'WITH period_facts AS' in sql:
            # Keep the actual WHERE/EXISTS/parameter semantics before the cap;
            # already-covered cargo/unit projection is represented by fixtures.
            cte = portable[:portable.index('\n    SELECT TOP')]
            keys = db.execute(cte + '\nSELECT tallyShiftId FROM period_facts '
                'ORDER BY shiftDate,tallyShiftId LIMIT ?', [*params, module.MAX_SOURCE_ROWS + 1]).fetchall()
            return [deepcopy(self.rows[database][row['tallyShiftId']]) for row in keys]
        assert 'berth_scope.vesselVoyageId AS voyage_id' in sql
        portable = re.sub(r'SELECT TOP \(\d+\)', 'SELECT', portable)
        return [dict(row) for row in db.execute(portable, params).fetchall()[:module.MAX_BERTH_ROWS + 1]]

    def close(self):
        for db in self.databases.values():
            db.close()


@pytest.fixture
def source(monkeypatch):
    monkeypatch.setattr(module, 'MAX_SOURCE_ROWS', 2)
    monkeypatch.setattr(module, 'MAX_BERTH_ROWS', 2)
    query = RelationalSource()
    yield query
    query.close()


@pytest.mark.parametrize('filters', [{'shipId': '9'}, {'handlingMethodId': '10'},
                                    {'shipId': '9', 'handlingMethodId': '10'}])
def test_small_filtered_result_is_not_blocked_by_unrelated_period_row_cap(source, filters):
    for key in range(1, 4):
        source.add(source_id=key, ship_id=99, method_id=11)
    # The requested fact sorts after the capped unfiltered prefix.
    source.add(source_id=4, ship_id=9, method_id=10)
    result = ProductionSource(source).extract(DAY, DAY, profile(),
        resources=['contQuayVolumesCB'], production_filters=filters)['contQuayVolumesCB']
    assert result['ready'], result['blockers']
    assert len(result['rows']) == 1
    assert result['rows'][0]['shipId'] == '9'
    assert result['rows'][0]['containerTEU'] == 2
    assert result['source_row_count'] == 1


def test_requested_result_over_cap_still_fails_closed(source):
    for key in range(1, 4):
        source.add(source_id=key)
    result = ProductionSource(source).extract(DAY, DAY, profile(),
        resources=['contQuayVolumesCB'], production_filters={'shipId': '9'})['contQuayVolumesCB']
    assert not result['ready'] and 'SOURCE_ROW_LIMIT' in result['blockers']
    assert result['coverage'] == [] and result['rows'] == []


def test_both_filters_are_combined_with_and_before_source_caps(source):
    source.add(source_id=1, ship_id=9, method_id=11)
    source.add(source_id=2, ship_id=99, method_id=10)
    source.add(source_id=3, ship_id=99, method_id=11)
    source.add(source_id=4, ship_id=9, method_id=10)
    result = ProductionSource(source).extract(DAY, DAY, profile(),
        resources=['contQuayVolumesCB'], production_filters={
            'handlingMethodId': '10', 'shipId': '9'})['contQuayVolumesCB']
    assert result['ready'] and result['source_row_count'] == 1
    assert result['rows'][0]['containerWeight'] == fact()['native_weight']
    for _, sql, params in source.calls:
        if sql == SCHEMA_SQL:
            continue
        assert sql.count('?') == len(params) == 4
        assert params == (DAY, date(2026, 9, 17), '9', '10')
        assert 'vesselId=CAST(? AS BIGINT)' in sql
        assert 't.jobMethodId=CAST(? AS BIGINT)' in sql


def test_native_id_filter_queries_both_sources_and_preserves_contributing_provenance(source):
    for database in source.databases:
        for key in range(1, 4):
            source.add(database, source_id=key, ship_id=99, method_id=11)
        source.add(database, source_id=4)
    config = profile()
    config['cargo_kind_by_cargo']['ben_thuy'] = deepcopy(config['cargo_kind_by_cargo']['cua_lo'])
    config['container_sizes_by_cargo']['ben_thuy'] = deepcopy(config['container_sizes_by_cargo']['cua_lo'])
    result = ProductionSource(source).extract(DAY, DAY, config,
        resources=['contQuayVolumesCB'], production_filters={'shipId': '9'})['contQuayVolumesCB']
    assert result['ready'] and result['source_row_count'] == 2
    assert len(result['rows']) == 1
    assert result['rows'][0]['containerWeight'] == fact()['native_weight'] * 2
    assert result['rows'][0]['containerTEU'] == 4
    assert result['rows'][0]['_sourceTerminals'] == ['ben_thuy', 'cua_lo']
    assert [name for name, sql, _ in source.calls if 'WITH period_facts AS' in sql] == list(source.databases)


def test_filter_only_narrows_voyages_not_their_historical_first_berth(source):
    for key in range(1, 4):
        source.add(source_id=key, ship_id=99, method_id=11)
    source.add(source_id=4, berth=13)
    source.databases['SmartTOS'].execute(
        "INSERT INTO DoBerth VALUES(4,12,'2026-09-16T08:00:00',NULL,0,1)")
    config = profile()
    config['production_scope'] = 'nghe_tinh'
    result = ProductionSource(source).extract(DAY, DAY, config,
        resources=['contQuayVolumesCB'], production_filters={'shipId': '9'})['contQuayVolumesCB']
    assert result['ready'] and result['rows'] == []
    assert result['excluded_scope_rows'] == 1


def test_requested_voyages_over_berth_cap_still_fail_closed(source, monkeypatch):
    monkeypatch.setattr(module, 'MAX_SOURCE_ROWS', 4)
    for key in range(1, 4):
        source.add(source_id=key)
    result = ProductionSource(source).extract(DAY, DAY, profile(),
        resources=['contQuayVolumesCB'], production_filters={'shipId': '9'})['contQuayVolumesCB']
    assert not result['ready'] and 'SOURCE_ROW_LIMIT' in result['blockers']
    assert result['coverage'] == [] and result['rows'] == []


@pytest.mark.parametrize('identity', ['09', '9.0', '9e0', '9223372036854775808',
                                      '9 OR 1=1', '\uff19', '0', '-9'])
def test_noncanonical_ids_never_reach_sql_numeric_cast_or_match_native_alias(source, identity):
    for key in range(1, 4):
        source.add(source_id=key)
    result = ProductionSource(source).extract(DAY, DAY, profile(),
        resources=['contQuayVolumesCB'], production_filters={'shipId': identity})['contQuayVolumesCB']
    assert result['ready'] and result['rows'] == []
    for _, sql, params in source.calls:
        if sql == SCHEMA_SQL:
            continue
        assert ' AND 1=0' in sql
        assert params == (DAY, date(2026, 9, 17))
        assert identity not in params


def test_no_filter_retains_complete_period_cap(source):
    for key in range(1, 4):
        source.add(source_id=key)
    result = ProductionSource(source).extract(DAY, DAY, profile(),
        resources=['contQuayVolumesCB'])['contQuayVolumesCB']
    assert not result['ready'] and 'SOURCE_ROW_LIMIT' in result['blockers']
    assert result['coverage'] == [] and result['rows'] == []

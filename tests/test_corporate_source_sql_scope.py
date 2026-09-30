"""Period-scoped berth history uses real generated SQL on relational fixtures.

SQLite executes the portable CTE/berth portions. SQL Server query-plan timing
must still be verified separately against the source; these are semantic tests.
"""
from datetime import date
import sqlite3

import pytest

from backend.berth_scope import initial_berth_query
from backend.corporate_api.source import MAX_SOURCE_ROWS, MAX_BERTH_ROWS, ProductionSource, _facts_sql, _berths_sql
from test_corporate_source import Query, fact


def portable_scope_sql(terminal='cua_lo'):
    schema = 'SmartTOS.dbo' if terminal == 'cua_lo' else 'SmartTOS_BenThuy.dbo'
    # Execute the exact standalone berth query, changing only dialect syntax.
    sql = _berths_sql(terminal).replace(f'SELECT TOP ({MAX_BERTH_ROWS + 1})', 'SELECT')
    return sql.replace(schema + '.', '').replace('ISNULL(', 'COALESCE(')


@pytest.fixture
def source_db():
    db = sqlite3.connect(':memory:')
    db.row_factory = sqlite3.Row
    db.executescript('''
        CREATE TABLE TallyShift(tallyShiftId INTEGER, shiftDate TEXT, cargoId INTEGER,
            jobMethodId INTEGER, cargoDirectId INTEGER, weightNetSum NUMERIC,
            quantityTotalSum INTEGER, consigneeId INTEGER, vesselVoyageId INTEGER,
            quantityUnitId INTEGER, weightUnitId INTEGER, rowDeleted INTEGER);
        CREATE TABLE DoBerth(vesselVoyageId INTEGER, berthId INTEGER,
            ATB TEXT, ATA TEXT, rowDeleted INTEGER, isArrival INTEGER);
        CREATE TABLE Berth(berthId INTEGER PRIMARY KEY, berthCode TEXT);
        INSERT INTO Berth VALUES (13,'C5'),(12,'C4');
    ''')
    yield db
    db.close()


def add_fact(db, key, voyage, day='2026-09-16', deleted=None):
    db.execute('INSERT INTO TallyShift VALUES(?,?,1,10,1,30,1,7,?,1,1,?)',
               (key, day, voyage, deleted))


def add_berth(db, voyage, berth, day, *, deleted=None, arrival=1):
    db.execute('INSERT INTO DoBerth VALUES(?,?,?,NULL,?,?)',
               (voyage, berth, day, deleted, arrival))


@pytest.mark.parametrize('first,later,scope', [(13,12,'vietsun'), (12,13,'nghe_tinh')])
def test_period_voyages_keep_the_first_historical_berth(source_db, first, later, scope):
    add_fact(source_db, 1, 100)
    add_fact(source_db, 2, 100)  # Several shifts do not multiply berth assignments.
    add_berth(source_db, 100, first, '2026-08-01T12:00:00')
    add_berth(source_db, 100, later, '2026-09-16T12:00:00')
    rows = source_db.execute(portable_scope_sql(), ('2026-09-16','2026-09-17')).fetchall()
    assert len(rows) == 1
    assert rows[0]['voyage_id'] == 100
    assert rows[0]['initial_berth_at'] == '2026-08-01T12:00:00'
    assert rows[0]['initial_berth_id'] == first
    assert rows[0]['production_scope'] == scope


def test_only_active_facts_in_half_open_period_select_voyages(source_db):
    for key, voyage, day, deleted in [(1,100,'2026-09-16',None),
            (2,101,'2026-09-16',0), (3,102,'2026-09-16',1),
            (4,103,'2026-09-15',None), (5,104,'2026-09-17',None)]:
        add_fact(source_db,key,voyage,day,deleted)
        add_berth(source_db,voyage,13,'2026-08-01T12:00:00')
    add_fact(source_db,6,None)
    add_berth(source_db,999,13,'2026-08-01T12:00:00')  # No selected facts.
    rows = source_db.execute(portable_scope_sql(), ('2026-09-16','2026-09-17')).fetchall()
    assert {row['voyage_id'] for row in rows} == {100,101}


def test_old_ambiguous_history_and_source_terminal_rule_are_preserved(source_db):
    add_fact(source_db,1,100)
    add_berth(source_db,100,13,None)  # Confirmed arrival without an actual date.
    add_berth(source_db,100,12,'2026-09-16T12:00:00')
    add_fact(source_db,2,101)
    add_berth(source_db,101,13,'2026-08-01T12:00:00')
    rows = source_db.execute(portable_scope_sql('ben_thuy'), ('2026-09-16','2026-09-17')).fetchall()
    by_voyage = {row['voyage_id']:row for row in rows}
    assert by_voyage[100]['production_scope'] == 'unclassified'
    assert by_voyage[101]['production_scope'] == 'nghe_tinh'


def test_fact_read_keeps_two_date_parameters_row_limit_and_order():
    query = Query([fact()])
    rows = ProductionSource(query)._read_terminal('cua_lo',date(2026,9,16),date(2026,9,30))
    assert rows == [fact()]
    assert len(query.calls) == 3  # schema, independent facts, independent berth lookup
    _,sql,params = query.calls[1]
    assert params == (date(2026,9,16),date(2026,10,1))
    assert sql.count('?') == len(params) == 2
    assert f'SELECT TOP ({MAX_SOURCE_ROWS + 1})' in sql
    assert sql.rstrip().endswith('ORDER BY t.shiftDate,t.tallyShiftId')
    assert 'DoBerth' not in sql and 'berth_scope' not in sql
    assert 't.vesselVoyageId AS voyage_id' in sql
    _,berths,berth_params = query.calls[2]
    assert berth_params == params and berths.count('?') == 2
    assert f'SELECT TOP ({MAX_BERTH_ROWS + 1})' in berths
    assert 'period_voyages' in berths


def test_tied_distinct_first_berths_stay_unclassified(source_db):
    add_fact(source_db,1,100)
    add_berth(source_db,100,13,'2026-08-01T12:00:00')
    add_berth(source_db,100,12,'2026-08-01T12:00:00')
    rows = source_db.execute(portable_scope_sql(), ('2026-09-16','2026-09-17')).fetchall()
    assert len(rows) == 1 and rows[0]['production_scope'] == 'unclassified'
    assert rows[0]['berth_assignment_status'] == 'ambiguous'


def test_missing_historical_assignment_is_not_replaced_by_a_dummy_port_scope(source_db):
    add_fact(source_db,1,100)
    facts = Query([fact(voyage_id=100)])
    def query(database, sql, params):
        if 'berth_scope.vesselVoyageId AS voyage_id' in sql:
            return [dict(row) for row in source_db.execute(portable_scope_sql(), tuple(value.isoformat() for value in params))]
        return facts(database, sql, params)
    rows = ProductionSource(query)._read_terminal('cua_lo',date(2026,9,16),date(2026,9,16))
    assert rows[0]['production_scope'] == 'unclassified'
    assert facts.rows[0]['production_scope'] == 'nghe_tinh'  # caller data not mutated


@pytest.mark.parametrize('first,later,scope', [(13,12,'vietsun'), (12,13,'nghe_tinh')])
def test_python_attachment_uses_initial_berth_for_all_shift_rows(source_db, first, later, scope):
    add_fact(source_db,1,100)
    add_fact(source_db,2,100)
    add_berth(source_db,100,first,'2026-08-01T12:00:00')
    add_berth(source_db,100,later,'2026-09-16T12:00:00')
    facts = Query([fact(voyage_id=100),fact(source_id=2,voyage_id=100)])
    def query(database, sql, params):
        if 'berth_scope.vesselVoyageId AS voyage_id' in sql:
            return [dict(row) for row in source_db.execute(portable_scope_sql(), tuple(value.isoformat() for value in params))]
        return facts(database,sql,params)
    rows = ProductionSource(query)._read_terminal('cua_lo',date(2026,9,16),date(2026,9,16))
    assert len(rows) == 2 and {row['production_scope'] for row in rows} == {scope}


def test_existing_dashboard_modes_do_not_depend_on_period_cte():
    for mode in ('all','voyage'):
        sql = initial_berth_query('SmartTOS.dbo','cua_lo',selection=mode)
        assert 'period_voyages' not in sql
        assert sql.count('?') == (1 if mode == 'voyage' else 0)


@pytest.mark.parametrize('selection', ['period_voyages; DELETE FROM DoBerth',None,[]])
def test_berth_scope_mode_remains_allowlisted(selection):
    with pytest.raises(ValueError):
        initial_berth_query('SmartTOS.dbo','cua_lo',selection=selection)

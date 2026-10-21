"""Execute the generated typed-unit relation on small relational fixtures.

Only SQL Server dialect spelling changes for SQLite. Source classification and
same-group policy are tested separately against the production Python helper.
"""
import re
import sqlite3

import pytest

from backend.corporate_api.source import _facts_sql


def typed_size_query(terminal='cua_lo'):
    schema = ('SmartTOS' if terminal == 'cua_lo' else 'SmartTOS_BenThuy') + '.dbo'
    sql = _facts_sql(terminal)
    first = sql.index('SELECT cargoName AS cargo_name,MIN(cargoId) AS cargo_id')
    last = sql.index('LEFT JOIN ' + schema + '.BaseUnit u', first)
    relation = 'LEFT JOIN (\n' + sql[first:last]
    relation = (re.sub(r"\bN'", "'", relation).replace(schema + '.', '')
                .replace('COUNT_BIG(', 'COUNT(').replace('ISNULL(', 'COALESCE(')
                .replace("'CONT '+typed_size.cargo_name", "'CONT '||typed_size.cargo_name"))
    return ('''SELECT qu.baseUnitCode,typed_size.cargo_id,typed_size.cargo_name,
               typed_size.cargo_group_id FROM BaseUnit qu ''' + relation
            + ' WHERE qu.baseUnitId=?')


@pytest.fixture
def typed_db():
    db = sqlite3.connect(':memory:')
    db.executescript('''
        CREATE TABLE Cargo(cargoId INTEGER,cargoName TEXT,cargoGroupId INTEGER,rowDeleted INTEGER);
        CREATE TABLE BaseUnit(baseUnitId INTEGER PRIMARY KEY,baseUnitCode TEXT);
        INSERT INTO BaseUnit VALUES(1,'CONT 20E'),(2,'CONT 20F'),(3,'CONT'),
          (4,'CONT 40F'),(5,'CONT 20R'),(6,'20E'),(7,'CONT 20 E'),(8,'CONT 45F');
    ''')
    yield db
    db.close()


def candidates(db, cargo_rows, unit=1, terminal='cua_lo'):
    db.executemany('INSERT INTO Cargo VALUES(?,?,?,?)', cargo_rows)
    return db.execute(typed_size_query(terminal), (unit,)).fetchall()


@pytest.mark.parametrize('terminal', ['cua_lo', 'ben_thuy'])
@pytest.mark.parametrize('unit,cargo_id,code', [(1,2127,'20E'),(2,2125,'20F')])
def test_explicit_unit_maps_to_one_active_native_size(typed_db, terminal, unit, cargo_id, code):
    assert candidates(typed_db, [(cargo_id,code,1034,None)],unit,terminal) == [
        ('CONT '+code,cargo_id,code,1034)]


def test_deleted_duplicate_cannot_override_unique_active_size(typed_db):
    assert candidates(typed_db, [(2127,'20E',1034,None),(999,'20E',5,1)]) == [
        ('CONT 20E',2127,'20E',1034)]


@pytest.mark.parametrize('rows', [
    [(2127,'20E',1034,0),(999,'20E',1034,None)],
    [(2127,'20E',1034,0),(999,'20E',5,0)],
    [(2127,'20E',1034,0),(2127,'20E',1034,0)],
    [(2127,'20E',1034,1)],
    [(2125,'20F',1034,0)],
    [],
])
def test_ambiguous_deleted_or_absent_candidate_has_no_relation(typed_db, rows):
    assert candidates(typed_db,rows) == [('CONT 20E',None,None,None)]


@pytest.mark.parametrize('unit', [3,4,5,6,7,8])
def test_generic_unsupported_or_malformed_unit_does_not_gain_a_size(typed_db, unit):
    rows = [(2127,'20E',1034,0),(2125,'20F',1034,0),
            (2126,'40F',1034,0),(2130,'45F',1034,0)]
    result = candidates(typed_db,rows,unit)
    assert len(result) == 1 and result[0][1:] == (None,None,None)


def test_unrelated_duplicate_does_not_invalidate_the_other_container_type(typed_db):
    rows = [(2127,'20E',1034,0),(999,'20E',1034,0),(2125,'20F',1034,None)]
    assert candidates(typed_db,rows,2) == [('CONT 20F',2125,'20F',1034)]

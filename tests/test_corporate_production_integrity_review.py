"""Relational checks of the API's generated mass-conversion selection.

SQLite executes the same aggregate/join/null semantics as the generated
SQL Server conversion subquery. This is not a production schema/plan test.
"""
import re
import sqlite3

import pytest

from backend.corporate_api.source import _facts_sql


def conversion_query():
    sql = _facts_sql('cua_lo')
    first = sql.index('SELECT cu.baseUnitId,MIN(cu.unitValue) AS tonne_factor')
    last = sql.index(') mass_conversion', first)
    sql = sql[first:last]
    return (re.sub(r"\bN'", "'", sql).replace('SmartTOS.dbo.', '')
            .replace('ISNULL(', 'COALESCE('))


@pytest.fixture
def conversion_db():
    db = sqlite3.connect(':memory:')
    db.executescript('''
        CREATE TABLE BaseUnit(baseUnitId INTEGER PRIMARY KEY,baseUnitCode TEXT,TONE NUMERIC);
        CREATE TABLE ConversionUnit(baseUnitId INTEGER,unitId INTEGER,unitValue NUMERIC,
                                    cargoId INTEGER,rowDeleted INTEGER);
        INSERT INTO BaseUnit VALUES(1,'TAN',1),(14,'KG',NULL),(42,'M3',NULL),(34,'GAU',0);
    ''')
    yield db
    db.close()


def selected_factors(db, rows):
    db.executemany('INSERT INTO ConversionUnit VALUES(?,?,?,?,?)', rows)
    return db.execute(conversion_query()).fetchall()


@pytest.mark.parametrize('rows', [
    [(14, 1, .001, 0, None)],
    [(14, 1, .001, 0, None), (14, 1, .001, 0, 0)],
    [(14, 1, .001, 0, 0), (14, 1, .5, 0, 1)],
])
def test_generic_kg_mass_conversion_accepts_only_consistent_active_factors(conversion_db, rows):
    assert selected_factors(conversion_db, rows) == [(14, .001)]


@pytest.mark.parametrize('rows', [
    [(14, 1, .001, 0, 0), (14, 1, None, 0, 0)],
    [(14, 1, .001, 0, 0), (14, 1, .002, 0, 0)],
    [(14, 1, None, 0, 0)],
    [(14, 1, 0, 0, 0)],
    [(14, 1, -.001, 0, 0)],
])
def test_incomplete_conflicting_or_nonpositive_generic_factor_is_unavailable(conversion_db, rows):
    assert selected_factors(conversion_db, rows) == []


@pytest.mark.parametrize('row', [
    (42, 1, 1, 0, 0),
    (34, 1, 2, 0, 0),
    (14, 1, .001, 99, 0),
    (1, 14, 1000, 0, 0),
])
def test_mass_lookup_does_not_infer_density_reverse_or_other_cargo_factors(conversion_db, row):
    assert selected_factors(conversion_db, [row]) == []

"""Bounded, read-only diagnostics; credentials come only from Settings/.env.

Run from the project root: python -m backend.inspect_database --mode schema.
This tool never issues data changes, DDL, or permission changes. A SQL account
with SELECT-only grants remains the actual database authorization boundary.
"""
import argparse
import json
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

if __package__:
    from .database import get_db_connection
else:
    from database import get_db_connection

DATABASES = ("SmartTOS", "SmartTOS_BenThuy")
OBJECTS = (
    "TallyShift", "Cargo", "CargoGroup", "CargoDirect", "JobMethod",
    "JobMethodType", "Partner", "VesselVoyage", "Warehouse",
    "vwCargoStatistics", "vwTallyShiftFull", "vwStatisticsWarehouseInventoryByDay",
)


def encode(value):
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, bytes):
        return value.hex()
    raise TypeError(type(value).__name__)


def query(connection, statement, params=()):
    """Execute only the static SELECT statements supplied in this module."""
    cursor = connection.cursor()
    try:
        cursor.execute(statement, params)
        names = [column[0] for column in cursor.description]
        return [dict(zip(names, row)) for row in cursor.fetchall()]
    finally:
        cursor.close()


def inspect(database, mode, start, end):
    if database not in DATABASES:
        raise ValueError("Unknown database")
    connection = get_db_connection(database)
    try:
        if mode == "tables":
            return query(connection, """
                SELECT TABLE_SCHEMA, TABLE_NAME, TABLE_TYPE
                FROM INFORMATION_SCHEMA.TABLES
                WHERE TABLE_SCHEMA = 'dbo' ORDER BY TABLE_NAME
            """)
        if mode == "schema":
            placeholders = ",".join("?" for _ in OBJECTS)
            return query(connection, f"""
                SELECT TABLE_NAME, COLUMN_NAME, DATA_TYPE, IS_NULLABLE,
                       CHARACTER_MAXIMUM_LENGTH, NUMERIC_PRECISION, NUMERIC_SCALE
                FROM INFORMATION_SCHEMA.COLUMNS
                WHERE TABLE_SCHEMA = 'dbo' AND TABLE_NAME IN ({placeholders})
                ORDER BY TABLE_NAME, ORDINAL_POSITION
            """, OBJECTS)
        if mode == "views":
            return query(connection, """
                SELECT name, OBJECT_DEFINITION(object_id) AS definition
                FROM sys.views WHERE schema_id = SCHEMA_ID('dbo')
                AND name IN ('vwCargoStatistics', 'vwTallyShiftFull',
                             'vwStatisticsWarehouseInventoryByDay')
                ORDER BY name
            """)
        if mode == "methods":
            return {
                "directions": query(connection, """
                    SELECT cargoDirectId, cargoDirectCode, cargoDirectName, rowDeleted
                    FROM dbo.CargoDirect ORDER BY cargoDirectId
                """),
                "methods": query(connection, """
                    SELECT TOP (200) jobMethodId, jobMethodCode, jobMethodName,
                           jobMethodTypeId, rowDeleted
                    FROM dbo.JobMethod ORDER BY jobMethodId
                """),
                "distribution": query(connection, """
                    SELECT t.cargoDirectId, t.jobMethodId, t.rowDeleted,
                           COUNT_BIG(*) AS row_count,
                           SUM(t.weightNetSum) AS recorded_weight
                    FROM dbo.TallyShift t
                    WHERE t.shiftDate >= ? AND t.shiftDate < ?
                    GROUP BY t.cargoDirectId, t.jobMethodId, t.rowDeleted
                    ORDER BY t.cargoDirectId, t.jobMethodId
                """, (start, end + timedelta(days=1))),
            }
        if mode == "sample":
            return query(connection, """
                SELECT TOP (5) tallyShiftId, shiftDate, weightNetSum,
                       quantityTotalSum, containerTeuSum, rowDeleted
                FROM dbo.TallyShift
                WHERE shiftDate >= ? AND shiftDate < ?
                ORDER BY shiftDate DESC, tallyShiftId DESC
            """, (start, end + timedelta(days=1)))
        raise ValueError("Unknown inspection mode")
    finally:
        connection.close()


def main(default_mode="schema"):
    today = datetime.now(timezone(timedelta(hours=7))).date()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=("tables", "schema", "views", "methods", "sample", "metrics"), default=default_mode)
    parser.add_argument("--database", choices=(*DATABASES, "all"), default="all")
    parser.add_argument("--start-date", type=date.fromisoformat, default=today.replace(day=1))
    parser.add_argument("--end-date", type=date.fromisoformat, default=today)
    args = parser.parse_args()
    if not 0 <= (args.end_date - args.start_date).days < 366 or args.end_date > today:
        parser.error("Use an ordered date range of at most 366 days ending no later than today.")
    results = {"start_date": args.start_date, "end_date": args.end_date, "mode": args.mode}
    failures = 0
    for database in DATABASES if args.database == "all" else (args.database,):
        try:
            if args.mode == "metrics":
                if __package__:
                    from .repository import dashboard_repo
                else:
                    from repository import dashboard_repo
                terminal = "cua_lo" if database == "SmartTOS" else "ben_thuy"
                data = dashboard_repo.get_dashboard(args.start_date, args.end_date, terminal)
            else:
                data = inspect(database, args.mode, args.start_date, args.end_date)
            results[database] = {"status": "ok", "data": data}
        except Exception as exc:
            # Driver messages may contain server addresses or connection details.
            failures += 1
            results[database] = {"status": "error", "error_type": type(exc).__name__,
                                 "message": "Inspection failed; check configuration, network, permissions, and schema."}
    print(json.dumps(results, ensure_ascii=False, indent=2, default=encode))
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())

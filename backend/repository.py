"""One fact set and one definition for every production dashboard panel.

Throughput membership follows the source reporting catalogue. Only quantities
with an evidenced mass-unit conversion contribute to tonnes.
"""

from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from typing import Any
import logging
from time import perf_counter

if __package__:
    from .database import DatabaseQueryError, DatabaseUnavailable, get_db_connection, log_database_failure
    from .berth_scope import BERTH_RULE_VERSION, PRODUCTION_SCOPES, initial_berth_query, validate_production_scope
else:
    from database import DatabaseQueryError, DatabaseUnavailable, get_db_connection, log_database_failure
    from berth_scope import BERTH_RULE_VERSION, PRODUCTION_SCOPES, initial_berth_query, validate_production_scope

logger = logging.getLogger(__name__)
MAX_QUERY_ROWS = 250_000
VIETNAM = timezone(timedelta(hours=7))
TERMINALS = {
    "cua_lo": ("SmartTOS.dbo", "Cửa Lò"),
    "ben_thuy": ("SmartTOS_BenThuy.dbo", "Bến Thủy"),
}


class VoyageNotFound(LookupError):
    """No eligible voyage exists in the requested source or reporting scope."""


def _timestamp(value):
    if value is None:
        return None
    if isinstance(value, datetime):
        if value.year < 1900:
            return None
        return value.isoformat()
    if isinstance(value, date):
        if value.year < 1900:
            return None
        return datetime.combine(value, datetime.min.time()).isoformat()
    return str(value)


def _voyage_calendar_day(value):
    """SQL naive timestamps are Vietnam local; offset timestamps need conversion."""
    if isinstance(value, str):
        try:
            value = datetime.fromisoformat(value)
        except ValueError:
            return None
    if isinstance(value, date) and value.year < 1900:
        return None
    if isinstance(value, datetime):
        if value.tzinfo is not None:
            try:
                value = value.astimezone(VIETNAM)
            except (ValueError, OverflowError):
                return None
        value = value.date()
    return value if isinstance(value, date) and value.year >= 1900 else None


def _voyage_daily_history(daily, header, start: date, end: date):
    """Trim calendar padding to this call, retaining every recorded operation day."""
    first_operation = _voyage_calendar_day(header.get("first_operation_date")) or start
    last_operation = _voyage_calendar_day(header.get("last_operation_date")) or end
    arrival = _voyage_calendar_day(header.get("arrival_at")) or first_operation
    departure = _voyage_calendar_day(header.get("departure_at")) or end
    # Do not hide genuine source records if arrival/departure metadata disagrees
    # with operation dates. Only empty calendar padding is removed.
    first_day = max(start, min(arrival, first_operation)).isoformat()
    last_day = min(end, max(departure, last_operation)).isoformat()
    return [row for row in daily if first_day <= row["date"] <= last_day]


def vietnam_today() -> date:
    return datetime.now(VIETNAM).date()


def date_range(start_date=None, end_date=None, terminal="all") -> tuple[date, date]:
    today = vietnam_today()
    start = date.fromisoformat(start_date) if isinstance(start_date, str) else start_date
    end = date.fromisoformat(end_date) if isinstance(end_date, str) else end_date
    start = start or today.replace(day=1)
    end = end or today
    if terminal not in {"all", *TERMINALS}:
        raise ValueError("Xí nghiệp không hợp lệ.")
    if start > end:
        raise ValueError("Ngày bắt đầu phải trước hoặc bằng ngày kết thúc.")
    if end > today:
        raise ValueError("Ngày kết thúc không được nằm trong tương lai.")
    if (end - start).days >= 366:
        raise ValueError("Mỗi lần xem tối đa 366 ngày.")
    if start < date(1900, 1, 1):
        raise ValueError("Ngày bắt đầu phải từ 01/01/1900.")
    return start, end


def _decimal(value: Any) -> Decimal:
    return Decimal(str(value)) if value is not None else Decimal(0)


def _number(value: Any) -> float:
    return float(round(_decimal(value), 3))


def _trend(current: Any, previous: Any) -> float | None:
    if current is None or previous is None:
        return None
    previous = _decimal(previous)
    if previous <= 0:
        return None
    return float(round((_decimal(current) - previous) / previous * 100, 1))


def _normalise_fact(row: dict[str, Any]) -> dict[str, Any]:
    """Guard physical mass identity; catalogue rates also exist for nonmass units."""
    code = str(row.get("unit_code") or "UNKNOWN").strip().upper()
    factor = row.get("tonne_factor")
    valid_mass = code in {"TAN", "KG"} and factor is not None and _decimal(factor) > 0
    if code == "TAN" and valid_mass:
        valid_mass = _decimal(factor) == 1
    row["mass_unit_known"] = valid_mass
    native = row.get("native_weight")
    row["tonnage"] = _decimal(native) * _decimal(factor) if valid_mass and native is not None else None
    row["measured_tonnage"] = row["tonnage"]
    row["known_native_weight_count"] = int(row.get("known_weight_count") or 0)
    row["known_tonne_count"] = row["known_native_weight_count"] if valid_mass else 0
    row["eligible_tonne_count"] = int(row["record_count"]) if valid_mass else 0
    row["container_row_count"] = int(row.get("container_row_count") or 0)
    row["missing_quantity_count"] = int(row.get("missing_quantity_count") or 0)
    row["known_teu_count"] = row["container_row_count"] - row["missing_quantity_count"]
    if row["container_row_count"] and not row["known_teu_count"]:
        row["teu"] = None
    # Raw snapshots may predate these diagnostic counters. Classify a raw
    # single row only; a grouped quantity cannot identify its missing members.
    raw_missing = int(row["record_count"]) == 1 and "quantity" in row and native is None
    quantity = row.get("quantity")
    conditions = {
        "empty_unweighed_count": raw_missing and quantity is not None and _decimal(quantity) == 0,
        "unweighed_unknown_quantity_count": raw_missing and quantity is None,
        "missing_weight_with_quantity_count": raw_missing and quantity is not None and _decimal(quantity) != 0,
    }
    for field, matches in conditions.items():
        row[field] = int(row[field] or 0) if field in row else int(matches)
    return row


def _aggregate(rows):
    count = sum(int(row["record_count"]) for row in rows)
    eligible = sum(row["eligible_tonne_count"] for row in rows)
    known = sum(row["known_tonne_count"] for row in rows)
    containers = sum(row["container_row_count"] for row in rows)
    known_teu = sum(row["known_teu_count"] for row in rows)
    tonnage_status = "empty" if not count else "unavailable" if not known else "partial" if known < count else "ready"
    teu_status = "empty" if not count else "unavailable" if containers and not known_teu else "partial" if known_teu < containers else "ready"
    tonnage = None if count and not known else sum((_decimal(row["tonnage"]) for row in rows), Decimal(0))
    teu = None if containers and not known_teu else sum((_decimal(row["teu"]) for row in rows), Decimal(0))
    return {"tonnage": tonnage, "measured_tonnage": tonnage, "teu": teu,
            "record_count": count,
            "vessel_calls": len({(row["terminal_id"], row["vessel_id"]) for row in rows if row["vessel_id"] is not None}),
            "coverage": {
                "tonnage": {"status": tonnage_status, "eligible_rows": eligible, "known_rows": known,
                            "missing_weight_rows": eligible - known, "excluded_native_rows": count - eligible},
                "teu": {"status": teu_status, "container_rows": containers, "known_rows": known_teu,
                        "missing_quantity_rows": containers - known_teu},
            }}


def _panel_values(values):
    return {**{key: _number(values[key]) if values[key] is not None else None
               for key in ("tonnage", "measured_tonnage", "teu")},
            "record_count": values["record_count"],
            "tonnage_status": values["coverage"]["tonnage"]["status"],
            "teu_status": values["coverage"]["teu"]["status"]}


def _berth_evidence(row, production_scope="nghe_tinh"):
    # SQL always supplies these fields. Defaults only support old unit fixtures,
    # not stored reports (their rule version is checked by ReportingService).
    return {"initial_berth_id": row.get("initial_berth_id"),
            "initial_berth_code": row.get("initial_berth_code"),
            "initial_berth_at": _timestamp(row.get("initial_berth_at")),
            "berth_assignment_status": row.get("berth_assignment_status", "missing"),
            "production_scope": row.get("production_scope", production_scope)}


class DashboardRepository:
    production_scope_filter = "COALESCE(berth_scope.production_scope, 'unclassified') = ?"
    physical_voyage_filter = """v.vesselVoyageId > 0 AND s.vesselId IS NOT NULL
        AND ISNULL(v.rowDeleted, 0) = 0 AND ISNULL(s.rowDeleted, 0) = 0
        AND ISNULL(v.isVirtualVesselVoyage, 0) = 0 AND ISNULL(s.isVirtualVessel, 0) = 0"""
    job_method_membership_filter = """EXISTS (
        SELECT 1 FROM {schema}.StatisticsGroupType sg
        WHERE sg.statisticsGroupTypeCode = N'SANLUONG-QUACANG'
          AND ISNULL(sg.rowDeleted, 0) = 0
          AND ',' + REPLACE(ISNULL(j.statisticsGroupTypeIdList, ''), ' ', '') + ','
              LIKE '%,' + CAST(sg.statisticsGroupTypeId AS varchar(20)) + ',%'
    )"""
    throughput_filter = "t.cargoDirectId IN (1, 2) AND " + job_method_membership_filter
    row_active_filter = "ISNULL(t.rowDeleted, 0) = 0"
    teu_logic = """CASE
        WHEN c.cargoName IN ('20F', '20E', '20R') THEN ISNULL(t.quantityTotalSum, 0)
        WHEN c.cargoName IN ('40F', '40E', '40R', '45F', '45E')
            THEN ISNULL(t.quantityTotalSum, 0) * 2
        ELSE 0 END"""
    container_cargo_codes = frozenset({"20F", "20E", "20R", "40F", "40E", "40R", "45F", "45E"})
    container_codes = ", ".join(f"'{code}'" for code in sorted(container_cargo_codes))
    tonne_factor_logic = """CASE
        WHEN u.baseUnitCode = N'TAN' AND u.TONE = 1 THEN u.TONE
        WHEN u.baseUnitCode = N'KG' THEN mass_conversion.tonne_factor
        ELSE NULL END"""

    def _execute_query(self, query: str, params: tuple = ()) -> list[dict[str, Any]]:
        connection = get_db_connection()
        if connection is None:
            # Also protects integrations still returning None from the old API.
            raise DatabaseUnavailable()
        cursor = None
        started_at = perf_counter()
        phase = "cursor"
        try:
            cursor = connection.cursor()
            phase = "execute"
            cursor.execute(query, params)
            phase = "fetch"
            # SET/DECLARE/INSERT in the report's table-variable batch can
            # expose no-column results on some ODBC driver configurations.
            while cursor.description is None:
                if not cursor.nextset():
                    raise ValueError("Report query returned no result set.")
            columns = [column[0] for column in cursor.description]
            rows = []
            while True:
                batch = cursor.fetchmany(1000)
                if not batch:
                    return rows
                rows.extend(dict(zip(columns, row)) for row in batch)
                # Fail visibly instead of exhausting the API process memory.
                if len(rows) > MAX_QUERY_ROWS:
                    phase = "row_limit"
                    raise DatabaseQueryError()
        except DatabaseUnavailable as exc:
            if phase == "row_limit":
                log_database_failure(exc, phase=phase, started_at=started_at, log=logger)
            raise
        except Exception as exc:
            log_database_failure(exc, phase=phase, started_at=started_at, log=logger)
            raise DatabaseQueryError() from None
        finally:
            if cursor is not None:
                try:
                    cursor.close()
                except Exception:
                    logger.warning("Database cursor close failed.")
            try:
                connection.close()
            except Exception:
                logger.warning("Database connection close failed.")

    @staticmethod
    def _berth_columns():
        return {"initial_berth_id": "berth_scope.initial_berth_id",
                "initial_berth_code": "berth_scope.initial_berth_code",
                "initial_berth_at": "berth_scope.initial_berth_at",
                "berth_assignment_status": "COALESCE(berth_scope.berth_assignment_status, 'missing')",
                "production_scope": "COALESCE(berth_scope.production_scope, 'unclassified')"}

    @staticmethod
    def _berth_joins(schema, voyage_column="t.vesselVoyageId", *, selection="all"):
        if voyage_column not in {"t.vesselVoyageId", "v.vesselVoyageId"}:
            raise ValueError("Nguồn chuyến tàu không hợp lệ.")
        terminal = next((key for key, value in TERMINALS.items() if value[0] == schema), None)
        return f"LEFT JOIN ({initial_berth_query(schema, terminal, selection=selection)}) berth_scope ON berth_scope.vesselVoyageId = {voyage_column}"

    def _source_joins(self, schema, *, berth_selection="all", include_berth=True):
        return f"""FROM {schema}.TallyShift t
            LEFT JOIN {schema}.Cargo c ON t.cargoId = c.cargoId
            LEFT JOIN {schema}.Partner p ON t.consigneeId = p.partnerId
            LEFT JOIN {schema}.BaseUnit u ON t.weightUnitId = u.baseUnitId
            LEFT JOIN {schema}.VesselVoyage v ON t.vesselVoyageId = v.vesselVoyageId
            LEFT JOIN {schema}.Vessel s ON v.vesselId = s.vesselId
            LEFT JOIN (
                SELECT cu.baseUnitId, MIN(cu.unitValue) AS tonne_factor
                FROM {schema}.ConversionUnit cu
                JOIN {schema}.BaseUnit source_unit ON cu.baseUnitId = source_unit.baseUnitId
                JOIN {schema}.BaseUnit target_unit ON cu.unitId = target_unit.baseUnitId
                WHERE ISNULL(cu.rowDeleted, 0) = 0 AND cu.cargoId = 0
                  AND source_unit.baseUnitCode = N'KG'
                  AND target_unit.baseUnitCode = N'TAN' AND target_unit.TONE = 1
                GROUP BY cu.baseUnitId
                HAVING COUNT(DISTINCT cu.unitValue) = 1 AND MIN(cu.unitValue) > 0
            ) mass_conversion ON mass_conversion.baseUnitId = u.baseUnitId
            JOIN {schema}.JobMethod j ON t.jobMethodId = j.jobMethodId
            {self._berth_joins(schema, selection=berth_selection) if include_berth else ''}"""

    def search_voyages(self, terminal: str, search: str = "", limit: int = 30):
        """Find existing physical calls, including calls with no production yet."""
        if terminal not in TERMINALS:
            raise ValueError("Chọn một xí nghiệp hợp lệ để tìm chuyến tàu.")
        if not isinstance(search, str) or len(search) > 100:
            raise ValueError("Từ khóa tìm chuyến tàu tối đa 100 ký tự.")
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 100:
            raise ValueError("Mỗi lần tìm từ 1 đến 100 chuyến tàu.")
        schema = TERMINALS[terminal][0]
        search = search.strip()
        predicate = ""
        params = [limit]
        if search:
            # SQL Server LIKE metacharacters are literal user input, not a way
            # to widen the search. Parameters also keep quotes out of SQL text.
            pattern = search.replace("~", "~~").replace("%", "~%").replace("_", "~_").replace("[", "~[")
            identifier = int(search) if search.isascii() and search.isdigit() and len(search) <= 10 else 0
            identifier = identifier if 1 <= identifier <= 2147483647 else 0
            predicate = """AND (v.vesselVoyageId = ?
                OR v.vesselVoyageCode LIKE ? ESCAPE '~'
                OR s.vesselName LIKE ? ESCAPE '~')"""
            params.extend((identifier, f"%{pattern}%", f"%{pattern}%"))
        query = f"""SELECT TOP (?) '{terminal}' AS terminal,
            CAST(v.vesselVoyageId AS nvarchar(128)) AS voyage_id,
            v.vesselVoyageCode AS voyage_code, s.vesselName AS vessel_name,
            v.ATA AS arrival_date, v.ATD AS departure_date,
            {', '.join(f'{expression} AS {key}' for key, expression in self._berth_columns().items())}
            FROM {schema}.VesselVoyage v
            JOIN {schema}.Vessel s ON v.vesselId = s.vesselId
            {self._berth_joins(schema, 'v.vesselVoyageId')}
            WHERE {self.physical_voyage_filter} {predicate}
            ORDER BY v.vesselVoyageId DESC"""
        return [{**row, "voyage_id": str(row["voyage_id"]),
                 "arrival_date": _timestamp(row.get("arrival_date")),
                 "departure_date": _timestamp(row.get("departure_date")), **_berth_evidence(row, "unclassified")}
                for row in self._execute_query(query, tuple(params))]

    def validate_voyages(self, pairs) -> set[tuple[str, str]]:
        """Return valid terminal/ID pairs in one read for a bounded plan import.

        IDs are normalized to decimal strings. Missing/deleted/virtual calls
        are absent from the result; malformed input fails before any SQL read.
        No operation date, arrival date or existing throughput is required.
        """
        selected = defaultdict(set)
        for index, pair in enumerate(pairs):
            if index >= 500:
                raise ValueError("Mỗi lần kiểm tra tối đa 500 chuyến tàu.")
            if not isinstance(pair, (tuple, list)) or len(pair) != 2:
                raise ValueError("Định danh chuyến tàu phải gồm xí nghiệp và mã chuyến.")
            terminal, voyage_id = pair
            if not isinstance(terminal, str) or terminal not in TERMINALS:
                raise ValueError("Xí nghiệp của chuyến tàu không hợp lệ.")
            if isinstance(voyage_id, str):
                voyage_id = voyage_id.strip()
                if not voyage_id.isascii() or not voyage_id.isdigit() or len(voyage_id) > 10:
                    raise ValueError("Mã định danh chuyến tàu không hợp lệ.")
                voyage_id = int(voyage_id)
            if isinstance(voyage_id, bool) or not isinstance(voyage_id, int) or not 1 <= voyage_id <= 2147483647:
                raise ValueError("Mã định danh chuyến tàu không hợp lệ.")
            selected[terminal].add(voyage_id)
        if not selected:
            return set()
        parts, params = [], []
        for terminal, ids in selected.items():
            schema = TERMINALS[terminal][0]
            parts.append(f"""SELECT '{terminal}' AS terminal,
                CAST(v.vesselVoyageId AS nvarchar(128)) AS voyage_id
                FROM {schema}.VesselVoyage v
                JOIN {schema}.Vessel s ON v.vesselId = s.vesselId
                WHERE {self.physical_voyage_filter}
                  AND v.vesselVoyageId IN ({', '.join('?' for _ in ids)})""")
            params.extend(sorted(ids))
        rows = self._execute_query("\nUNION ALL\n".join(parts), tuple(params))
        return {(row["terminal"], str(row["voyage_id"])) for row in rows}

    def _fact_query(self, start: date, end_exclusive: date, terminal: str, voyage_id: int | None = None, *, production_scope="nghe_tinh"):
        validate_production_scope(production_scope)
        parts = []
        params = []
        selected = TERMINALS if terminal == "all" else {terminal: TERMINALS[terminal]}
        for terminal_id, (schema, name) in selected.items():
            throughput = self.throughput_filter.format(schema=schema)
            # Only identifiers from the fixed allowlist enter SQL text.
            columns = {
                "kind": "'fact'", "terminal_id": f"'{terminal_id}'", **self._berth_columns(),
                "terminal_name": f"N'{name}'", "operation_day": "CAST(t.shiftDate AS date)",
                "vessel_id": f"CASE WHEN {self.physical_voyage_filter} THEN CAST(t.vesselVoyageId AS nvarchar(128)) ELSE NULL END",
                "vessel_name": "s.vesselName", "voyage_code": "v.vesselVoyageCode",
                "arrival_at": "v.ATA", "departure_at": "v.ATD",
                "cargo_name": "COALESCE(c.cargoName, N'Chưa phân loại')",
                "direction_id": "t.cargoDirectId", "customer_id": "CAST(t.consigneeId AS nvarchar(128))",
                "customer_name": "COALESCE(NULLIF(p.partnerShortName, N''), N'Chưa xác định')",
                "native_weight": "SUM(t.weightNetSum)", "tonne_factor": self.tonne_factor_logic,
                "unit_code": "COALESCE(u.baseUnitCode, N'UNKNOWN')", "unit_name": "COALESCE(u.baseUnitName, N'Chưa xác định đơn vị')",
                "teu": f"SUM({self.teu_logic})", "record_count": "COUNT_BIG(*)",
                "latest_operation_at": "MAX(t.shiftDate)",
                "known_weight_count": "SUM(CASE WHEN t.weightNetSum IS NOT NULL THEN 1 ELSE 0 END)",
                "missing_weight_count": "SUM(CASE WHEN t.weightNetSum IS NULL THEN 1 ELSE 0 END)",
                "container_row_count": f"SUM(CASE WHEN c.cargoName IN ({self.container_codes}) THEN 1 ELSE 0 END)",
                "missing_quantity_count": f"SUM(CASE WHEN c.cargoName IN ({self.container_codes}) AND t.quantityTotalSum IS NULL THEN 1 ELSE 0 END)",
                "negative_value_count": "SUM(CASE WHEN t.weightNetSum < 0 OR t.quantityTotalSum < 0 THEN 1 ELSE 0 END)",
                "empty_unweighed_count": "SUM(CASE WHEN t.weightNetSum IS NULL AND t.quantityTotalSum = 0 THEN 1 ELSE 0 END)",
                "unweighed_unknown_quantity_count": "SUM(CASE WHEN t.weightNetSum IS NULL AND t.quantityTotalSum IS NULL THEN 1 ELSE 0 END)",
                "missing_weight_with_quantity_count": "SUM(CASE WHEN t.weightNetSum IS NULL AND t.quantityTotalSum <> 0 THEN 1 ELSE 0 END)",
            }
            source_columns = {key: (value if key in {"terminal_id", "terminal_name"} else "'source'" if key == "kind" else "MAX(t.shiftDate)" if key == "latest_operation_at" else "NULL") for key, value in columns.items()}
            fact_select = ",\n".join(f"{value} AS {key}" for key, value in columns.items())
            source_select = ",\n".join(f"{value} AS {key}" for key, value in source_columns.items())
            parts.append(f"""
                SELECT {fact_select}
                {self._source_joins(schema, berth_selection='voyage' if voyage_id is not None else 'all')}
                WHERE t.shiftDate >= ? AND t.shiftDate < ?
                    AND {throughput} AND {self.row_active_filter} AND {self.production_scope_filter}
                    {"AND t.vesselVoyageId = ?" if voyage_id is not None else ""}
                GROUP BY CAST(t.shiftDate AS date), t.vesselVoyageId, c.cargoName,
                    t.cargoDirectId, t.consigneeId, p.partnerShortName,
                    u.baseUnitCode, u.baseUnitName, u.TONE, u.KG, mass_conversion.tonne_factor,
                    v.vesselVoyageId, v.rowDeleted, v.isVirtualVesselVoyage,
                    s.vesselId, s.rowDeleted, s.isVirtualVessel,
                    s.vesselName, v.vesselVoyageCode, v.ATA, v.ATD,
                    berth_scope.initial_berth_id, berth_scope.initial_berth_code,
                    berth_scope.initial_berth_at, berth_scope.berth_assignment_status, berth_scope.production_scope
                UNION ALL
                SELECT {source_select}
                FROM {schema}.TallyShift t
                JOIN {schema}.JobMethod j ON t.jobMethodId = j.jobMethodId
                WHERE {throughput} AND {self.row_active_filter}
            """)
            if voyage_id is not None:
                params.append(voyage_id)
            params.extend((start, end_exclusive, production_scope))
            if voyage_id is not None:
                params.append(voyage_id)
        # Scope/date selectivity varies greatly. Compile this statement for its
        # actual parameters instead of reusing an unsuitable report plan.
        return "\nUNION ALL\n".join(parts) + "\nOPTION (RECOMPILE)", tuple(params)

    def get_dashboard(self, start_date=None, end_date=None, terminal="all", *, voyage_id: int | None = None, production_scope="nghe_tinh") -> dict[str, Any]:
        start, end = date_range(start_date, end_date, terminal)
        length = (end - start).days + 1
        previous_start = start - timedelta(days=length)
        previous_end = start - timedelta(days=1)
        query, params = self._fact_query(previous_start, end + timedelta(days=1), terminal, voyage_id, production_scope=production_scope)
        fetched = self._execute_query(query, params)
        return self._dashboard_from_rows(fetched, start, end, terminal, production_scope=production_scope)

    def _report_query(self, start: date, end_exclusive: date, terminal: str, *, production_scope="nghe_tinh"):
        """Read facts and one-row voyage assignments without a correlated join.

        Materialize the small eligible-method catalogue once per source. The
        following UNION still reads all raw facts and voyage assignments in one
        statement, before scope attribution and report aggregation. Table
        variables are request-local; no source table is modified.
        """
        validate_production_scope(production_scope)
        parts, params, method_tables = [], [], []
        selected = TERMINALS if terminal == "all" else {terminal: TERMINALS[terminal]}
        for terminal_id, (schema, name) in selected.items():
            method_table = f"@eligible_{terminal_id}"
            method_tables.append(f"""DECLARE {method_table} TABLE(jobMethodId int NOT NULL PRIMARY KEY);
                INSERT INTO {method_table}(jobMethodId)
                SELECT DISTINCT j.jobMethodId FROM {schema}.JobMethod j
                WHERE {self.job_method_membership_filter.format(schema=schema)};""")
            columns = {
                "kind": "'fact'", "id": "CAST(t.tallyShiftId AS nvarchar(128))",
                **{key: "NULL" for key in self._berth_columns()},
                "operation_code": "t.tallyShiftCode", "operation_day": "CAST(t.shiftDate AS date)",
                "terminal_id": f"'{terminal_id}'", "terminal_name": f"N'{name}'",
                "vessel_id": f"CASE WHEN {self.physical_voyage_filter} THEN CAST(t.vesselVoyageId AS nvarchar(128)) ELSE NULL END",
                "source_voyage_id": "CAST(t.vesselVoyageId AS nvarchar(128))",
                "vessel_name": "s.vesselName", "voyage_code": "v.vesselVoyageCode",
                "arrival_at": "v.ATA", "departure_at": "v.ATD",
                "shift_id": "CAST(t.shiftId AS nvarchar(128))", "shift_code": "sh.shiftCode",
                "customer_id": "CAST(t.consigneeId AS nvarchar(128))",
                "customer_name": "COALESCE(NULLIF(p.partnerShortName, N''), N'Chưa xác định')",
                "latest_operation_at": "t.shiftDate",
                "job_method": "j.jobMethodName", "job_method_code": "j.jobMethodCode",
                "cargo_name": "COALESCE(c.cargoName, N'Chưa phân loại')", "direction_id": "t.cargoDirectId",
                "quantity": "t.quantityTotalSum", "quantity_unit": "qu.baseUnitCode",
                "quantity_unit_name": "qu.baseUnitName", "native_weight": "t.weightNetSum",
                "unit_code": "COALESCE(u.baseUnitCode, N'UNKNOWN')",
                "unit_name": "COALESCE(u.baseUnitName, N'Chưa xác định đơn vị')",
                "tonne_factor": self.tonne_factor_logic, "teu": self.teu_logic,
                "record_count": "1",
                "known_weight_count": "CASE WHEN t.weightNetSum IS NOT NULL THEN 1 ELSE 0 END",
                "missing_weight_count": "CASE WHEN t.weightNetSum IS NULL THEN 1 ELSE 0 END",
                "container_row_count": f"CASE WHEN c.cargoName IN ({self.container_codes}) THEN 1 ELSE 0 END",
                "missing_quantity_count": f"CASE WHEN c.cargoName IN ({self.container_codes}) AND t.quantityTotalSum IS NULL THEN 1 ELSE 0 END",
                "negative_value_count": "CASE WHEN t.weightNetSum < 0 OR t.quantityTotalSum < 0 THEN 1 ELSE 0 END",
                "empty_unweighed_count": "CASE WHEN t.weightNetSum IS NULL AND t.quantityTotalSum = 0 THEN 1 ELSE 0 END",
                "unweighed_unknown_quantity_count": "CASE WHEN t.weightNetSum IS NULL AND t.quantityTotalSum IS NULL THEN 1 ELSE 0 END",
                "missing_weight_with_quantity_count": "CASE WHEN t.weightNetSum IS NULL AND t.quantityTotalSum <> 0 THEN 1 ELSE 0 END",
            }
            source_columns = {
                key: (value if key in {"terminal_id", "terminal_name"}
                      else "'source'" if key == "kind"
                      else "MAX(t.shiftDate)" if key == "latest_operation_at" else "NULL")
                for key, value in columns.items()
            }
            fact_select = ",\n".join(f"{value} AS {key}" for key, value in columns.items())
            source_select = ",\n".join(f"{value} AS {key}" for key, value in source_columns.items())
            berth_values = {"kind": "'berth'", "terminal_id": f"'{terminal_id}'",
                            "source_voyage_id": "CAST(berth_scope.vesselVoyageId AS nvarchar(128))",
                            **self._berth_columns()}
            berth_select = ",\n".join(f"{berth_values.get(key, 'NULL')} AS {key}" for key in columns)
            throughput = f"""t.cargoDirectId IN (1, 2) AND EXISTS (
                SELECT 1 FROM {method_table} em WHERE em.jobMethodId = j.jobMethodId)"""
            parts.append(f"""SELECT {fact_select}
                {self._source_joins(schema, include_berth=False)}
                LEFT JOIN {schema}.BaseUnit qu ON t.quantityUnitId = qu.baseUnitId
                LEFT JOIN {schema}.Shift sh ON t.shiftId = sh.shiftId
                WHERE t.shiftDate >= ? AND t.shiftDate < ?
                  AND {throughput} AND {self.row_active_filter}
                UNION ALL SELECT {source_select}
                FROM {schema}.TallyShift t
                JOIN {schema}.JobMethod j ON t.jobMethodId = j.jobMethodId
                WHERE {throughput} AND {self.row_active_filter}
                UNION ALL SELECT {berth_select}
                FROM ({initial_berth_query(schema, terminal_id)}) berth_scope""")
            params.extend((start, end_exclusive))
        query = "SET NOCOUNT ON;\n" + "\n".join(method_tables) + "\n" + "\nUNION ALL\n".join(parts)
        return query + "\nOPTION (RECOMPILE)", tuple(params)

    @staticmethod
    def _scope_report_rows(fetched, production_scope):
        """Join by source identity once; never multiply facts or infer a berth."""
        validate_production_scope(production_scope)
        assignments = {}
        for row in fetched:
            if row["kind"] != "berth":
                continue
            if row["source_voyage_id"] is None:
                # Match SQL equality semantics: NULL identities never join.
                continue
            key = (row["terminal_id"], row["source_voyage_id"])
            if key in assignments:
                # The classifier GROUP BY guarantees uniqueness. Fail closed
                # if a future source/query change violates that invariant.
                raise DatabaseQueryError()
            assignments[key] = _berth_evidence(row, "unclassified")
        selected = []
        for row in fetched:
            if row["kind"] == "source":
                selected.append(row)
            elif row["kind"] == "fact":
                evidence = assignments.get((row["terminal_id"], row.get("source_voyage_id")),
                                           _berth_evidence({}, "unclassified"))
                if evidence["production_scope"] == production_scope:
                    selected.append({**row, **evidence})
        return selected

    def read_report(self, start_date=None, end_date=None, terminal="all", *, production_scope="nghe_tinh"):
        """Read current/prior facts once; preserve unassigned throughput rows."""
        start, end = date_range(start_date, end_date, terminal)
        previous_start = start - timedelta(days=(end - start).days + 1)
        query, params = self._report_query(previous_start, end + timedelta(days=1), terminal, production_scope=production_scope)
        fetched = self._scope_report_rows(self._execute_query(query, params), production_scope)
        report = self._dashboard_from_rows(fetched, start, end, terminal, production_scope=production_scope)
        current = [row for row in fetched if row["kind"] == "fact" and start <= row["operation_day"] <= end]
        return {"report": report, "rows": current}

    def _dashboard_from_rows(self, fetched, start: date, end: date, terminal: str, *, production_scope="nghe_tinh"):
        validate_production_scope(production_scope)
        length = (end - start).days + 1
        previous_start = start - timedelta(days=length)
        previous_end = start - timedelta(days=1)
        current, previous, sources = [], [], []
        for row in fetched:
            if row["kind"] == "source":
                sources.append(row)
                continue
            operation_day = row["operation_day"]
            if isinstance(operation_day, datetime):
                operation_day = operation_day.date()
            elif isinstance(operation_day, str):
                operation_day = date.fromisoformat(operation_day[:10])
            row["operation_day"] = operation_day
            (current if operation_day >= start else previous).append(_normalise_fact(row))

        def totals(rows):
            values = _aggregate(rows)
            return {"total_tonnage": values["tonnage"], "total_measured_tonnage": values["tonnage"],
                    "total_teu": values["teu"], "vessel_calls": values["vessel_calls"], "record_count": values["record_count"]}

        raw_overview, prev = totals(current), totals(previous)
        metric_coverage = _aggregate(current)["coverage"]
        previous_coverage = _aggregate(previous)["coverage"]
        def comparable(metric):
            return all(coverage[metric]["status"] in {"ready", "empty"} for coverage in (metric_coverage, previous_coverage))
        overview = {key: (_number(value) if isinstance(value, Decimal) else value) for key, value in raw_overview.items()}
        overview.update({
            "tonnage_status": metric_coverage["tonnage"]["status"],
            "teu_status": metric_coverage["teu"]["status"],
            "period": f"{start.isoformat()} - {end.isoformat()}",
            "trend_tonnage": _trend(raw_overview["total_tonnage"], prev["total_tonnage"]) if comparable("tonnage") else None,
            "trend_teu": _trend(raw_overview["total_teu"], prev["total_teu"]) if comparable("teu") else None,
            "trend_vessels": _trend(raw_overview["vessel_calls"], prev["vessel_calls"]),
            "trend_measured_tonnage": _trend(raw_overview["total_measured_tonnage"], prev["total_measured_tonnage"]) if comparable("tonnage") else None,
        })

        def grouped(key_function):
            result = defaultdict(list)
            for row in current:
                result[key_function(row)].append(row)
            return {key: _aggregate(rows) for key, rows in result.items()}

        def breakdown(key_function):
            groups = grouped(key_function)
            total = raw_overview["total_tonnage"]
            return [{
                "name": name, **_panel_values(values),
                "value": None if values["tonnage"] is None else float(round(values["tonnage"] / total * 100, 1)) if total is not None and total > 0 else 0,
            } for name, values in sorted(groups.items(), key=lambda item: (-_decimal(item[1]["tonnage"]), item[0]))]

        cargo = breakdown(lambda row: "Hàng container"
                          if str(row["cargo_name"]).strip().upper() in self.container_cargo_codes
                          else row["cargo_name"])
        directions = breakdown(lambda row: "Hàng xếp" if int(row["direction_id"]) == 1 else "Hàng dỡ")
        terminals = [{"name": name, **_panel_values(values)}
                     for name, values in grouped(lambda row: row["terminal_name"]).items()]
        history_groups = grouped(lambda row: row["operation_day"].strftime("%Y-%m"))
        # Include zero months inside the actual filter, never extend to GETDATE().
        month = start.replace(day=1)
        history = []
        while month <= end:
            label = month.strftime("%Y-%m")
            values = history_groups.get(label, _aggregate([]))
            history.append({"date": label, **_panel_values(values)})
            month = (month.replace(day=28) + timedelta(days=4)).replace(day=1)
        daily_groups = grouped(lambda row: row["operation_day"])
        daily_history = []
        day = start
        while day <= end:
            values = daily_groups.get(day, _aggregate([]))
            daily_history.append({"date": day.isoformat(), **_panel_values(values)})
            day += timedelta(days=1)
        customer_key = lambda row: (row["terminal_id"], row["customer_id"])
        customer_groups = grouped(customer_key)
        customer_labels = {customer_key(row): row for row in current}
        ranked_customers = sorted(customer_groups.items(), key=lambda item: (
            -_decimal(item[1]["tonnage"]), item[0][0], item[0][1] or ""
        ))[:5]
        customers = [{
            "name": customer_labels[key]["customer_name"],
            "customer_id": key[1], "terminal_id": key[0],
            "terminal_name": customer_labels[key]["terminal_name"],
            "volume": _number(values["tonnage"]) if values["tonnage"] is not None else None, "type": "Tấn",
            "tonnage_status": values["coverage"]["tonnage"]["status"],
            "measured_tonnage": _number(values["tonnage"]) if values["tonnage"] is not None else None,
        } for key, values in ranked_customers]

        voyage_rows = defaultdict(list)
        for row in current:
            if row["vessel_id"] is not None:
                voyage_rows[(row["terminal_id"], str(row["vessel_id"]))].append(row)
        voyages = []
        for (terminal_id, selected_voyage_id), rows in voyage_rows.items():
            label = rows[0]
            voyages.append({
                "terminal_id": terminal_id, "terminal_name": label["terminal_name"],
                "voyage_id": selected_voyage_id, "vessel_name": label.get("vessel_name"),
                "voyage_code": label.get("voyage_code"),
                **_berth_evidence(label, production_scope),
                "arrival_at": _timestamp(label.get("arrival_at")),
                "departure_at": _timestamp(label.get("departure_at")),
                "first_operation_date": min(row["operation_day"] for row in rows).isoformat(),
                "last_operation_date": max(row["operation_day"] for row in rows).isoformat(),
                "cargo_names": sorted({row["cargo_name"] for row in rows}),
                **_panel_values(_aggregate(rows)),
            })
        voyages.sort(key=lambda row: (row["last_operation_date"], row["terminal_id"], row["voyage_id"]), reverse=True)

        native_groups = defaultdict(list)
        for row in current:
            if not row["mass_unit_known"]:
                native_groups[(row["terminal_id"], row["unit_code"], row["unit_name"])].append(row)
        native_units = []
        for (terminal_id, unit_code, unit_name), rows in sorted(native_groups.items()):
            known = sum(row["known_native_weight_count"] for row in rows)
            count = sum(int(row["record_count"]) for row in rows)
            native_units.append({"terminal_id": terminal_id, "terminal_name": rows[0]["terminal_name"],
                                 "unit_code": unit_code, "unit_name": unit_name,
                                 "value": _number(sum((_decimal(row["native_weight"]) for row in rows), Decimal(0))) if known else None,
                                 "record_count": count, "known_value_rows": known,
                                 "status": "unavailable" if not known else "partial" if known < count else "ready"})

        selected_terminals = TERMINALS if terminal == "all" else {terminal: TERMINALS[terminal]}
        source_by_id = {row["terminal_id"]: row for row in sources}
        source_meta = []
        for terminal_id, (_, name) in selected_terminals.items():
            selected_rows = [row for row in current if row["terminal_id"] == terminal_id]
            latest = source_by_id.get(terminal_id, {}).get("latest_operation_at")
            latest_text = _timestamp(latest)
            latest_day = date.fromisoformat(latest_text[:10]) if latest_text else None
            source_meta.append({
                "id": terminal_id, "name": name,
                "latest_operation_at": latest_text,
                "latest_selected_operation_at": max((_timestamp(row["latest_operation_at"]) for row in selected_rows), default=None),
                "days_since_latest_operation": (vietnam_today() - latest_day).days if latest_day else None,
                "record_count": sum(int(row["record_count"]) for row in selected_rows),
            })
        unavailable = {
            "efficiency": "Chưa xác minh cách tính thời gian làm hàng, ca trùng và đơn vị năng suất.",
            "yard": "Chưa xác minh tồn kho theo từng bãi, thời điểm chốt và đơn vị sức chứa.",
        }
        warnings = [
            "Phạm vi qua cảng theo danh mục SANLUONG-QUACANG đã khớp tổng API nguồn ở kỳ đối soát; vẫn cần ký xác nhận với báo cáo sản xuất đã duyệt.",
            "Tấn chỉ cộng khối lượng có đơn vị khối lượng xác định; TEU cộng theo quy tắc kích cỡ container đã đối chiếu. Các số thiếu không được thay bằng dữ liệu giả.",
        ]
        missing_weight = sum(int(row.get("missing_weight_count") or 0) for row in current)
        missing_quantity = sum(int(row.get("missing_quantity_count") or 0) for row in current)
        negative_values = sum(int(row.get("negative_value_count") or 0) for row in current)
        if missing_weight:
            warnings.append(f"Có {missing_weight} dòng thiếu weightNetSum; tổng khối lượng ghi nhận chỉ cộng giá trị có sẵn.")
        if missing_quantity:
            warnings.append(f"Có {missing_quantity} dòng container thiếu quantityTotalSum; TEU có thể bị thiếu.")
        if negative_values:
            warnings.append(f"Có {negative_values} dòng có khối lượng hoặc số lượng âm; cần đối soát điều chỉnh, hiện vẫn giữ trong tổng.")
        if native_units:
            warnings.append(f"Có {metric_coverage['tonnage']['excluded_native_rows']} dòng chưa có đơn vị khối lượng được xác nhận; trình bày riêng theo đơn vị nguồn, không cộng vào tấn.")
        if end == vietnam_today():
            warnings.append("Kỳ đang gồm ngày hiện tại chưa chốt; số liệu trong ngày có thể tiếp tục thay đổi.")
        return {
            "overview": overview, "cargo": cargo, "history": history, "daily_history": daily_history,
            "terminals": terminals, "directions": directions, "customers": customers, "native_units": native_units, "voyages": voyages,
            "efficiency": {"status": "unavailable", "turnaround_time": None, "productivity": None, "reason": unavailable["efficiency"]},
            "yard": [],
            "meta": {
                "status": "ok" if current else "empty",
                "generated_at": datetime.now(timezone.utc).isoformat(),
                "filters": {"start_date": start.isoformat(), "end_date": end.isoformat(), "terminal": terminal, "timezone": "Asia/Ho_Chi_Minh", "production_scope": production_scope},
                "berth_rule_version": BERTH_RULE_VERSION,
                "previous_period": {"start_date": previous_start.isoformat(), "end_date": previous_end.isoformat(), "label": f"{length} ngày liền trước", "record_count": prev["record_count"], "metric_coverage": previous_coverage},
                "sources": source_meta,
                "metric_coverage": metric_coverage,
                "unassigned_voyage_totals": _panel_values(_aggregate([row for row in current if row["vessel_id"] is None])),
                "incomplete_period": end == vietnam_today(),
                "definitions": {
                    "tonnage": "Tổng weightNetSum của dòng qua cảng có BaseUnit TAN; KG chỉ đổi theo hệ số chung KG→TAN duy nhất còn hiệu lực trong ConversionUnit. M2, M3, GÀU, LẦN, GIỜ và đơn vị chưa xác định được giữ riêng. Không nhân hệ số container 25/30/3,88. Đây là trọng lượng ghi nhận, không khẳng định cân thực tế hay đã loại vỏ container.",
                    "measured_tonnage": "Trường tương thích, cùng giá trị tổng trọng lượng ghi nhận đã chuẩn hóa về tấn; không phải KPI khối lượng thứ hai.",
                    "teu": "20F/20E/20R ×1; 40F/40E/40R/45F/45E ×2 theo quantityTotalSum. Các mã khác chưa có quy tắc quy đổi.",
                    "throughput": "TallyShift đang hoạt động (rowDeleted NULL hoặc 0), cargoDirectId 1 hoặc 2, JobMethod thuộc StatisticsGroupType có mã SANLUONG-QUACANG bằng quan hệ ID đầy đủ trong statisticsGroupTypeIdList. Không tìm theo từ khóa tên phương án.",
                    "production_scope": "Phân loại toàn chuyến theo cầu cập thực tế đầu tiên, ưu tiên ATB rồi ATA. Cầu 5 (berthId 13) tại Cửa Lò thuộc Cầu 5 – Vietsun; cầu khác thuộc Nghệ Tĩnh. Thiếu hoặc mơ hồ về cầu đầu được giữ riêng ở Chưa xác định cầu. Chuyển cầu sau không đổi phân loại.",
                    "vessel_calls": "Số cặp xí nghiệp + vesselVoyageId dương có chuyến và tàu vật lý còn hợp lệ, không ảo/đã xóa, phát sinh dòng qua cảng trong kỳ. Dòng không đủ điều kiện đếm chuyến vẫn giữ trong khối lượng. Không đồng nghĩa số tàu cập cảng.",
                    "voyages": "Danh sách gồm đúng các chuyến được đếm trong KPI. Khối lượng của mỗi chuyến giới hạn theo kỳ lọc; ATA/ATD là thời điểm thực tế ghi tại VesselVoyage, không thay bằng ETA/ETD. Tổng khối lượng công ty còn có thể gồm dòng chưa gắn chuyến hợp lệ.",
                    "cargo": "Cơ cấu hàng hóa gộp các mã 20F/20E/20R/40F/40E/40R/45F/45E thành Hàng container; hàng khác giữ Cargo.cargoName, dòng thiếu danh mục giữ nhãn Chưa phân loại. Phiếu tác nghiệp và danh sách hàng của từng chuyến vẫn giữ mã hàng gốc. Việc gộp nhóm không thay đổi tấn, TEU hoặc tổng sản lượng.",
                    "customers": "Top 5 theo cặp xí nghiệp + consigneeId, hiển thị partnerShortName; giữ tách khách trùng tên. Khách thiếu consigneeId gộp vào Chưa xác định trong từng xí nghiệp. Chưa hợp nhất định danh giữa các nguồn; top 5 không đại diện toàn bộ tổng.",
                    "history": "Tổng theo tháng nằm trong khoảng ngày đã chọn; tháng đầu/cuối có thể chưa đủ tháng.",
                    "daily_history": "Tổng từng ngày trong khoảng đã chọn từ cùng tập dòng; ngày không có dòng trả 0, không tự kết luận mất dữ liệu hay ngừng sản xuất.",
                    "sources": "latest_operation_at là shiftDate mới nhất của dòng thông qua tại toàn nguồn xí nghiệp, không giới hạn kỳ hoặc phạm vi Nghệ Tĩnh/Vietsun; không phải thời điểm đồng bộ hay sửa dữ liệu. latest_selected_operation_at và record_count chỉ phản ánh kỳ và phạm vi sản lượng đã chọn.",
                    "comparison": "So sánh phần trăm với khoảng liền trước có cùng số ngày; không tính khi mẫu số bằng 0, không có dữ liệu, hoặc dữ liệu tấn/TEU của một trong hai kỳ chưa đầy đủ.",
                    "native_units": "Tổng weightNetSum theo weightUnitId ở từng xí nghiệp; giữ nguyên đơn vị nguồn. Không cộng chung giữa các đơn vị và không diễn giải là tấn.",
                    "consistency": "Các biểu đồ được tổng hợp từ cùng một tập dữ liệu của một câu lệnh SQL; chưa bật snapshot isolation trên nguồn.",
                },
                "warnings": warnings, "unavailable": unavailable,
                "data_quality": {
                    "missing_weight_count": missing_weight,
                    **{field: sum(row[field] for row in current) for field in (
                        "empty_unweighed_count", "unweighed_unknown_quantity_count",
                        "missing_weight_with_quantity_count",
                    )},
                    "missing_quantity_count": missing_quantity,
                    "negative_value_count": negative_values,
                    "days_with_records": len({row["operation_day"] for row in current}),
                    "days_without_records": length - len({row["operation_day"] for row in current}),
                    "note": "Ngày không có dòng theo bộ lọc có thể là ngày không sản xuất; không tự kết luận mất dữ liệu.",
                },
                "business_rules_verified": False,
                "reporting_catalog_reconciled": True,
            },
        }

    def _operation_query(self, start: date, end: date, terminal: str, voyage_id: int, *, production_scope="nghe_tinh"):
        validate_production_scope(production_scope)
        schema, name = TERMINALS[terminal]
        query = f"""SELECT 'fact' AS kind, CAST(t.tallyShiftId AS nvarchar(128)) AS id,
            t.tallyShiftCode AS operation_code, CAST(t.shiftDate AS date) AS operation_day,
            CAST(t.shiftId AS nvarchar(128)) AS shift_id, sh.shiftCode AS shift_code,
            '{terminal}' AS terminal_id, N'{name}' AS terminal_name,
            CAST(t.vesselVoyageId AS nvarchar(128)) AS vessel_id,
            s.vesselName AS vessel_name, v.vesselVoyageCode AS voyage_code,
            v.ATA AS arrival_at, v.ATD AS departure_at,
            {', '.join(f'{expression} AS {key}' for key, expression in self._berth_columns().items())},
            CAST(t.consigneeId AS nvarchar(128)) AS customer_id,
            COALESCE(NULLIF(p.partnerShortName, N''), N'Chưa xác định') AS customer_name,
            t.shiftDate AS latest_operation_at,
            j.jobMethodName AS job_method, j.jobMethodCode AS job_method_code,
            COALESCE(c.cargoName, N'Chưa phân loại') AS cargo_name,
            t.cargoDirectId AS direction_id,
            t.quantityTotalSum AS quantity, qu.baseUnitCode AS quantity_unit,
            qu.baseUnitName AS quantity_unit_name,
            t.weightNetSum AS native_weight, COALESCE(u.baseUnitCode, N'UNKNOWN') AS unit_code,
            COALESCE(u.baseUnitName, N'Chưa xác định đơn vị') AS unit_name,
            {self.tonne_factor_logic} AS tonne_factor, {self.teu_logic} AS teu,
            1 AS record_count,
            CASE WHEN t.weightNetSum IS NOT NULL THEN 1 ELSE 0 END AS known_weight_count,
            CASE WHEN t.weightNetSum IS NULL THEN 1 ELSE 0 END AS missing_weight_count,
            CASE WHEN c.cargoName IN ({self.container_codes}) THEN 1 ELSE 0 END AS container_row_count,
            CASE WHEN c.cargoName IN ({self.container_codes}) AND t.quantityTotalSum IS NULL THEN 1 ELSE 0 END AS missing_quantity_count,
            CASE WHEN t.weightNetSum < 0 OR t.quantityTotalSum < 0 THEN 1 ELSE 0 END AS negative_value_count,
            CASE WHEN t.weightNetSum IS NULL AND t.quantityTotalSum = 0 THEN 1 ELSE 0 END AS empty_unweighed_count,
            CASE WHEN t.weightNetSum IS NULL AND t.quantityTotalSum IS NULL THEN 1 ELSE 0 END AS unweighed_unknown_quantity_count,
            CASE WHEN t.weightNetSum IS NULL AND t.quantityTotalSum <> 0 THEN 1 ELSE 0 END AS missing_weight_with_quantity_count
            {self._source_joins(schema, berth_selection='voyage')}
            LEFT JOIN {schema}.BaseUnit qu ON t.quantityUnitId = qu.baseUnitId
            LEFT JOIN {schema}.Shift sh ON t.shiftId = sh.shiftId
            WHERE t.shiftDate >= ? AND t.shiftDate < ? AND t.vesselVoyageId = ?
              AND {self.throughput_filter.format(schema=schema)} AND {self.row_active_filter}
              AND {self.physical_voyage_filter}
              AND {self.production_scope_filter}
            ORDER BY t.shiftDate DESC, t.tallyShiftId DESC
            OPTION (RECOMPILE)"""
        return query, (voyage_id, start, end + timedelta(days=1), voyage_id, production_scope)

    def get_voyage_detail(self, terminal: str, voyage_id: int, start_date=None, end_date=None, page=1, page_size=25, operation_filter="all", *, production_scope="nghe_tinh"):
        if terminal not in TERMINALS:
            raise ValueError("Chọn một xí nghiệp hợp lệ để xem chuyến tàu.")
        if isinstance(voyage_id, bool) or not isinstance(voyage_id, int) or not 1 <= voyage_id <= 2147483647:
            raise ValueError("Mã định danh chuyến tàu không hợp lệ.")
        if isinstance(page, bool) or not isinstance(page, int) or page < 1:
            raise ValueError("Số trang phải là số nguyên dương.")
        if isinstance(page_size, bool) or not isinstance(page_size, int) or not 1 <= page_size <= 100:
            raise ValueError("Mỗi trang từ 1 đến 100 phiếu.")
        if not isinstance(operation_filter, str) or operation_filter not in {"all", "with_values", "missing_weight"}:
            raise ValueError("Bộ lọc phiếu tác nghiệp không hợp lệ.")
        start, end = date_range(start_date, end_date, terminal)
        # Summary, charts and the requested page share this one read. Pagination
        # is applied to the bounded voyage set after aggregation, so same-count
        # source edits cannot make a response combine two different row sets.
        query, params = self._operation_query(start, end, terminal, voyage_id, production_scope=production_scope)
        raw_operations = self._execute_query(query, params)
        dashboard = self._dashboard_from_rows(raw_operations, start, end, terminal, production_scope=production_scope)
        header = next((row for row in dashboard["voyages"] if row["terminal_id"] == terminal and row["voyage_id"] == str(voyage_id)), None)
        if header is None:
            raise VoyageNotFound("Không có chuyến tàu hợp lệ phát sinh sản lượng trong phạm vi đã chọn.")
        # Filter only the table, after every total has been computed from the
        # complete source set. Native nonzero values include corrections and
        # nonmass units; approval flags and converted tonnes/TEU are not filters.
        with_values = [row for row in raw_operations if any(
            value is not None and _decimal(value) != 0
            for value in (row.get("native_weight"), row.get("quantity"))
        )]
        missing_weight = [row for row in raw_operations if row.get("native_weight") is None]
        groups = {"all": raw_operations, "with_values": with_values, "missing_weight": missing_weight}
        counts = {name: len(rows) for name, rows in groups.items()}
        selected_operations = groups[operation_filter]
        total = len(selected_operations)
        total_pages = (total + page_size - 1) // page_size
        if page > max(1, total_pages):
            raise ValueError("Trang phiếu tác nghiệp nằm ngoài phạm vi.")
        operations = []
        offset = (page - 1) * page_size
        for row in selected_operations[offset:offset + page_size]:
            values = _panel_values(_aggregate([row]))
            operation_day = row["operation_day"]
            operations.append({
                "id": str(row["id"]), "operation_code": row.get("operation_code"),
                "shift_id": str(row["shift_id"]) if row.get("shift_id") is not None else None,
                "shift_code": row.get("shift_code"),
                "operation_date": operation_day.isoformat()[:10] if isinstance(operation_day, (date, datetime)) else str(operation_day)[:10],
                "job_method": row.get("job_method"), "job_method_code": row.get("job_method_code"),
                "cargo_name": row["cargo_name"],
                "direction": "Hàng xếp" if int(row["direction_id"]) == 1 else "Hàng dỡ",
                "quantity": _number(row["quantity"]) if row.get("quantity") is not None else None,
                "quantity_unit": row.get("quantity_unit"), "quantity_unit_name": row.get("quantity_unit_name"),
                "weight": _number(row["native_weight"]) if row.get("native_weight") is not None else None,
                "weight_unit": row["unit_code"], "weight_unit_name": row["unit_name"],
                "tonnage": values["tonnage"], "teu": values["teu"],
                "tonnage_status": values["tonnage_status"], "teu_status": values["teu_status"],
                **_berth_evidence(row, production_scope),
            })
        summary = {key: header[key] for key in ("tonnage", "teu", "record_count", "tonnage_status", "teu_status")}
        return {"header": header, "summary": summary, "cargo": dashboard["cargo"],
                "daily": _voyage_daily_history(dashboard["daily_history"], header, start, end),
                "native_units": dashboard["native_units"],
                "operations": {"page": page, "page_size": page_size, "total": total, "total_pages": total_pages, "rows": operations,
                               "filter": operation_filter, "total_all": counts["all"], "counts": counts},
                "meta": {"filters": {"terminal": terminal, "voyage_id": str(voyage_id), "start_date": start.isoformat(), "end_date": end.isoformat(), "timezone": "Asia/Ho_Chi_Minh", "operation_filter": operation_filter, "production_scope": production_scope},
                         "berth_rule_version": BERTH_RULE_VERSION,
                         "generated_at": dashboard["meta"]["generated_at"], "metric_coverage": dashboard["meta"]["metric_coverage"],
                         "operations_order": "shiftDate DESC, tallyShiftId DESC",
                          "read_consistency": "single_fact_set"}}

    def read_voyage_lifetime(self, terminal: str, voyage_id: int, *, production_scope="nghe_tinh"):
        """Whole-call progress, separate from the selected reporting period."""
        if terminal not in TERMINALS:
            raise ValueError("Chọn một xí nghiệp hợp lệ.")
        if isinstance(voyage_id, bool) or not isinstance(voyage_id, int) or not 1 <= voyage_id <= 2147483647:
            raise ValueError("Mã chuyến tàu không hợp lệ.")
        start, end = date(1900, 1, 1), vietnam_today()
        query, params = self._operation_query(start, end, terminal, voyage_id, production_scope=production_scope)
        rows = self._execute_query(query, params)
        if not rows:
            raise VoyageNotFound("Không có tác nghiệp qua cảng của chuyến tàu.")
        for row in rows:
            day = row["operation_day"]
            row["operation_day"] = day.date() if isinstance(day, datetime) else date.fromisoformat(day[:10]) if isinstance(day, str) else day
            _normalise_fact(row)
        label = rows[0]
        values = _panel_values(_aggregate(rows))
        header = {"terminal_id": terminal, "terminal_name": label["terminal_name"],
                  "voyage_id": str(voyage_id), "vessel_name": label.get("vessel_name"),
                  "voyage_code": label.get("voyage_code"), "arrival_at": _timestamp(label.get("arrival_at")),
                  "departure_at": _timestamp(label.get("departure_at")),
                  "first_operation_date": min(row["operation_day"] for row in rows).isoformat(),
                  "last_operation_date": max(row["operation_day"] for row in rows).isoformat(),
                  "cargo_names": sorted({row["cargo_name"] for row in rows}), **values}
        header.update(_berth_evidence(label, production_scope))
        return {"header": header, "summary": values, "rows": rows,
                "meta": {"scope": "whole_voyage", "terminal": terminal, "voyage_id": str(voyage_id),
                         "start_date": start.isoformat(), "end_date": end.isoformat(),
                         "filters": {"terminal": terminal, "voyage_id": str(voyage_id),
                                     "start_date": start.isoformat(), "end_date": end.isoformat(),
                                     "production_scope": production_scope},
                         "berth_rule_version": BERTH_RULE_VERSION,
                         "source_read_at": datetime.now(timezone.utc).isoformat(),
                         "read_consistency": "single_fact_set"}}

    # Existing consumers keep their routes while gaining identical date rules.
    def get_overview(self, start_date=None, end_date=None, terminal="all"):
        return self.get_dashboard(start_date, end_date, terminal)["overview"]

    def get_cargo_breakdown(self, start_date=None, end_date=None, terminal="all"):
        return self.get_dashboard(start_date, end_date, terminal)["cargo"]

    def get_throughput_history(self, start_date=None, end_date=None, terminal="all"):
        return self.get_dashboard(start_date, end_date, terminal)["history"]

    def get_terminal_breakdown(self, start_date=None, end_date=None, terminal="all"):
        return self.get_dashboard(start_date, end_date, terminal)["terminals"]

    def get_direction_breakdown(self, start_date=None, end_date=None, terminal="all"):
        return self.get_dashboard(start_date, end_date, terminal)["directions"]

    def get_top_customers(self, start_date=None, end_date=None, terminal="all"):
        return self.get_dashboard(start_date, end_date, terminal)["customers"]

    def get_efficiency_metrics(self, start_date=None, end_date=None, terminal="all"):
        return self.get_dashboard(start_date, end_date, terminal)["efficiency"]

    def get_yard_occupancy(self, start_date=None, end_date=None, terminal="all"):
        return self.get_dashboard(start_date, end_date, terminal)["yard"]


dashboard_repo = DashboardRepository()

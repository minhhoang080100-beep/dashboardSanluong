"""Bounded, SELECT-only S production preview; no inferred source policy.

The caller publishes only complete, approved previews. This adapter never writes
to TOS, runs a dashboard report, or changes the existing throughput formula.
"""
from collections import Counter
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation

from .catalog_source import source_id, _Reader, _native_sizes, SourceProblem
from .errors import CorporateError
if __package__ == 'corporate_api':
    from berth_scope import initial_berth_query
    from repository import DashboardRepository
else:
    from ..berth_scope import initial_berth_query
    from ..repository import DashboardRepository

DATABASES = {'cua_lo': 'SmartTOS', 'ben_thuy': 'SmartTOS_BenThuy'}
RESOURCES = ('contQuayVolumesCB', 'contGateVolumesCB', 'bulkQuayVolumesCB', 'bulkGateVolumesCB')
MAX_SOURCE_ROWS = 50000
MAX_BERTH_ROWS = 50000
CARGO_KINDS = frozenset({'container', 'bulk', 'roro', 'exclude'})
YARD_TYPES = frozenset({'Container Yard', 'Bulk Yard', 'Equipment Yard',
                        'Warehouse', 'Boned Warehouse', 'CFS'})
REQUIRED_COLUMNS = {
    'TallyShift': {'tallyShiftId', 'shiftDate', 'cargoId', 'jobMethodId', 'cargoDirectId',
                   'weightNetSum', 'weightUnitId', 'quantityTotalSum', 'vesselVoyageId',
                   'consigneeId', 'rowDeleted', 'quantityUnitId'},
    'Cargo': {'cargoId', 'cargoName', 'cargoGroupId'},
    'JobMethod': {'jobMethodId', 'statisticsGroupTypeIdList'},
    'StatisticsGroupType': {'statisticsGroupTypeId', 'statisticsGroupTypeCode', 'rowDeleted'},
    'BaseUnit': {'baseUnitId', 'baseUnitCode', 'TONE'},
    'ConversionUnit': {'baseUnitId', 'unitId', 'unitValue', 'cargoId', 'rowDeleted'},
    'VesselVoyage': {'vesselVoyageId', 'vesselId', 'rowDeleted', 'isVirtualVesselVoyage'},
    'Vessel': {'vesselId', 'rowDeleted', 'isVirtualVessel', 'vesselTypeId'},
    'VesselType': {'vesselTypeId', 'vesselTypeCode', 'rowDeleted'},
    'DoBerth': {'doBerthId', 'vesselVoyageId', 'berthId', 'ATA', 'ATB', 'rowDeleted', 'isArrival'},
    'Berth': {'berthId', 'berthCode'},
}
SCHEMA_SQL = """SELECT TABLE_NAME AS table_name,COLUMN_NAME AS column_name
FROM INFORMATION_SCHEMA.COLUMNS WHERE TABLE_SCHEMA='dbo'
AND TABLE_NAME IN ('TallyShift','Cargo','JobMethod','StatisticsGroupType','BaseUnit',
 'ConversionUnit','VesselVoyage','Vessel','VesselType','DoBerth','Berth')
ORDER BY TABLE_NAME,ORDINAL_POSITION"""


class SourceError(ValueError):
    def __init__(self, code, message):
        self.code = code
        super().__init__(message)


def _decimal(value):
    if value is None or isinstance(value, bool):
        return None
    try:
        number = Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None
    return number if number.is_finite() and number >= 0 else None


def _native_id(value):
    number = _decimal(value)
    return str(int(number)) if number is not None and number > 0 and number == int(number) else None


def _mapping(profile, key, terminal):
    outer = profile.get(key, {})
    mapping = outer.get(terminal, {}) if isinstance(outer, dict) else {}
    return mapping if isinstance(mapping, dict) else {}


def _method_ids(profile, key, terminal):
    outer = profile.get(key, {})
    if (not isinstance(outer, dict) or terminal not in outer
            or not isinstance(outer[terminal], list) or not outer[terminal]):
        return None
    values = {_native_id(value) for value in outer[terminal]}
    if None in values:
        raise SourceError('SOURCE_PROFILE', 'Danh sách phương án phải chứa ID nguồn nguyên dương.')
    return values


def _cargo_kind_maps(profile, key):
    mapping = profile.get(key, {})
    if not isinstance(mapping, dict):
        raise SourceError('SOURCE_PROFILE', 'Ánh xạ phân loại hàng phải là object theo xí nghiệp.')
    for terminal, assignments in mapping.items():
        if terminal not in DATABASES or not isinstance(assignments, dict):
            raise SourceError('SOURCE_PROFILE', 'Phạm vi ánh xạ phân loại hàng không hợp lệ.')
        for identity, kind in assignments.items():
            if (not isinstance(identity, str) or _native_id(identity) != identity
                    or not isinstance(kind, str) or kind not in CARGO_KINDS):
                raise SourceError('SOURCE_PROFILE', 'Phân loại hàng cần ID nguồn hợp lệ và loại container/bulk/roro/exclude.')
    return mapping


def _facts_sql(terminal):
    schema = DATABASES[terminal] + '.dbo'
    repo = DashboardRepository()
    # Read facts independently of berth-history ranking. Combining these joins
    # produced an expensive SQL Server plan even for a single reporting day.
    return f"""WITH period_facts AS (
        SELECT t.tallyShiftId,t.shiftDate,t.cargoId,t.jobMethodId,t.cargoDirectId,
            t.weightNetSum,t.quantityTotalSum,t.consigneeId,t.vesselVoyageId,
            t.quantityUnitId,t.weightUnitId
        FROM {schema}.TallyShift t
        WHERE t.shiftDate>=? AND t.shiftDate<? AND ISNULL(t.rowDeleted,0)=0
    )
    SELECT TOP ({MAX_SOURCE_ROWS + 1})
        t.tallyShiftId AS source_id,t.shiftDate AS business_date,t.cargoId AS cargo_id,
        c.cargoName AS cargo_name,c.cargoGroupId AS cargo_group_id,t.jobMethodId AS method_id,t.cargoDirectId AS direction_id,
        t.weightNetSum AS native_weight,t.quantityTotalSum AS quantity,
        t.consigneeId AS customer_id,s.vesselId AS ship_id,t.vesselVoyageId AS voyage_id,
        vt.vesselTypeCode AS vessel_type_code,qu.baseUnitCode AS quantity_unit_code,
        CASE WHEN {repo.physical_voyage_filter} THEN 1 ELSE 0 END AS physical_voyage,
        CASE WHEN {repo.throughput_filter.format(schema=schema)} THEN 1 ELSE 0 END AS quay_eligible,
        {repo.tonne_factor_logic} AS tonne_factor
        FROM period_facts t
        LEFT JOIN {schema}.Cargo c ON c.cargoId=t.cargoId
        LEFT JOIN {schema}.JobMethod j ON j.jobMethodId=t.jobMethodId
        LEFT JOIN {schema}.VesselVoyage v ON v.vesselVoyageId=t.vesselVoyageId
        LEFT JOIN {schema}.Vessel s ON s.vesselId=v.vesselId
        LEFT JOIN {schema}.VesselType vt ON vt.vesselTypeId=s.vesselTypeId AND ISNULL(vt.rowDeleted,0)=0
        LEFT JOIN {schema}.BaseUnit qu ON qu.baseUnitId=t.quantityUnitId
        LEFT JOIN {schema}.BaseUnit u ON u.baseUnitId=t.weightUnitId
        LEFT JOIN (
            SELECT cu.baseUnitId,MIN(cu.unitValue) AS tonne_factor
            FROM {schema}.ConversionUnit cu
            JOIN {schema}.BaseUnit source_unit ON source_unit.baseUnitId=cu.baseUnitId
            JOIN {schema}.BaseUnit target_unit ON target_unit.baseUnitId=cu.unitId
            WHERE ISNULL(cu.rowDeleted,0)=0 AND cu.cargoId=0
              AND source_unit.baseUnitCode=N'KG'
              AND target_unit.baseUnitCode=N'TAN' AND target_unit.TONE=1
            GROUP BY cu.baseUnitId
            HAVING COUNT(DISTINCT cu.unitValue)=1 AND MIN(cu.unitValue)>0
        ) mass_conversion ON mass_conversion.baseUnitId=u.baseUnitId
        ORDER BY t.shiftDate,t.tallyShiftId"""


def _berths_sql(terminal):
    schema = DATABASES[terminal] + '.dbo'
    berth = initial_berth_query(schema, terminal, selection='period_voyages')
    # Bound WHICH voyages are needed, never WHEN their first berth occurred.
    return f"""WITH period_voyages AS (
        SELECT DISTINCT t.vesselVoyageId
        FROM {schema}.TallyShift t
        WHERE t.shiftDate>=? AND t.shiftDate<? AND ISNULL(t.rowDeleted,0)=0
          AND t.vesselVoyageId>0
    )
    SELECT TOP ({MAX_BERTH_ROWS + 1}) berth_scope.vesselVoyageId AS voyage_id,
        berth_scope.production_scope,berth_scope.initial_berth_id,
        berth_scope.initial_berth_code,berth_scope.initial_berth_at,
        berth_scope.berth_assignment_status
    FROM ({berth}) berth_scope
    ORDER BY berth_scope.vesselVoyageId"""


def _result():
    return {'rows': [], 'warnings': [], 'status': 'blocked', 'ready': False, 'blockers': [],
            'coverage': [], 'source_row_count': 0, 'excluded_roro_rows': 0,
            'excluded_scope_rows': 0, 'excluded_unknown_scope_rows': 0,
            'excluded_location_rows': 0, 'excluded_unknown_location_rows': 0,
            'excluded_nonphysical_rows': 0, 'excluded_empty_container_rows': 0,
            'unselected_source_row_count': 0, 'source_errors': [], 'issue_samples': []}


class ProductionSource:
    def __init__(self, query_fn):
        """query_fn(database, static_sql, params) returns bounded dict rows."""
        self.query = query_fn

    def _read_terminal(self, terminal, start, end):
        database = DATABASES[terminal]
        describe = getattr(self.query, 'describe_table', None)
        if callable(describe):
            # Runtime can inspect one known table using SELECT TOP (0), avoiding
            # the expensive multi-table INFORMATION_SCHEMA plan on SmartTOS.
            # Production expressions already define unit/type semantics; this
            # preflight establishes the required column names only.
            metadata = []
            for table in REQUIRED_COLUMNS:
                columns = describe(database, table)
                if (not isinstance(columns, list)
                        or any(not isinstance(row, dict) or not isinstance(row.get('column_name'), str)
                               or not row['column_name'] for row in columns)):
                    raise SourceError('SOURCE_SCHEMA', 'Kết quả kiểm tra cột bảng nguồn không hợp lệ.')
                names = [row['column_name'] for row in columns]
                if len(set(names)) != len(names):
                    raise SourceError('SOURCE_SCHEMA', 'Kết quả kiểm tra cột bảng nguồn bị trùng tên.')
                metadata.extend({'table_name': table, 'column_name': name} for name in names)
        else:
            metadata = self.query(database, SCHEMA_SQL, ())
        if (not isinstance(metadata, list) or any(not isinstance(item, dict)
                or not isinstance(item.get('table_name'), str)
                or not isinstance(item.get('column_name'), str) for item in metadata)):
            raise SourceError('SOURCE_SCHEMA', 'Kết quả kiểm tra cấu trúc nguồn không hợp lệ.')
        present = {}
        for item in metadata:
            present.setdefault(item['table_name'], set()).add(item['column_name'])
        missing = [table + '.' + column for table, columns in REQUIRED_COLUMNS.items()
                   for column in sorted(columns - present.get(table, set()))]
        if missing:
            raise SourceError('SOURCE_SCHEMA', 'Thiếu cột nguồn đã xác minh: ' + ', '.join(missing))
        facts = self.query(database, _facts_sql(terminal), (start, end + timedelta(days=1)))
        if not isinstance(facts, list) or any(not isinstance(item, dict) for item in facts):
            raise SourceError('SOURCE_DATA', 'Kết quả truy vấn sản lượng không phải danh sách bản ghi.')
        if len(facts) > MAX_SOURCE_ROWS:
            raise SourceError('SOURCE_ROW_LIMIT', 'Kỳ đọc nguồn quá lớn; chia thành kỳ ngắn hơn.')
        voyages = set()
        fact_voyages = []
        for fact in facts:
            raw = fact.get('voyage_id')
            voyage = _native_id(raw)
            if 'voyage_id' not in fact or (voyage is None and (isinstance(raw, bool) or raw not in (None, 0))):
                raise SourceError('SOURCE_DATA', 'Phiếu nguồn thiếu hoặc sai mã chuyến để đối chiếu cầu.')
            fact_voyages.append(voyage)
            if voyage is not None:
                voyages.add(voyage)
        assignments = {}
        if voyages:
            berths = self.query(database, _berths_sql(terminal), (start, end + timedelta(days=1)))
            if not isinstance(berths, list) or any(not isinstance(row, dict) for row in berths):
                raise SourceError('SOURCE_DATA', 'Kết quả đối chiếu cầu không phải danh sách bản ghi.')
            if len(berths) > MAX_BERTH_ROWS:
                raise SourceError('SOURCE_ROW_LIMIT', 'Số chuyến đối chiếu cầu vượt giới hạn đọc an toàn.')
            for row in berths:
                voyage = _native_id(row.get('voyage_id'))
                scope = row.get('production_scope')
                if (voyage not in voyages or voyage in assignments
                        or not isinstance(scope, str) or scope not in {'nghe_tinh', 'vietsun', 'unclassified'}
                        or (terminal != 'cua_lo' and scope == 'vietsun')):
                    raise SourceError('SOURCE_DATA', 'Kết quả cầu có mã chuyến trùng, ngoài phạm vi hoặc phân loại không hợp lệ.')
                assignments[voyage] = scope
        # Missing history is unknown, never an implicit Nghệ Tĩnh assignment.
        # Copy rows so callers' fixtures or request-local source data are not mutated.
        return [{**fact, 'production_scope': assignments.get(voyage, 'unclassified')}
                for fact, voyage in zip(facts, fact_voyages)]

    def extract(self, start: date, end: date, profile: dict | None = None, *, resources=None):
        if (type(start) is not date or type(end) is not date or end < start
                or (end - start).days >= 31):
            raise SourceError('SOURCE_RANGE', 'Mỗi lần đọc nguồn tối đa 31 ngày liên tiếp.')
        selected = list(RESOURCES) if resources is None else resources
        if (not isinstance(selected, (list, tuple)) or not selected
                or any(not isinstance(name, str) or name not in RESOURCES for name in selected)
                or len(set(selected)) != len(selected)):
            raise SourceError('SOURCE_RESOURCE', 'Phạm vi API sản lượng không hợp lệ.')
        selected_set = set(selected)
        needs_container_sizes = any(name.startswith('cont') for name in selected)
        profile = profile or {}
        if not isinstance(profile, dict) or profile.get('company_id', 'CNT') != 'CNT':
            raise SourceError('SOURCE_PROFILE', 'Hồ sơ nguồn chỉ áp dụng cho CNT.')
        quay_mode = profile.get('quay_selection', 'methods')
        if not isinstance(quay_mode, str) or quay_mode not in {'methods', 'source_statistics'}:
            raise SourceError('SOURCE_PROFILE', 'Cách chọn dữ liệu qua cầu không hợp lệ.')
        by_statistics = quay_mode == 'source_statistics'
        cargo_kinds = _cargo_kind_maps(profile, 'cargo_kind_by_cargo')
        group_kinds = _cargo_kind_maps(profile, 'cargo_kind_by_group')
        gate_mode = profile.get('gate_selection', 'methods')
        if gate_mode not in {'methods', 'vessel_type'}:
            raise SourceError('SOURCE_PROFILE', 'Cách chọn dữ liệu cổng/bãi không hợp lệ.')
        by_yard = gate_mode == 'vessel_type'
        size_mode = profile.get('container_size_source', 'configured')
        if size_mode not in {'configured', 'native_domestic', 'native_cargo'}:
            raise SourceError('SOURCE_PROFILE', 'Nguồn danh mục kích cỡ container không hợp lệ.')
        terminals = profile.get('terminals', list(DATABASES))
        if (not isinstance(terminals, list) or not terminals
                or any(not isinstance(t, str) or t not in DATABASES for t in terminals)
                or len(set(terminals)) != len(terminals)):
            raise SourceError('SOURCE_PROFILE', 'Phạm vi nguồn xí nghiệp không hợp lệ.')
        results = {name: _result() for name in RESOURCES}
        issues = {name: Counter() for name in RESOURCES}
        groups = {name: {} for name in RESOURCES}
        def flag(name, code, fact, terminal):
            issues[name][code] += 1
            # Private reconciliation only; never part of the public API payload.
            samples = results[name]['issue_samples']
            if len(samples) < 50:
                samples.append({'code': code, 'terminal': terminal,
                    'sourceId': fact.get('source_id'), 'date': str(fact.get('business_date')),
                    'cargoId': fact.get('cargo_id'), 'methodId': fact.get('method_id'),
                    'quantityUnit': fact.get('quantity_unit_code')})
        scope = profile.get('production_scope')
        global_blockers = []
        if set(terminals) != set(DATABASES):
            global_blockers.append('SOURCE_TERMINALS_INCOMPLETE')
        if profile.get('approved') is not True:
            global_blockers.append('SOURCE_PROFILE_UNAPPROVED')
        if profile.get('date_basis') != 'shiftDate':
            global_blockers.append('DATE_BASIS_UNCONFIRMED')
        if scope not in {'nghe_tinh', 'vietsun', 'all_activity'}:
            global_blockers.append('PRODUCTION_SCOPE_UNCONFIRMED')
        report_date = profile.get('report_date', datetime.now(timezone(timedelta(hours=7))).date())
        if isinstance(report_date, str):
            try:
                report_date = date.fromisoformat(report_date)
            except ValueError as exc:
                raise SourceError('SOURCE_PROFILE', 'Ngày đọc nguồn không hợp lệ.') from exc
        if type(report_date) is not date:
            raise SourceError('SOURCE_PROFILE', 'Ngày đọc nguồn không hợp lệ.')

        for terminal in terminals:
            quay_methods = None if by_statistics else _method_ids(profile, 'quay_method_ids', terminal)
            gate_methods = _method_ids(profile, 'gate_method_ids', terminal)
            if quay_methods is None and not by_statistics:
                for name in ('contQuayVolumesCB', 'bulkQuayVolumesCB'):
                    issues[name]['QUAY_METHODS_UNCONFIRMED'] += 1
            if gate_methods is None and not by_yard:
                for name in ('contGateVolumesCB', 'bulkGateVolumesCB'):
                    issues[name]['GATE_METHODS_UNCONFIRMED'] += 1
            # Endpoint sets must be deliberately exclusive: a gate cannot be
            # inferred from every operation not selected as quay throughput.
            if not by_yard and quay_methods and gate_methods and quay_methods & gate_methods:
                raise SourceError('SOURCE_PROFILE', 'Phương án cầu và cổng đang bị chồng lấn.')
            try:
                facts = self._read_terminal(terminal, start, end)
            except Exception as exc:
                # Unknown driver text may contain host or credentials. Only our
                # own schema diagnostics are safe to copy into a preview.
                code = exc.code if isinstance(exc, SourceError) else 'SOURCE_UNAVAILABLE'
                message = str(exc) if isinstance(exc, SourceError) else 'Không đọc được nguồn SQL; chưa công bố dữ liệu mới.'
                if isinstance(exc, CorporateError) and exc.code in {
                        'SOURCE_TIMEOUT', 'SOURCE_UNAVAILABLE', 'SOURCE_SCHEMA', 'SOURCE_ROW_LIMIT'}:
                    code = exc.code
                    # Use only typed, allowlisted codes. Driver/adapter messages
                    # can contain credentials even when an error is categorized.
                    message = 'Không đọc được nguồn SQL hợp lệ; chưa công bố dữ liệu mới.'
                for name in selected:
                    issues[name][code] += 1
                    results[name]['source_errors'].append({'terminal': terminal, 'code': code, 'message': message})
                continue
            native_sizes = {}
            if needs_container_sizes and size_mode == 'native_domestic':
                try:
                    size_rows, _ = _native_sizes(_Reader(self.query, terminal,
                        tables=['vwContainerSizeTypeDomestic']), report_date.strftime('%Y%m%d'))
                    native_sizes = {row['containerSizeId']: row for row in size_rows}
                except SourceProblem as exc:
                    for name in ('contQuayVolumesCB', 'contGateVolumesCB'):
                        issues[name][exc.code] += 1
                        results[name]['source_errors'].append(
                            {'terminal': terminal, 'code': exc.code, 'message': str(exc)})
            source_keys = set()
            for fact in facts:
                native = _native_id(fact.get('source_id'))
                if native is None or native in source_keys:
                    raise SourceError('SOURCE_DUPLICATE', 'Dòng nguồn không có khóa duy nhất.')
                source_keys.add(native)
                cargo = _native_id(fact.get('cargo_id'))
                kind = cargo_kinds.get(terminal, {}).get(cargo)
                if kind is None:
                    kind = group_kinds.get(terminal, {}).get(_native_id(fact.get('cargo_group_id')))
                # A known bulk row cannot invalidate a container endpoint (and
                # vice versa), even when its berth or physical-ship data is bad.
                prefixes = (('cont',) if kind == 'container' else
                            ('bulk',) if kind == 'bulk' else ('cont', 'bulk'))
                gate_affected = tuple(prefix + 'GateVolumesCB' for prefix in prefixes)
                method = _native_id(fact.get('method_id'))
                location_type = fact.get('vessel_type_code')
                if by_yard and location_type == 'Ro-Ro Yard':
                    for endpoint in gate_affected:
                        results[endpoint]['excluded_roro_rows'] += 1
                    continue
                is_gate = (location_type in YARD_TYPES if by_yard
                           else gate_methods is not None and method in gate_methods)
                if by_yard and not is_gate:
                    # The agreed gate scope is the explicit vesselTypeCode
                    # whitelist. Unknown locations are outside it, never an
                    # inferred yard. Physical quay activity can still qualify.
                    for endpoint in gate_affected:
                        results[endpoint]['excluded_location_rows'] += 1
                        results[endpoint]['excluded_unknown_location_rows'] += int(not location_type)
                is_quay = (not (by_yard and is_gate) and fact.get('quay_eligible') == 1
                           and (quay_methods is None or method in quay_methods))
                if by_statistics and not by_yard and is_gate and is_quay:
                    raise SourceError('SOURCE_PROFILE', 'Phương án cổng trùng nhóm sản lượng qua cảng của nguồn.')
                if not is_quay and not is_gate:
                    for name in gate_affected:
                        results[name]['unselected_source_row_count'] += 1
                    continue
                side = 'Quay' if is_quay else 'Gate'
                affected = tuple(prefix + side + 'VolumesCB' for prefix in prefixes)
                actual_scope = fact.get('production_scope')
                # Yard operations do not have an initial vessel berth. Their
                # scope is all configured yard locations, independent of quay KPIs.
                if not (by_yard and is_gate) and scope != 'all_activity' and scope in {'nghe_tinh', 'vietsun'} and actual_scope != scope:
                    # Same scope filter as the dashboard: unknown initial
                    # berths stay outside Nghệ Tĩnh/Cầu 5, with explicit counts.
                    for name in affected:
                        results[name]['excluded_scope_rows'] += 1
                        results[name]['excluded_unknown_scope_rows'] += int(actual_scope == 'unclassified')
                    continue
                quantity_unit = str(fact.get('quantity_unit_code') or '').strip().upper()
                if quantity_unit == 'GIO' and (kind == 'container' or (by_yard and is_gate)):
                    for endpoint in affected:
                        results[endpoint].setdefault('excluded_time_rows', 0)
                        results[endpoint]['excluded_time_rows'] += 1
                    continue
                if kind in {'roro', 'exclude'}:
                    for name in affected:
                        results[name]['excluded_roro_rows'] += int(kind == 'roro')
                    continue
                if is_quay:
                    physical = fact.get('physical_voyage')
                    if type(physical) is int and physical == 0:
                        for endpoint in affected:
                            results[endpoint]['excluded_nonphysical_rows'] += 1
                        continue
                    if type(physical) is not int or physical != 1:
                        for endpoint in affected:
                            flag(endpoint, 'PHYSICAL_SHIP_OR_DIRECTION_UNAVAILABLE', fact, terminal)
                        continue
                if kind not in {'container', 'bulk'}:
                    for name in affected:
                        issues[name]['CARGO_KIND_UNMAPPED'] += 1
                    continue
                name = ('cont' if kind == 'container' else 'bulk') + side + 'VolumesCB'
                if name not in selected_set:
                    continue
                results[name]['source_row_count'] += 1
                if method is None:
                    issues[name]['METHOD_ID_UNAVAILABLE'] += 1
                    continue
                day = fact.get('business_date')
                if isinstance(day, datetime):
                    day = day.date()
                if type(day) is not date or not start <= day <= end:
                    issues[name]['SOURCE_DATE_INVALID'] += 1
                    continue
                if is_quay:
                    ship = _native_id(fact.get('ship_id'))
                    direction = _native_id(fact.get('direction_id'))
                    if not ship or direction not in {'1', '2'}:
                        flag(name, 'PHYSICAL_SHIP_OR_DIRECTION_UNAVAILABLE', fact, terminal)
                        continue
                weight = _decimal(fact.get('native_weight'))
                if (kind == 'container' and size_mode == 'native_cargo'
                        and _decimal(fact.get('quantity')) == 0
                        and (weight == 0 or (quantity_unit == 'CONT'
                                             and fact.get('native_weight') is None))):
                    # Native container sources contain explicit zero-count,
                    # zero-weight activity slots even when the unit is absent.
                    # An absent weight is excluded only with an explicit CONT
                    # unit; never infer movement or zero weight for other gaps.
                    results[name]['excluded_empty_container_rows'] += 1
                    continue
                if kind == 'container' and quantity_unit != 'CONT':
                    flag(name, 'CONTAINER_QUANTITY_UNIT_UNCONFIRMED', fact, terminal)
                    continue
                factor = _decimal(fact.get('tonne_factor'))
                if weight is None or factor is None or factor <= 0:
                    flag(name, 'WEIGHT_OR_UNIT_UNAVAILABLE', fact, terminal)
                    continue
                tonnes = weight * factor
                if tonnes > Decimal('1000000000000000'):
                    issues[name]['WEIGHT_OUT_OF_RANGE'] += 1
                    continue
                row = {'reportDate': report_date.strftime('%Y%m%d'), 'companyId': 'CNT',
                       'finishDate': day.strftime('%Y%m%d'),
                       'handlingMethodId': source_id(terminal, method)}
                if is_quay:
                    row['shipId'] = source_id(terminal, ship)
                    row['classId' if kind == 'container' else 'shipClassId'] = source_id(terminal, direction)
                if kind == 'container':
                    size = _mapping(profile, 'container_sizes_by_cargo', terminal).get(cargo)
                    cargo_name = str(fact.get('cargo_name') or '').strip().upper()
                    size_id = source_id(terminal, cargo)
                    if size_mode == 'native_cargo':
                        # Approved local-cargo mode keeps the original Cargo ID
                        # and exact canonical name. It does not invent ISO data.
                        raw_name = fact.get('cargo_name')
                        size = ({'localSzTp': raw_name} if isinstance(raw_name, str)
                                and raw_name in DashboardRepository.container_cargo_codes else None)
                    elif size_mode == 'native_domestic':
                        size_id = _native_id(_mapping(profile, 'container_size_ids_by_cargo', terminal).get(cargo))
                        size = native_sizes.get(size_id)
                        if not size or size.get('sizeCode') != cargo_name[:2]:
                            flag(name, 'CONTAINER_SIZE_RELATION_UNCONFIRMED', fact, terminal)
                            continue
                    quantity = _decimal(fact.get('quantity'))
                    teu_factor = (1 if cargo_name in {'20F', '20E', '20R'} else
                                  2 if cargo_name in {'40F', '40E', '40R', '45F', '45E'} else None)
                    if not isinstance(size, dict) or not size.get('localSzTp') or teu_factor is None:
                        flag(name, 'CONTAINER_SIZE_UNMAPPED', fact, terminal)
                        continue
                    if quantity is None or quantity != int(quantity):
                        flag(name, 'CONTAINER_QUANTITY_UNAVAILABLE', fact, terminal)
                        continue
                    row.update(originId=None, containerWeight=tonnes, containerTEU=quantity * teu_factor,
                               containerOperatorId=None, containerSizeId=size_id)
                    if is_quay:
                        row['shipOperatorId'] = None
                    sums = ('containerWeight', 'containerTEU')
                else:
                    native_groups = profile.get('cargo_catalog_source') == 'native_groups'
                    cargo_type = (_native_id(fact.get('cargo_group_id')) if native_groups
                                  else _mapping(profile, 'cargo_type_by_cargo', terminal).get(cargo))
                    # Native group IDs are checked against the fresh CargoGroup
                    # catalog by downstream publication/live FK validation.
                    # A second static profile allowlist would reject new groups.
                    if not cargo_type or (not native_groups and cargo_type not in profile.get('cargo_types', {})):
                        issues[name]['CARGO_TYPE_UNMAPPED'] += 1
                        continue
                    row.update(cargoTypeId=cargo_type, cargoCategoryId=source_id(terminal, cargo),
                               bulkOriginId=None, bulkWeight=tonnes)
                    if is_quay:
                        row['shipAgentId'] = None
                    else:
                        customer = _native_id(fact.get('customer_id'))
                        row['customerCode'] = source_id(terminal, customer) if customer else None
                    sums = ('bulkWeight',)
                key = tuple((key, value) for key, value in sorted(row.items()) if key not in sums)
                if key in groups[name]:
                    provenance = groups[name][key]['_sourceTerminals']
                    if terminal not in provenance:
                        provenance.append(terminal)
                        provenance.sort()
                    for measure in sums:
                        groups[name][key][measure] += row[measure]
                else:
                    # Preserve every contributing source before combining rows
                    # with identical public dimensions. This private field is
                    # required for origin-aware downstream FK validation.
                    row['_sourceTerminals'] = [terminal]
                    groups[name][key] = row
        for name in selected:
            item = results[name]
            item['rows'] = list(groups[name].values())
            if any(value > Decimal('1000000000000000')
                   for row in item['rows'] for key, value in row.items()
                   if key in {'containerWeight', 'containerTEU', 'bulkWeight'}):
                issues[name]['AGGREGATE_OUT_OF_RANGE'] += 1
            item['blockers'] = sorted(set(global_blockers) | set(issues[name]))
            item['issue_counts'] = dict(issues[name])
            item['ready'] = not item['blockers']
            item['status'] = 'ready' if item['ready'] else 'blocked'
            if item['ready']:
                item['coverage'] = [[start.strftime('%Y%m%d'), end.strftime('%Y%m%d')]]
            item['warnings'] = [
                'finishDate dùng ngày hạch toán ca shiftDate theo hồ sơ nguồn; không khẳng định là thời điểm hoàn tất tác nghiệp.',
                'Nguồn gốc hàng, chủ khai thác tàu/vỏ và đại lý chưa được xác minh; trường tương ứng giữ null.',
                'ID giữ nguyên từ database, không thêm tiền tố; ID trùng khác nội dung giữa hai nguồn cần tách phạm vi.',
            ]
            if by_yard and 'Gate' in name:
                item['warnings'].append('Phạm vi tất cả kho/bãi theo vesselTypeCode; không lọc phương án hoặc cầu cập tàu. Có thể gồm gom bãi và tác nghiệp tính phí, chưa phải chỉ tiêu hàng qua cổng vật lý.')
            if item['unselected_source_row_count']:
                item['warnings'].append(
                    f"Có {item['unselected_source_row_count']} phiếu ngoài phạm vi tác nghiệp đã chọn; không tự gán chúng thành sản lượng cổng."
                )
            if item['excluded_scope_rows']:
                item['warnings'].append(
                    f"Loại {item['excluded_scope_rows']} phiếu ngoài phạm vi cầu đã chọn, gồm {item['excluded_unknown_scope_rows']} phiếu chưa xác định cầu ban đầu; không gán lại vào Cảng Nghệ Tĩnh hoặc Cầu 5."
                )
            if item['excluded_location_rows']:
                item['warnings'].append(
                    f"Loại {item['excluded_location_rows']} phiếu ngoài danh sách loại kho/bãi, gồm {item['excluded_unknown_location_rows']} phiếu thiếu loại địa điểm; không tự gán thành kho/bãi."
                )
            if item['excluded_nonphysical_rows']:
                item['warnings'].append(
                    f"Loại {item['excluded_nonphysical_rows']} phiếu không thuộc chuyến tàu thực khỏi sản lượng qua cầu."
                )
            if item['excluded_empty_container_rows']:
                item['warnings'].append(
                    f"Loại {item['excluded_empty_container_rows']} phiếu container có số lượng bằng 0 và trọng lượng bằng 0, hoặc đơn vị CONT chưa ghi trọng lượng; không quy đổi trọng lượng thiếu thành 0."
                )
        return {name: results[name] for name in selected}

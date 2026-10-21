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
MAX_ZERO_MASS_SAMPLES = 50
CARGO_KINDS = frozenset({'container', 'bulk', 'roro', 'exclude'})
YARD_TYPES = frozenset({'Container Yard', 'Bulk Yard', 'Equipment Yard',
                        'Warehouse', 'Boned Warehouse', 'CFS'})
REQUIRED_COLUMNS = {
    'TallyShift': {'tallyShiftId', 'shiftDate', 'cargoId', 'jobMethodId', 'cargoDirectId',
                   'weightNetSum', 'weightUnitId', 'quantityTotalSum', 'vesselVoyageId',
                   'consigneeId', 'rowDeleted', 'quantityUnitId'},
    'Cargo': {'cargoId', 'cargoName', 'cargoGroupId', 'rowDeleted'},
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
BULK_CONVERSION_COLUMNS = {
    'TallyShift': {'tallyShiftId', 'cargoManifestId', 'cargoId', 'vesselVoyageId', 'rowDeleted',
                   'weightUnitId', 'weightNetSum', 'shiftDate'},
    'CargoManifest': {'cargoManifestId', 'cargoId', 'vesselVoyageId', 'goodsSpecificationId', 'rowDeleted'},
    'GoodsSpecification': {'goodsSpecificationId', 'rowDeleted'},
    'BaseUnit': {'baseUnitId', 'baseUnitCode', 'TONE', 'rowDeleted'},
    'ConversionUnit': {'conversionUnitId', 'baseUnitId', 'unitId', 'cargoId', 'goodsSpecificationId',
                       'unitValue', 'rowDeleted', 'rowInvisible', 'createTime', 'updateTime'},
}
BULK_CONVERSION_SCHEMA_SQL = """SELECT TABLE_NAME AS table_name,COLUMN_NAME AS column_name
FROM INFORMATION_SCHEMA.COLUMNS WHERE TABLE_SCHEMA='dbo'
AND TABLE_NAME IN ('TallyShift','CargoManifest','GoodsSpecification','BaseUnit','ConversionUnit')
ORDER BY TABLE_NAME,ORDINAL_POSITION"""
MAX_BULK_CONVERSION_FACTS = 1000
MAX_BULK_CONVERSION_EDGES = 1000
BULK_CONVERSION_UNITS = frozenset({'M3', 'VỎ BỊCH'})


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


def _bulk_conversion_ids(profile):
    """An explicit per-source opt-in, never a generic volume-to-mass policy."""
    raw = profile.get('bulk_unit_conversion_cargo_ids', {})
    if not isinstance(raw, dict):
        raise SourceError('SOURCE_PROFILE', 'Phạm vi quy đổi hàng rời phải là object theo xí nghiệp.')
    result = {}
    for terminal, values in raw.items():
        if (terminal not in DATABASES or not isinstance(values, list) or len(values) > 32
                or any(not isinstance(value, str) or _native_id(value) != value for value in values)
                or len(set(values)) != len(values)):
            raise SourceError('SOURCE_PROFILE', 'Quy đổi hàng rời cần danh sách ID nguồn nguyên dương duy nhất.')
        result[terminal] = set(values)
    return result


def _source_active(value):
    return value is None or value is False or (type(value) is int and value == 0)


def _conversion_predates(edge, day):
    # Current catalog values cannot reconstruct an earlier revision. A change
    # on the same day is also ambiguous because shiftDate has no operation time.
    created, updated = edge.get('created_at'), edge.get('updated_at')
    if not isinstance(created, datetime) or created.date() < date(1900, 1, 1):
        return False
    try:
        if updated is not None and (not isinstance(updated, datetime) or updated < created):
            return False
        return max(created, updated or created).date() < day
    except TypeError:
        return False


def _bulk_conversion_factor(fact, manifest, edges):
    """Resolve verified cargo/spec paths: M3 mass or corroborated native mass.

    These are source-catalog factors, not density inferred from cargo names or a
    target report. Missing, conflicting, deleted or historically changed paths
    leave the original missing-weight blocker in place.
    """
    unit = str(fact.get('weight_unit_code') or '').strip().upper()
    if not isinstance(manifest, dict) or unit not in BULK_CONVERSION_UNITS:
        return None
    day = fact.get('business_date')
    day = day.date() if isinstance(day, datetime) else day
    linked_day = manifest.get('fact_business_date')
    linked_day = linked_day.date() if isinstance(linked_day, datetime) else linked_day
    cargo, voyage = _native_id(fact.get('cargo_id')), _native_id(fact.get('voyage_id'))
    spec = _native_id(manifest.get('specification_id'))
    if (type(day) is not date or linked_day != day or cargo is None or voyage is None or spec is None
            or _native_id(manifest.get('manifest_id')) is None
            or _native_id(manifest.get('fact_cargo_id')) != cargo
            or _native_id(manifest.get('manifest_cargo_id')) != cargo
            or _native_id(manifest.get('fact_voyage_id')) != voyage
            or _native_id(manifest.get('manifest_voyage_id')) != voyage
            or _decimal(fact.get('native_weight')) is None
            or _decimal(manifest.get('fact_weight')) != _decimal(fact.get('native_weight'))
            or any(key not in manifest or not _source_active(manifest[key])
                   for key in ('fact_deleted', 'manifest_deleted', 'specification_deleted'))):
        return None
    candidates = {'forward': [], 'mass': [], 'reverse': [], 'direct': []}
    for edge in edges:
        if not isinstance(edge, dict) or _native_id(edge.get('specification_id')) != spec:
            continue
        if any(key not in edge or not _source_active(edge[key]) for key in (
                'edge_deleted', 'edge_hidden', 'source_unit_deleted', 'target_unit_deleted')):
            continue
        source, target = edge.get('source_unit_code'), edge.get('target_unit_code')
        edge_cargo = _native_id(edge.get('cargo_id'))
        kind = ('forward' if (source, target, edge_cargo) == ('M3', 'KG', cargo) else
                'reverse' if (source, target, edge_cargo) == ('TAN', 'M3', cargo) else
                'direct' if (source, target, edge_cargo) == ('VỎ BỊCH', 'TAN', cargo) else
                'mass' if (source, target) == ('KG', 'TAN') and type(edge.get('cargo_id')) is int
                and edge['cargo_id'] == 0 else None)
        if kind is not None:
            # Keep invalid values in this list: they must invalidate uniqueness
            # rather than silently allowing a second, apparently valid edge.
            candidates[kind].append(edge)
    if unit == 'VỎ BỊCH':
        # This source-specific unit is declared as mass by BaseUnit.TONE=1.
        # Require the independent cargo/spec conversion to confirm exactly the
        # same tonne value; never generalize this to arbitrary count units.
        if len(candidates['direct']) != 1:
            return None
        edge = candidates['direct'][0]
        if (_decimal(edge.get('factor')) != 1 or _decimal(edge.get('source_unit_tone')) != 1
                or _decimal(edge.get('target_unit_tone')) != 1 or not _conversion_predates(edge, day)
                or any(_native_id(edge.get(key)) is None for key in ('conversion_id', 'source_unit_id', 'target_unit_id'))
                or _native_id(edge['source_unit_id']) == _native_id(edge['target_unit_id'])
                or _native_id(manifest.get('fact_weight_unit_id')) != _native_id(edge['source_unit_id'])):
            return None
        return Decimal(1)
    if any(len(candidates[key]) != 1 for key in ('forward', 'mass', 'reverse')):
        return None
    forward, mass, reverse = (candidates[key][0] for key in ('forward', 'mass', 'reverse'))
    factors = [_decimal(edge.get('factor')) for edge in (forward, mass, reverse)]
    if (any(value is None or value <= 0 for value in factors)
            or any(not _conversion_predates(edge, day) for edge in (forward, mass, reverse))
            or _decimal(mass.get('target_unit_tone')) != 1
            or _decimal(reverse.get('source_unit_tone')) != 1
            or any(_native_id(edge.get('conversion_id')) is None for edge in (forward, mass, reverse))
            or _native_id(forward.get('target_unit_id')) != _native_id(mass.get('source_unit_id'))
            or _native_id(mass.get('target_unit_id')) != _native_id(reverse.get('source_unit_id'))
            or _native_id(reverse.get('target_unit_id')) != _native_id(forward.get('source_unit_id'))
            or _native_id(manifest.get('fact_weight_unit_id')) != _native_id(forward.get('source_unit_id'))
            or any(_native_id(edge.get(key)) is None for edge in (forward, mass, reverse)
                   for key in ('source_unit_id', 'target_unit_id'))):
        return None
    factor = factors[0] * factors[1]
    return factor if factor * factors[2] == 1 else None


def _bulk_conversion_candidates(facts, *, start, end, allowed_cargo_ids, cargo_kinds, group_kinds,
                                selected, by_yard, quay_methods, gate_methods, scope):
    """Batch only rows that reach bulk mass validation in the selected scope."""
    candidates = []
    for fact in facts:
        cargo = _native_id(fact.get('cargo_id'))
        kind = cargo_kinds.get(cargo, group_kinds.get(_native_id(fact.get('cargo_group_id'))))
        weight = _decimal(fact.get('native_weight'))
        quantity = _decimal(fact.get('quantity'))
        if (cargo not in allowed_cargo_ids or kind != 'bulk'
                or str(fact.get('weight_unit_code') or '').strip().upper() not in BULK_CONVERSION_UNITS
                or fact.get('tonne_factor') is not None or weight is None
                or (weight == 0 and quantity == 0)):
            continue
        method = _native_id(fact.get('method_id'))
        day = fact.get('business_date')
        day = day.date() if isinstance(day, datetime) else day
        if method is None or type(day) is not date or not start <= day <= end:
            continue
        location = fact.get('vessel_type_code')
        if by_yard and location == 'Ro-Ro Yard':
            continue
        is_gate = location in YARD_TYPES if by_yard else gate_methods is not None and method in gate_methods
        is_quay = (not (by_yard and is_gate) and fact.get('quay_eligible') == 1
                   and (quay_methods is None or method in quay_methods))
        endpoint = 'bulkQuayVolumesCB' if is_quay else 'bulkGateVolumesCB' if is_gate else None
        if endpoint not in selected:
            continue
        if (not (by_yard and is_gate) and scope != 'all_activity'
                and fact.get('production_scope') != scope):
            continue
        if by_yard and is_gate and str(fact.get('quantity_unit_code') or '').strip().upper() == 'GIO':
            continue
        if is_quay and (type(fact.get('physical_voyage')) is not int or fact['physical_voyage'] != 1
                        or _native_id(fact.get('ship_id')) is None
                        or _native_id(fact.get('direction_id')) not in {'1', '2'}):
            continue
        candidates.append(fact)
    return candidates


def production_filter_scope(filters, resources):
    """Keep public filter strings exact; native IDs are never alias-expanded."""
    if filters is None:
        return {}
    allowed = {'handlingMethodId'}
    if all(name in {'contQuayVolumesCB', 'bulkQuayVolumesCB'} for name in resources):
        allowed.add('shipId')
    if (not isinstance(filters, dict) or set(filters) - allowed
            or any(not isinstance(value, str) or not value or len(value) > 255
                   or value != value.strip() for value in filters.values())):
        raise SourceError('SOURCE_FILTER', 'Bộ lọc sản lượng không hợp lệ hoặc không áp dụng cho API đã chọn.')
    return dict(filters)


def _matches_production_filters(fact, filters):
    fields = {'shipId': 'ship_id', 'handlingMethodId': 'method_id'}
    return all(_native_id(fact.get(fields[name])) == value for name, value in filters.items())


def _container_fields(fact, profile, terminal, cargo, size_mode, native_sizes, *,
                      allow_typed_yard_unit=False):
    """Validate independent container dimensions in the same source read.

    A missing weight must not hide a missing size or quantity. Return all
    actionable dimension errors without inventing a default container type.
    """
    problems = []
    quantity_unit = str(fact.get('quantity_unit_code') or '').strip().upper()
    raw_name = fact.get('cargo_name')
    cargo_name = str(raw_name or '').strip().upper()
    size = _mapping(profile, 'container_sizes_by_cargo', terminal).get(cargo)
    size_id = cargo
    typed_size = {'CONT 20E': '20E', 'CONT 20F': '20F'}.get(quantity_unit)
    typed_yard_size = (
        allow_typed_yard_unit and size_mode == 'native_cargo' and raw_name == 'Container'
        and cargo is not None
        and typed_size is not None
        and _native_id(fact.get('resolved_size_cargo_id')) is not None
        and fact.get('resolved_size_cargo_name') == typed_size
        and _native_id(fact.get('cargo_group_id')) is not None
        and _native_id(fact.get('resolved_size_cargo_group_id')) == _native_id(fact.get('cargo_group_id')))
    if quantity_unit != 'CONT' and not typed_yard_size:
        problems.append('CONTAINER_QUANTITY_UNIT_UNCONFIRMED')
    if size_mode == 'native_cargo':
        size = ({'localSzTp': raw_name} if isinstance(raw_name, str)
                and raw_name in DashboardRepository.container_cargo_codes else None)
        if typed_yard_size:
            # These source units explicitly name the container type. Resolve
            # only to a unique active Cargo in the same source/group, retaining
            # the original weight. Do not infer an ISO code or a standard mass.
            cargo_name = typed_size
            size_id = _native_id(fact['resolved_size_cargo_id'])
            size = {'localSzTp': typed_size}
    elif size_mode == 'native_domestic':
        size_id = _native_id(_mapping(profile, 'container_size_ids_by_cargo', terminal).get(cargo))
        size = native_sizes.get(size_id)
        if not size or size.get('sizeCode') != cargo_name[:2]:
            problems.append('CONTAINER_SIZE_RELATION_UNCONFIRMED')
    teu_factor = (1 if cargo_name in {'20F', '20E', '20R'} else
                  2 if cargo_name in {'40F', '40E', '40R', '45F', '45E'} else None)
    if (not isinstance(size, dict) or not size.get('localSzTp')
            or teu_factor is None or size_id is None):
        if 'CONTAINER_SIZE_RELATION_UNCONFIRMED' not in problems:
            problems.append('CONTAINER_SIZE_UNMAPPED')
    quantity = _decimal(fact.get('quantity'))
    if quantity is None or quantity != int(quantity):
        problems.append('CONTAINER_QUANTITY_UNAVAILABLE')
    if problems:
        return {}, problems
    return {'containerSizeId': source_id(terminal, size_id),
            'containerTEU': quantity * teu_factor}, []


def _period_filter_sql(terminal, production_filters=None):
    """Bound exact public filters before row caps in both source-period CTEs."""
    filters = production_filter_scope(production_filters, ['contQuayVolumesCB'])
    # Noncanonical IDs cannot match the exact public native-ID representation.
    # Return an empty scope; LiveReader performs its existing membership check
    # before accepting an empty result. Never let an invalid value reach a SQL
    # numeric cast or silently accept aliases such as 09/9.0/9e0.
    if any(not (value.isascii() and value.isdigit() and not value.startswith('0')
               and len(value) <= 19 and int(value) <= 9223372036854775807)
           for value in filters.values()):
        return ' AND 1=0', ()
    predicates, params = [], []
    if 'shipId' in filters:
        schema = DATABASES[terminal] + '.dbo'
        predicates.append(f'''EXISTS (SELECT 1 FROM {schema}.VesselVoyage filter_voyage
            WHERE filter_voyage.vesselVoyageId=t.vesselVoyageId
              AND filter_voyage.vesselId=CAST(? AS BIGINT))''')
        params.append(filters['shipId'])
    if 'handlingMethodId' in filters:
        predicates.append('t.jobMethodId=CAST(? AS BIGINT)')
        params.append(filters['handlingMethodId'])
    return ''.join(' AND ' + predicate for predicate in predicates), tuple(params)


def _facts_sql(terminal, *, production_filters=None):
    schema = DATABASES[terminal] + '.dbo'
    period_filter, _ = _period_filter_sql(terminal, production_filters)
    repo = DashboardRepository()
    # Read facts independently of berth-history ranking. Combining these joins
    # produced an expensive SQL Server plan even for a single reporting day.
    return f"""WITH period_facts AS (
        SELECT t.tallyShiftId,t.shiftDate,t.cargoId,t.jobMethodId,t.cargoDirectId,
            t.weightNetSum,t.quantityTotalSum,t.consigneeId,t.vesselVoyageId,
            t.quantityUnitId,t.weightUnitId
        FROM {schema}.TallyShift t
        WHERE t.shiftDate>=? AND t.shiftDate<? AND ISNULL(t.rowDeleted,0)=0{period_filter}
    )
    SELECT TOP ({MAX_SOURCE_ROWS + 1})
        t.tallyShiftId AS source_id,t.shiftDate AS business_date,t.cargoId AS cargo_id,
        c.cargoName AS cargo_name,c.cargoGroupId AS cargo_group_id,t.jobMethodId AS method_id,t.cargoDirectId AS direction_id,
        t.weightNetSum AS native_weight,t.quantityTotalSum AS quantity,
        t.consigneeId AS customer_id,s.vesselId AS ship_id,t.vesselVoyageId AS voyage_id,
        vt.vesselTypeCode AS vessel_type_code,qu.baseUnitCode AS quantity_unit_code,
        typed_size.cargo_id AS resolved_size_cargo_id,
        typed_size.cargo_name AS resolved_size_cargo_name,
        typed_size.cargo_group_id AS resolved_size_cargo_group_id,
        u.baseUnitCode AS weight_unit_code,
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
        LEFT JOIN (
            SELECT cargoName AS cargo_name,MIN(cargoId) AS cargo_id,
                MIN(cargoGroupId) AS cargo_group_id
            FROM {schema}.Cargo
            WHERE ISNULL(rowDeleted,0)=0 AND cargoName IN (N'20E',N'20F')
            GROUP BY cargoName HAVING COUNT_BIG(*)=1
        ) typed_size ON qu.baseUnitCode=N'CONT '+typed_size.cargo_name
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
            HAVING COUNT(*)=COUNT(cu.unitValue)
              AND COUNT(DISTINCT cu.unitValue)=1 AND MIN(cu.unitValue)>0
        ) mass_conversion ON mass_conversion.baseUnitId=u.baseUnitId
        ORDER BY t.shiftDate,t.tallyShiftId"""


def _berths_sql(terminal, *, production_filters=None):
    schema = DATABASES[terminal] + '.dbo'
    period_filter, _ = _period_filter_sql(terminal, production_filters)
    berth = initial_berth_query(schema, terminal, selection='period_voyages')
    # Bound WHICH voyages are needed, never WHEN their first berth occurred.
    return f"""WITH period_voyages AS (
        SELECT DISTINCT t.vesselVoyageId
        FROM {schema}.TallyShift t
        WHERE t.shiftDate>=? AND t.shiftDate<? AND ISNULL(t.rowDeleted,0)=0{period_filter}
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
            'excluded_empty_bulk_rows': 0,
            'zero_mass_positive_quantity_rows': 0,
            'zero_mass_positive_quantity_samples': [],
            'zero_mass_positive_quantity_samples_truncated': False,
            'excluded_placeholder_rows': 0,
            'unselected_source_row_count': 0, 'source_errors': [], 'issue_samples': []}


class ProductionSource:
    def __init__(self, query_fn):
        """query_fn(database, static_sql, params) returns bounded dict rows."""
        self.query = query_fn

    def _read_bulk_conversion_support(self, terminal, candidates):
        """Read only opted-in facts and source edges; never query from replays unless opted in."""
        database = DATABASES[terminal]
        identities = [_native_id(fact.get('source_id')) for fact in candidates]
        cargo_set = {_native_id(fact.get('cargo_id')) for fact in candidates}
        if (not identities or len(identities) > MAX_BULK_CONVERSION_FACTS or None in identities
                or len(set(identities)) != len(identities) or None in cargo_set):
            raise SourceError('SOURCE_ROW_LIMIT', 'Số phiếu đối chiếu quy đổi vượt giới hạn hoặc khóa nguồn không hợp lệ.')
        cargo_ids = sorted(cargo_set)
        describe = getattr(self.query, 'describe_table', None)
        if callable(describe):
            metadata = []
            for table in BULK_CONVERSION_COLUMNS:
                columns = describe(database, table)
                if not isinstance(columns, list) or any(not isinstance(row, dict)
                        or not isinstance(row.get('column_name'), str) for row in columns):
                    raise SourceError('SOURCE_SCHEMA', 'Không xác minh được cột quy đổi hàng rời.')
                metadata.extend({'table_name': table, 'column_name': row['column_name']} for row in columns)
        else:
            metadata = self.query(database, BULK_CONVERSION_SCHEMA_SQL, ())
        if not isinstance(metadata, list) or any(not isinstance(row, dict)
                or not isinstance(row.get('table_name'), str) or not isinstance(row.get('column_name'), str)
                for row in metadata):
            raise SourceError('SOURCE_SCHEMA', 'Không xác minh được cấu trúc quy đổi hàng rời.')
        present = {}
        for row in metadata:
            present.setdefault(row['table_name'], set()).add(row['column_name'])
        if any(not fields <= present.get(table, set()) for table, fields in BULK_CONVERSION_COLUMNS.items()):
            raise SourceError('SOURCE_SCHEMA', 'Thiếu cột đối chiếu chứng từ và quy đổi hàng rời.')
        marks = ','.join('CAST(? AS BIGINT)' for _ in identities)
        links = self.query(database, f"""SELECT TOP ({MAX_BULK_CONVERSION_FACTS + 1})
            t.tallyShiftId AS source_id,t.cargoId AS fact_cargo_id,t.vesselVoyageId AS fact_voyage_id,
            t.shiftDate AS fact_business_date,t.weightNetSum AS fact_weight,t.weightUnitId AS fact_weight_unit_id,
            t.rowDeleted AS fact_deleted,m.cargoManifestId AS manifest_id,m.cargoId AS manifest_cargo_id,
            m.vesselVoyageId AS manifest_voyage_id,m.rowDeleted AS manifest_deleted,
            g.goodsSpecificationId AS specification_id,g.rowDeleted AS specification_deleted
            FROM dbo.TallyShift t
            LEFT JOIN dbo.CargoManifest m ON m.cargoManifestId=t.cargoManifestId
            LEFT JOIN dbo.GoodsSpecification g ON g.goodsSpecificationId=m.goodsSpecificationId
            WHERE t.tallyShiftId IN ({marks}) ORDER BY t.tallyShiftId""", tuple(identities))
        if not isinstance(links, list) or len(links) > MAX_BULK_CONVERSION_FACTS:
            raise SourceError('SOURCE_ROW_LIMIT', 'Đối chiếu quy đổi vượt giới hạn đọc.')
        linked = {}
        for row in links:
            identity = _native_id(row.get('source_id')) if isinstance(row, dict) else None
            if identity not in identities or identity in linked:
                raise SourceError('SOURCE_DATA', 'Quan hệ chứng từ quy đổi không có khóa duy nhất.')
            linked[identity] = row
        marks = ','.join('CAST(? AS BIGINT)' for _ in cargo_ids)
        edges = self.query(database, f"""SELECT TOP ({MAX_BULK_CONVERSION_EDGES + 1})
            cu.conversionUnitId AS conversion_id,cu.cargoId AS cargo_id,
            cu.goodsSpecificationId AS specification_id,cu.unitValue AS factor,
            cu.baseUnitId AS source_unit_id,cu.unitId AS target_unit_id,
            cu.rowDeleted AS edge_deleted,cu.rowInvisible AS edge_hidden,
            cu.createTime AS created_at,cu.updateTime AS updated_at,
            su.baseUnitCode AS source_unit_code,su.TONE AS source_unit_tone,su.rowDeleted AS source_unit_deleted,
            tu.baseUnitCode AS target_unit_code,tu.TONE AS target_unit_tone,tu.rowDeleted AS target_unit_deleted
            FROM dbo.ConversionUnit cu
            LEFT JOIN dbo.BaseUnit su ON su.baseUnitId=cu.baseUnitId
            LEFT JOIN dbo.BaseUnit tu ON tu.baseUnitId=cu.unitId
            WHERE (cu.cargoId IN ({marks}) OR cu.cargoId=0)
              AND ((su.baseUnitCode=N'M3' AND tu.baseUnitCode=N'KG')
                OR (su.baseUnitCode=N'KG' AND tu.baseUnitCode=N'TAN')
                OR (su.baseUnitCode=N'TAN' AND tu.baseUnitCode=N'M3')
                OR (su.baseUnitCode=N'VỎ BỊCH' AND tu.baseUnitCode=N'TAN'))
            ORDER BY cu.cargoId,cu.goodsSpecificationId,cu.conversionUnitId""", tuple(cargo_ids))
        if (not isinstance(edges, list) or len(edges) > MAX_BULK_CONVERSION_EDGES
                or any(not isinstance(row, dict) for row in edges)):
            raise SourceError('SOURCE_DATA', 'Không đọc được danh mục quy đổi duy nhất trong giới hạn.')
        return linked, edges

    def _read_terminal(self, terminal, start, end, *, production_filters=None, include_berths=True):
        database = DATABASES[terminal]
        # Yard-only gate selection does not use an initial berth assignment.
        # Keep its actual fact/location schema mandatory while avoiding a
        # dependency on unavailable or slow vessel-berth metadata/history.
        required_columns = (REQUIRED_COLUMNS if include_berths else
                            {table: columns for table, columns in REQUIRED_COLUMNS.items()
                             if table not in {'DoBerth', 'Berth'}})
        describe = getattr(self.query, 'describe_table', None)
        if callable(describe):
            # Runtime can inspect one known table using SELECT TOP (0), avoiding
            # the expensive multi-table INFORMATION_SCHEMA plan on SmartTOS.
            # Production expressions already define unit/type semantics; this
            # preflight establishes the required column names only.
            metadata = []
            for table in required_columns:
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
        missing = [table + '.' + column for table, columns in required_columns.items()
                   for column in sorted(columns - present.get(table, set()))]
        if missing:
            raise SourceError('SOURCE_SCHEMA', 'Thiếu cột nguồn đã xác minh: ' + ', '.join(missing))
        _, filter_params = _period_filter_sql(terminal, production_filters)
        period_params = (start, end + timedelta(days=1), *filter_params)
        facts = self.query(database, _facts_sql(terminal, production_filters=production_filters), period_params)
        if not isinstance(facts, list) or any(not isinstance(item, dict) for item in facts):
            raise SourceError('SOURCE_DATA', 'Kết quả truy vấn sản lượng không phải danh sách bản ghi.')
        if len(facts) > MAX_SOURCE_ROWS:
            raise SourceError('SOURCE_ROW_LIMIT', 'Kỳ đọc nguồn quá lớn; chia thành kỳ ngắn hơn.')
        if production_filters:
            # Keep an exact representation check after the SQL pushdown, before
            # source-row validation, aggregation and reference discovery.
            facts = [fact for fact in facts if _matches_production_filters(fact, production_filters)]
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
        if include_berths and voyages:
            berths = self.query(database, _berths_sql(terminal, production_filters=production_filters), period_params)
            if not isinstance(berths, list) or any(not isinstance(row, dict) for row in berths):
                raise SourceError('SOURCE_DATA', 'Kết quả đối chiếu cầu không phải danh sách bản ghi.')
            if len(berths) > MAX_BERTH_ROWS:
                raise SourceError('SOURCE_ROW_LIMIT', 'Số chuyến đối chiếu cầu vượt giới hạn đọc an toàn.')
            for row in berths:
                voyage = _native_id(row.get('voyage_id'))
                if production_filters and voyage is not None and voyage not in voyages:
                    # Only histories of the filtered facts are dependencies,
                    # including with injected providers or concurrent changes.
                    continue
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

    def extract(self, start: date, end: date, profile: dict | None = None, *, resources=None,
                production_filters=None):
        if (type(start) is not date or type(end) is not date or end < start
                or end == date.max or (end - start).days >= 31):
            raise SourceError('SOURCE_RANGE', 'Mỗi lần đọc nguồn tối đa 31 ngày liên tiếp, ngày kết thúc phải trước 99991231.')
        selected = list(RESOURCES) if resources is None else resources
        if (not isinstance(selected, (list, tuple)) or not selected
                or any(not isinstance(name, str) or name not in RESOURCES for name in selected)
                or len(set(selected)) != len(selected)):
            raise SourceError('SOURCE_RESOURCE', 'Phạm vi API sản lượng không hợp lệ.')
        selected_set = set(selected)
        filters = production_filter_scope(production_filters, selected)
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
        bulk_conversion_ids = _bulk_conversion_ids(profile)
        gate_mode = profile.get('gate_selection', 'methods')
        if gate_mode not in {'methods', 'vessel_type'}:
            raise SourceError('SOURCE_PROFILE', 'Cách chọn dữ liệu cổng/bãi không hợp lệ.')
        by_yard = gate_mode == 'vessel_type'
        include_berths = not (by_yard and selected_set <= {'contGateVolumesCB', 'bulkGateVolumesCB'})
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
                    'quantityUnit': fact.get('quantity_unit_code'),
                    'weightUnit': fact.get('weight_unit_code'),
                    'nativeWeight': fact.get('native_weight'), 'quantity': fact.get('quantity'),
                    'tonneFactor': fact.get('tonne_factor')})
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
                facts = self._read_terminal(terminal, start, end, production_filters=filters,
                                            include_berths=include_berths)
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
            conversion_candidates = _bulk_conversion_candidates(
                facts, start=start, end=end, allowed_cargo_ids=bulk_conversion_ids.get(terminal, set()),
                cargo_kinds=cargo_kinds.get(terminal, {}), group_kinds=group_kinds.get(terminal, {}),
                selected=selected_set, by_yard=by_yard, quay_methods=quay_methods,
                gate_methods=gate_methods, scope=scope) if bulk_conversion_ids.get(terminal) else []
            conversion_support = None
            conversion_error = None
            conversion_errors_recorded = set()
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
                day = fact.get('business_date')
                if isinstance(day, datetime):
                    day = day.date()
                empty_yard_method = (by_yard and is_gate
                    and (fact.get('method_id') is None or _decimal(fact.get('method_id')) == 0))
                if (_decimal(fact.get('cargo_id')) == 0
                        and _decimal(fact.get('quantity')) == 0
                        and _decimal(fact.get('native_weight')) == 0
                        and (method is not None or empty_yard_method)
                        and type(day) is date and start <= day <= end
                        and (not is_quay or (_native_id(fact.get('ship_id')) is not None
                                            and _native_id(fact.get('direction_id')) in {'1', '2'}))):
                    # SmartTOS can insert a still-empty activity slot with the
                    # explicit cargo ID 0 and both reported measures exactly 0.
                    # It represents no cargo movement. Missing cargo metadata
                    # for a positive ID, NULL measures and nonzero measures are
                    # never treated as this placeholder or silently discarded.
                    for endpoint in affected:
                        results[endpoint]['excluded_placeholder_rows'] += 1
                    continue
                if kind not in {'container', 'bulk'}:
                    for name in affected:
                        flag(name, 'CARGO_KIND_UNMAPPED', fact, terminal)
                    continue
                name = ('cont' if kind == 'container' else 'bulk') + side + 'VolumesCB'
                if name not in selected_set:
                    continue
                results[name]['source_row_count'] += 1
                if method is None:
                    if (empty_yard_method and cargo is not None
                            and (kind == 'bulk' or size_mode == 'native_cargo')
                            and type(day) is date and start <= day <= end
                            and _decimal(fact.get('quantity')) == 0
                            and _decimal(fact.get('native_weight')) == 0):
                        # A yard slot with both measures explicitly zero has no
                        # production row to attach a method to. Retain a count
                        # instead of blocking unrelated movements in the period.
                        # NULL/nonzero measures and malformed IDs still block.
                        counter = ('excluded_empty_container_rows' if kind == 'container'
                                   else 'excluded_empty_bulk_rows')
                        results[name][counter] += 1
                        continue
                    flag(name, 'METHOD_ID_UNAVAILABLE', fact, terminal)
                    continue
                if type(day) is not date or not start <= day <= end:
                    flag(name, 'SOURCE_DATE_INVALID', fact, terminal)
                    continue
                if is_quay:
                    ship = _native_id(fact.get('ship_id'))
                    direction = _native_id(fact.get('direction_id'))
                    if not ship or direction not in {'1', '2'}:
                        flag(name, 'PHYSICAL_SHIP_OR_DIRECTION_UNAVAILABLE', fact, terminal)
                        continue
                weight = _decimal(fact.get('native_weight'))
                weight_unit = str(fact.get('weight_unit_code') or '').strip().upper()
                if (by_yard and is_gate and cargo is not None and weight_unit == 'GIO'
                        and not quantity_unit and _decimal(fact.get('quantity')) == 0
                        and weight is not None):
                    # Some yard service hours are recorded in weightNetSum,
                    # with GIO as its actual unit and explicit zero quantity.
                    # Do not discard a positive/unknown cargo quantity or extend
                    # this source-specific rule to physical quay throughput.
                    results[name].setdefault('excluded_time_rows', 0)
                    results[name]['excluded_time_rows'] += 1
                    continue
                if (kind == 'bulk' and cargo is not None
                        and _decimal(fact.get('quantity')) == 0 and weight == 0):
                    # Both recorded measures explicitly confirm no movement.
                    # Do not require a mass conversion for this empty slot, or
                    # extend the exclusion to missing/invalid/nonzero measures.
                    results[name]['excluded_empty_bulk_rows'] += 1
                    continue
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
                container_fields, container_problems = ({}, [])
                if kind == 'container':
                    container_fields, container_problems = _container_fields(
                        fact, profile, terminal, cargo, size_mode, native_sizes,
                        allow_typed_yard_unit=by_yard and is_gate)
                    for code in container_problems:
                        flag(name, code, fact, terminal)
                factor = _decimal(fact.get('tonne_factor'))
                if (kind == 'bulk' and fact.get('tonne_factor') is None and weight is not None
                        and weight_unit in BULK_CONVERSION_UNITS and cargo in bulk_conversion_ids.get(terminal, set())):
                    if conversion_support is None and conversion_error is None:
                        try:
                            conversion_support = self._read_bulk_conversion_support(terminal, conversion_candidates)
                        except Exception as exc:
                            conversion_error = (exc.code if isinstance(exc, SourceError) else
                                exc.code if isinstance(exc, CorporateError) and exc.code in {
                                    'SOURCE_TIMEOUT', 'SOURCE_UNAVAILABLE', 'SOURCE_SCHEMA', 'SOURCE_ROW_LIMIT'}
                                else 'SOURCE_UNAVAILABLE')
                    if conversion_error is not None:
                        flag(name, conversion_error, fact, terminal)
                        if name not in conversion_errors_recorded:
                            results[name]['source_errors'].append({'terminal': terminal, 'code': conversion_error,
                                'message': 'Không đọc được nguồn đối chiếu quy đổi hàng rời; chưa công bố dữ liệu mới.'})
                            conversion_errors_recorded.add(name)
                    elif conversion_support is not None:
                        links, edges = conversion_support
                        factor = _bulk_conversion_factor(fact, links.get(native), edges)
                if weight is None or factor is None or factor <= 0:
                    flag(name, 'WEIGHT_OR_UNIT_UNAVAILABLE', fact, terminal)
                    continue
                tonnes = weight * factor
                if tonnes > Decimal('1000000000000000'):
                    flag(name, 'WEIGHT_OUT_OF_RANGE', fact, terminal)
                    continue
                if container_problems:
                    continue
                row = {'reportDate': report_date.strftime('%Y%m%d'), 'companyId': 'CNT',
                       'finishDate': day.strftime('%Y%m%d'),
                       'handlingMethodId': source_id(terminal, method)}
                if is_quay:
                    row['shipId'] = source_id(terminal, ship)
                    row['classId' if kind == 'container' else 'shipClassId'] = source_id(terminal, direction)
                if kind == 'container':
                    row.update(originId=None, containerWeight=tonnes,
                               containerOperatorId=None, **container_fields)
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
                        flag(name, 'CARGO_TYPE_UNMAPPED', fact, terminal)
                        continue
                    row.update(cargoTypeId=cargo_type, cargoCategoryId=source_id(terminal, cargo),
                               bulkOriginId=None, bulkWeight=tonnes)
                    if is_quay:
                        row['shipAgentId'] = None
                    else:
                        customer = _native_id(fact.get('customer_id'))
                        row['customerCode'] = source_id(terminal, customer) if customer else None
                    sums = ('bulkWeight',)
                    quantity = _decimal(fact.get('quantity'))
                    if weight == 0 and quantity is not None and quantity > 0:
                        # Private observation after every per-fact validation,
                        # before aggregation can merge zero and positive mass.
                        # Zero is not missing mass: do not infer a replacement,
                        # exclude the row, or turn this observation into a blocker.
                        item = results[name]
                        item['zero_mass_positive_quantity_rows'] += 1
                        samples = item['zero_mass_positive_quantity_samples']
                        if len(samples) < MAX_ZERO_MASS_SAMPLES:
                            samples.append({
                                'code': 'BULK_ZERO_MASS_POSITIVE_QUANTITY', 'terminal': terminal,
                                'sourceId': native, 'date': day.isoformat(),
                                'cargoId': cargo, 'methodId': method,
                                'quantityUnit': quantity_unit if 0 < len(quantity_unit) <= 32 else None,
                                'weightUnit': weight_unit if 0 < len(weight_unit) <= 32 else None,
                                'nativeWeight': weight, 'quantity': quantity,
                            })
                        else:
                            item['zero_mass_positive_quantity_samples_truncated'] = True
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
            if item['excluded_placeholder_rows']:
                item['warnings'].append(
                    f"Loại {item['excluded_placeholder_rows']} phiếu chưa nhập hàng có mã hàng, số lượng và trọng lượng nguồn đều bằng 0; không bỏ phiếu có chỉ tiêu thiếu hoặc khác 0."
                )
            if item['excluded_empty_bulk_rows']:
                item['warnings'].append(
                    f"Loại {item['excluded_empty_bulk_rows']} phiếu hàng rời đã xác định mã hàng có số lượng và trọng lượng nguồn đều bằng 0; không bỏ phiếu có chỉ tiêu thiếu hoặc khác 0."
                )
            if item['zero_mass_positive_quantity_rows']:
                item['warnings'].append(
                    f"Có {item['zero_mass_positive_quantity_rows']} phiếu hàng rời có trọng lượng nguồn bằng 0 nhưng số lượng dương; giữ nguyên 0 tấn theo nguồn, cần đối chiếu nghiệp vụ trước khi thay đổi cách tính."
                )
            if item.get('excluded_time_rows'):
                item['warnings'].append(
                    f"Loại {item['excluded_time_rows']} phiếu tính giờ theo đơn vị GIO của số lượng, hoặc trọng lượng nguồn tại kho/bãi khi số lượng bằng 0 và chưa có đơn vị số lượng; không quy đổi giờ thành tấn."
                )
        return {name: results[name] for name in selected}

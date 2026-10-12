"""Bounded admin diagnostics. Never copy source messages or free-text values."""
from datetime import date
from decimal import Decimal, InvalidOperation
import re

from .registry import MODELS


MAX_SAMPLES = 50
TERMINALS = frozenset({'cua_lo', 'ben_thuy'})
UNITS = frozenset({'TAN', 'KG', 'M3', 'M2', 'CAI', 'GÀU', 'GIO', 'CONT', 'LÓNG', 'CỌC'})
CODES = frozenset({
    'DATE_BASIS_UNCONFIRMED', 'PRODUCTION_SCOPE_UNCONFIRMED',
    'QUAY_METHODS_UNCONFIRMED', 'GATE_METHODS_UNCONFIRMED',
    'CARGO_KIND_UNMAPPED', 'CARGO_TYPE_UNMAPPED',
    'CONTAINER_SIZE_RELATION_UNCONFIRMED', 'CONTAINER_SIZE_UNMAPPED',
    'CONTAINER_QUANTITY_UNIT_UNCONFIRMED', 'WEIGHT_OR_UNIT_UNAVAILABLE',
    'CONTAINER_QUANTITY_UNAVAILABLE', 'WEIGHT_OUT_OF_RANGE', 'AGGREGATE_OUT_OF_RANGE',
    'PHYSICAL_SHIP_OR_DIRECTION_UNAVAILABLE', 'METHOD_ID_UNAVAILABLE', 'SOURCE_DATE_INVALID',
    'SOURCE_REFERENCE_NOT_FOUND', 'SOURCE_ID_CONFLICT', 'SOURCE_UNAVAILABLE',
    'SOURCE_TIMEOUT', 'SOURCE_SCHEMA', 'SOURCE_ROW_LIMIT', 'SOURCE_DATA',
    'SOURCE_TERMINALS_INCOMPLETE', 'SOURCE_PROFILE_UNAPPROVED',
    'SOURCE_CHANGE_DATE_MISSING', 'MAPPING_REQUIRED', 'SOURCE_DATA_NOT_READY',
})


def _count(value):
    return value if type(value) is int and 0 <= value <= 1_000_000 else None


def _identity(value, *, zero=False):
    if type(value) is int:
        if not 0 <= value <= 9_999_999_999_999_999_999:
            return None
        value = str(value)
    if not isinstance(value, str) or not re.fullmatch(r'(?:0|[1-9][0-9]{0,18})', value):
        return None
    return value if zero or value != '0' else None


def _day(value):
    if not isinstance(value, str) or not re.fullmatch(r'[0-9]{4}-[0-9]{2}-[0-9]{2}', value):
        return None
    try:
        return date.fromisoformat(value).isoformat()
    except ValueError:
        return None


def _measure(value):
    if type(value) not in (int, float, str, Decimal):
        return None
    if type(value) is int and not 0 <= value <= 10**15:
        return None
    text = str(value)
    if len(text) > 64:
        return None
    try:
        number = Decimal(text)
        if (not number.is_finite() or not 0 <= number <= Decimal('1e15')
                or number.as_tuple().exponent < -38):
            return None
        if number == 0:
            return '0'
        result = format(number, 'f')
        return result.rstrip('0').rstrip('.') if '.' in result else result
    except InvalidOperation:
        return None


def sanitize_diagnostics(value):
    """Apply the same allowlist at creation and the authenticated HTTP boundary."""
    if (not isinstance(value, dict) or not isinstance(value.get('resource'), str)
            or value['resource'] not in MODELS):
        return None
    blockers = []
    raw_blockers = value.get('blockers', [])
    if isinstance(raw_blockers, list):
        seen = set()
        for item in raw_blockers[:len(CODES) * 2]:
            if not isinstance(item, dict):
                continue
            code = item.get('code')
            if isinstance(code, str) and code in CODES and code not in seen:
                blockers.append({'code': code, 'count': _count(item.get('count'))})
                seen.add(code)
    if not blockers:
        return None
    allowed = {item['code'] for item in blockers}
    samples = []
    raw_samples = value.get('samples', [])
    if isinstance(raw_samples, list):
        for item in raw_samples[:MAX_SAMPLES]:
            if not isinstance(item, dict):
                continue
            code, terminal = item.get('code'), item.get('terminal')
            if not isinstance(code, str) or code not in allowed or not isinstance(terminal, str) or terminal not in TERMINALS:
                continue
            sample = {'code': code, 'terminal': terminal}
            for key in ('sourceId', 'cargoId', 'methodId', 'referenceId'):
                identity = _identity(item.get(key), zero=key == 'cargoId')
                if identity is not None:
                    sample[key] = identity
            day = _day(item.get('date'))
            if day is not None:
                sample['date'] = day
            for key in ('weightUnit', 'quantityUnit'):
                unit = item.get(key)
                if isinstance(unit, str) and len(unit) <= 20 and unit.strip().upper() in UNITS:
                    sample[key] = unit.strip().upper()
            for key in ('nativeWeight', 'quantity'):
                measure = _measure(item.get(key))
                if measure is not None:
                    sample[key] = measure
            samples.append(sample)
    result = {'resource': value['resource'], 'blockers': blockers, 'samples': samples,
              'samplesTruncated': value.get('samplesTruncated') is True
                  or isinstance(raw_samples, list) and len(raw_samples) > len(samples)}
    if 'SOURCE_ID_CONFLICT' in allowed:
        conflicts = value.get('conflictIds', [])
        result['conflictIds'] = list(dict.fromkeys(identity for item in
            (conflicts[:MAX_SAMPLES] if isinstance(conflicts, list) else [])
            if (identity := _identity(item)) is not None))
        result['conflictCount'] = _count(value.get('conflictCount'))
    return result


def dataset_diagnostics(resource, dataset):
    counts = dataset.get('issue_counts', {})
    counts = counts if isinstance(counts, dict) else {}
    raw_blockers = dataset.get('blockers', [])
    blockers = []
    if isinstance(raw_blockers, list):
        for item in raw_blockers[:len(CODES) * 2]:
            code = item.get('code') if isinstance(item, dict) else item
            if isinstance(code, str) and code in CODES:
                blockers.append({'code': code, 'count': counts.get(code)})
    samples = dataset.get('issue_samples', [])
    samples = list(samples[:MAX_SAMPLES + 1]) if isinstance(samples, list) else []
    # Catalog/connection failures can identify a terminal, but not a source row.
    # Do not infer missing IDs from the set requested from that terminal.
    if isinstance(raw_blockers, list):
        samples.extend({'code': item.get('code'), 'terminal': item.get('terminal')}
            for item in raw_blockers[:len(CODES) * 2]
            if isinstance(item, dict) and 'terminal' in item)
    source_errors = dataset.get('source_errors', [])
    if isinstance(source_errors, list):
        samples.extend({'code': item.get('code'), 'terminal': item.get('terminal')}
            for item in source_errors[:len(TERMINALS)] if isinstance(item, dict))
    total = sum(count for item in blockers if (count := _count(item['count'])) is not None)
    return sanitize_diagnostics({'resource': resource, 'blockers': blockers,
        'samples': samples, 'samplesTruncated': total > len(samples),
        'conflictIds': dataset.get('identity_conflicts'),
        'conflictCount': dataset.get('identity_conflict_count')})

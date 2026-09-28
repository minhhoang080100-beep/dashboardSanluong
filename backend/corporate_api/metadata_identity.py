"""Explicit, lossless date variants for otherwise identical operation masters.

No policy is enabled by default. Business values and source deletion flags must
agree. Private timestamp pairs preserve the receiver's created OR modified filter.
"""
from datetime import datetime, timedelta, timezone
from pydantic import TypeAdapter

from .operation_contracts import OPERATION_MODELS

POLICY = 'source_date_variants_v1'
VARIANTS_KEY = '_sourceDateVariants'
PAIR_FIELDS = ('createdDate', 'modifiedDate')
MAX_VARIANTS = 2  # At most the two explicitly supported source databases.
TZ = timezone(timedelta(hours=7))
_DATETIME = TypeAdapter(datetime)


def policies(profile):
    if profile is not None and not isinstance(profile, dict):
        raise ValueError('Invalid operation metadata merge profile.')
    configured = (profile or {}).get('operation_metadata_merge', {})
    if (not isinstance(configured, dict) or set(configured) - set(OPERATION_MODELS)
            or any(value != POLICY for value in configured.values())):
        raise ValueError('Invalid operation metadata merge policy.')
    if configured and (profile or {}).get('approved') is not True:
        raise ValueError('Operation metadata merge policy requires an approved profile.')
    return configured


def timestamp(value):
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError('Invalid source date variant timestamp.')
    try:
        parsed = _DATETIME.validate_python(value)
        if _DATETIME.dump_python(parsed, mode='json') != value:
            raise ValueError('Noncanonical date variant.')
        return parsed
    except ValueError:
        raise ValueError('Invalid source date variant timestamp.') from None


def day(value):
    parsed = timestamp(value)
    if parsed is None:
        return None
    if parsed.tzinfo is not None:
        parsed = parsed.astimezone(TZ)
    return parsed.strftime('%Y%m%d')


def _stamp_key(value):
    parsed = timestamp(value)
    if parsed is None:
        return 0
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=TZ)
    # Integer microseconds avoid overflowing datetime at year 1/9999 offsets.
    local = ((parsed.toordinal() * 86400 + parsed.hour * 3600 + parsed.minute * 60
              + parsed.second) * 1000000 + parsed.microsecond)
    return local - int(parsed.utcoffset().total_seconds() * 1000000)


def pair_key(pair):
    return (_stamp_key(pair['modifiedDate'] or pair['createdDate']),
            _stamp_key(pair['createdDate']),
            pair['createdDate'] or '', pair['modifiedDate'] or '')


def validate_variants(resource, row, policy=None):
    """Validate the private row marker independently at each publication gate."""
    if VARIANTS_KEY not in row:
        return None
    if resource not in OPERATION_MODELS or policy != POLICY:
        raise ValueError('Source date variants require explicit resource metadata merge opt-in.')
    values = row[VARIANTS_KEY]
    if not isinstance(values, list) or not 2 <= len(values) <= MAX_VARIANTS:
        raise ValueError('Invalid source date variant count.')
    seen = set()
    pairs = []
    for pair in values:
        if not isinstance(pair, dict) or set(pair) != set(PAIR_FIELDS):
            raise ValueError('Source date variants may contain only original timestamp pairs.')
        normalized = {field: pair[field] for field in PAIR_FIELDS}
        for value in normalized.values():
            timestamp(value)
        if not any(value is not None for value in normalized.values()):
            raise ValueError('Source date variant has no actual change date.')
        key = tuple(normalized.values())
        if key in seen:
            raise ValueError('Duplicate source date variant.')
        seen.add(key)
        pairs.append(normalized)
    if tuple(row.get(field) for field in PAIR_FIELDS) not in seen:
        raise ValueError('Export timestamps must be one original source date pair.')
    return sorted(pairs, key=pair_key)


def merge_rows(resource, left, right, policy):
    """Return a merged row only if all non-date public fields agree exactly."""
    if policy != POLICY:
        return None
    ignored = {'reportDate', 'createdDate', 'modifiedDate', '_changedDate', VARIANTS_KEY}
    if {k: v for k, v in left.items() if k not in ignored} != {
            k: v for k, v in right.items() if k not in ignored}:
        return None
    pairs = []
    for row in (left, right):
        original = validate_variants(resource, row, policy)
        for pair in original or [{field: row.get(field) for field in PAIR_FIELDS}]:
            if pair not in pairs:
                pairs.append(pair)
    if len(pairs) < 2:
        return left
    selected = max(pairs, key=pair_key)
    result = {**left, **selected, VARIANTS_KEY: pairs}
    result[VARIANTS_KEY] = validate_variants(resource, result, policy)
    result['_changedDate'] = day(selected['modifiedDate'] or selected['createdDate'])
    return result


def matching_pair(pairs, start, end):
    matches = [pair for pair in pairs if any(value is not None and start <= day(value) <= end
                                           for value in pair.values())]
    return max(matches, key=pair_key) if matches else None

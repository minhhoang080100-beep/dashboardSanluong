"""Explicit native-value completion, limited to matching container type records.

This never infers TEU from names, ISO prefixes, billing factors or formulas.
The policy is off by default. A known source value may fill a null only when
all other business fields (including deletion flags) match exactly. Different
source dates additionally require the existing lossless date-variant policy.
"""
from .metadata_identity import merge_rows
POLICY = 'matching_native_teu_v1'
RESOURCE = 'oprt.contSizeType'
FIELDS = frozenset({'teu'})


def policies(profile):
    if profile is not None and not isinstance(profile, dict):
        raise ValueError('Invalid operation attribute completion profile.')
    configured = (profile or {}).get('operation_attribute_completion', {})
    if (not isinstance(configured, dict) or set(configured) - {RESOURCE}
            or any(value != POLICY for value in configured.values())):
        raise ValueError('Invalid operation attribute completion policy.')
    if configured and (profile or {}).get('approved') is not True:
        raise ValueError('Attribute completion requires an approved profile.')
    return configured


def complete_rows(resource, left, right, policy, date_policy=None):
    if resource != RESOURCE or policy != POLICY:
        return None
    # The extractor supplies one raw normalized row from each source. Never
    # complete already-merged rows or bypass date-variant validation via equality.
    if any('_sourceDateVariants' in row for row in (left,right)):
        return None
    ignored = FIELDS | {'createdDate', 'modifiedDate'}
    comparable = lambda row: {k: v for k, v in row.items() if not k.startswith('_') and k not in ignored}
    if comparable(left) != comparable(right):
        return None
    # Opaque extensions are not a source of evidence for completion.
    if any(key.startswith('_') and key not in {'_changedDate','_sourceDateVariants'}
           for row in (left,right) for key in row):
        return None
    first_row, second_row, completed = dict(left), dict(right), []
    for field in sorted(FIELDS):
        first, second = left.get(field), right.get(field)
        if first is not None and second is not None and first != second:
            return None
        if (first is None) != (second is None):
            first_row[field] = second_row[field] = second if first is None else first
            completed.append(field)
    if not completed:
        return None
    if first_row == second_row:
        return first_row, completed
    result = merge_rows(resource, first_row, second_row, date_policy)
    return (result, completed) if result is not None else None

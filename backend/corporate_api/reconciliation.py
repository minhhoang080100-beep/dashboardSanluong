"""Private delivery diagnostics. No source secrets or extra public JSON fields."""
from collections import Counter, defaultdict
from datetime import datetime
from decimal import Decimal

from pydantic import ValidationError

from .contracts import PRODUCTION, MODELS as S_MODELS, IDENTITY as S_IDENTITY
from .catalog_source import SOURCES
from .operation_contracts import OPERATION_IDENTITY, OPERATION_MODELS, OPERATION_REFERENCES

NULL_FIELDS = {
    'contQuayVolumesCB': ('originId', 'containerOperatorId', 'shipOperatorId'),
    'contGateVolumesCB': ('originId', 'containerOperatorId'),
    'bulkQuayVolumesCB': ('bulkOriginId', 'shipAgentId'),
    'bulkGateVolumesCB': ('bulkOriginId', 'customerCode'),
}

# S-domain references have different targets from identically named operation
# fields. Keep these aligned with publication; this read-only check never queries
# the published store, so omitted dependencies remain unproven.
S_REFERENCES = {
    'shipId': 'shipDetails', 'shipAgentId': 'customers', 'shipOperatorId': 'customers',
    'containerOperatorId': 'customers', 'customerCode': 'customers',
    'handlingMethodId': 'handlingMethodList', 'classId': 'class', 'shipClassId': 'class',
    'originId': 'origins', 'bulkOriginId': 'origins', 'containerSizeId': 'containerSize',
    'cargoTypeId': 'cargoType', 'cargoCategoryId': 'cargoCategory', 'cargoParentId': 'cargoCategory',
}


def _s_references(resource):
    return {field: target for field, target in S_REFERENCES.items()
            if field in S_MODELS[resource].model_fields and field != S_IDENTITY.get(resource)}


def _day(value):
    if not isinstance(value, str) or len(value) != 8 or not value.isascii() or not value.isdigit():
        return None
    try:
        return datetime.strptime(value, '%Y%m%d').date()
    except ValueError:
        return None


def _s_validation(resource, dataset, preview):
    """Count contract, identity and period errors without exposing source rows."""
    counts = Counter()
    identities = set()
    valid_rows = []
    start, end = _day(preview.get('startDate')), _day(preview.get('endDate'))
    period_valid = bool(start and end and start <= end)
    for row in dataset['rows']:
        if not isinstance(row, dict):
            counts['invalidRows'] += 1
            continue
        valid = True
        try:
            S_MODELS[resource].model_validate(
                {key: value for key, value in row.items() if not key.startswith('_')})
        except (ValidationError, ValueError, TypeError):
            counts['invalidRows'] += 1
            valid = False
        invalid_date = _day(row.get('reportDate')) is None
        if resource in PRODUCTION:
            finish = _day(row.get('finishDate'))
            if finish is None:
                invalid_date = True
            elif not period_valid or not start <= finish <= end:
                counts['rowsOutsidePeriod'] += 1
                valid = False
            if row.get('companyId') != preview['companyId']:
                counts['otherCompanyRows'] += 1
                valid = False
        else:
            identity = row.get(S_IDENTITY[resource])
            if not isinstance(identity, str) or not identity.strip():
                counts['rowsWithoutIdentity'] += 1
                valid = False
            elif identity in identities:
                counts['duplicateIdentityRows'] += 1
                valid = False
            else:
                identities.add(identity)
        if invalid_date:
            counts['invalidDateRows'] += 1
            valid = False
        if valid:
            valid_rows.append(row)
    coverage_ready = (period_valid and dataset.get('coverage') == [
        [preview.get('startDate'), preview.get('endDate')]]) if resource in PRODUCTION else None
    summary = {key: counts[key] for key in ('invalidRows', 'invalidDateRows', 'rowsOutsidePeriod',
                                           'otherCompanyRows', 'rowsWithoutIdentity', 'duplicateIdentityRows')}
    return valid_rows, {**summary, 'schemaReady': not any(summary.values()),
                        'coverageReady': coverage_ready}


def _operation_validation(resource, dataset, preview):
    """Validate standalone operation rows, even when no other resource uses them."""
    counts = Counter()
    identities = set()
    valid_rows = []
    for row in dataset['rows']:
        if not isinstance(row, dict):
            counts['invalidRows'] += 1
            continue
        valid = True
        try:
            OPERATION_MODELS[resource].model_validate(
                {key: value for key, value in row.items() if not key.startswith('_')})
        except ValidationError as exc:
            counts['invalidRows'] += 1
            if any(error['loc'] and error['loc'][0] in ('reportDate', 'createdDate', 'modifiedDate')
                   for error in exc.errors(include_input=False)):
                counts['invalidDateRows'] += 1
            valid = False
        except (ValueError, TypeError):
            counts['invalidRows'] += 1
            valid = False
        if not (row.get('createdDate') or row.get('modifiedDate')):
            counts['rowsMissingChangeDate'] += 1
            valid = False
        if row.get('companyId') != preview['companyId']:
            counts['otherCompanyRows'] += 1
            valid = False
        identity = row.get(OPERATION_IDENTITY[resource])
        if not isinstance(identity, str) or not identity.strip():
            counts['rowsWithoutIdentity'] += 1
            valid = False
        elif identity in identities:
            counts['duplicateIdentityRows'] += 1
            valid = False
        else:
            identities.add(identity)
        if valid:
            valid_rows.append(row)
    summary = {key: counts[key] for key in ('invalidRows', 'invalidDateRows', 'rowsOutsidePeriod',
                                           'otherCompanyRows', 'rowsWithoutIdentity',
                                           'duplicateIdentityRows', 'rowsMissingChangeDate')}
    # Operation catalogs are complete snapshots; their change dates may predate
    # the requested period. Production coverage intervals do not apply here.
    return valid_rows, {**summary, 'schemaReady': not any(summary.values()), 'coverageReady': None}


def unaccepted_nulls(resource, rows, profile):
    policy = profile.get('accepted_null_fields', {})
    if not isinstance(policy, dict) or set(policy) - set(NULL_FIELDS):
        raise ValueError('Invalid receiver null-field policy')
    for name, fields in policy.items():
        if not isinstance(fields, list) or any(field not in NULL_FIELDS[name] for field in fields):
            raise ValueError('Invalid receiver null-field policy')
    accepted = policy.get(resource, [])
    return {field: sum(not isinstance(row, dict) or row.get(field) is None for row in rows)
            for field in NULL_FIELDS.get(resource, ())
            if field not in accepted and any(not isinstance(row, dict) or row.get(field) is None for row in rows)}


def _source_counts(dataset, profile):
    """Keep unknown source sizes distinct from a verified empty source."""
    expected = profile.get('terminals', list(SOURCES))
    diagnostics = dataset.get('source_diagnostics')
    if diagnostics is not None:
        counts = {item['terminal']: item.get('raw_row_count') for item in diagnostics}
        unknown = [terminal for terminal in expected if counts.get(terminal) is None]
        known = sum(value for terminal, value in counts.items()
                    if terminal in expected and value is not None)
        basis = 'raw_rows'
    elif 'source_row_count' in dataset:
        # S production currently counts selected source facts, not every SQL row.
        known = dataset['source_row_count']
        unknown = sorted({item['terminal'] for item in dataset.get('source_errors', [])
                          if item.get('terminal') in expected})
        if known is None:
            unknown = list(expected)
        basis = 'selected_rows'
    else:
        counts = {item['terminal']: item.get('row_count') for item in dataset.get('source_coverage', [])}
        unknown = [terminal for terminal in expected if counts.get(terminal) is None]
        known = sum(value for terminal, value in counts.items()
                    if terminal in expected and value is not None)
        basis = 'catalog_rows'
    return {'sourceRows': known if not unknown else None, 'sourceRowsKnown': known,
            'sourceCountComplete': not unknown, 'sourceCountUnknownTerminals': unknown,
            'sourceCountBasis': basis}


def _reference_target(dataset, resource, company_id):
    """Validate a preview dependency without returning IDs, rows or validation input."""
    operation = resource in OPERATION_MODELS
    identity = OPERATION_IDENTITY[resource] if operation else S_IDENTITY[resource]
    model = OPERATION_MODELS[resource] if operation else S_MODELS[resource]
    identities = set()
    counts = Counter()
    for row in dataset['rows']:
        if not isinstance(row, dict):
            counts['targetInvalidRows'] += 1
            counts['targetRowsWithoutIdentity'] += 1
            continue
        value = row.get(identity)
        if not isinstance(value, str) or not value.strip():
            counts['targetRowsWithoutIdentity'] += 1
        elif value in identities:
            counts['targetDuplicateIdentityRows'] += 1
        else:
            identities.add(value)
        try:
            model.model_validate(
                {key: value for key, value in row.items() if not key.startswith('_')})
        except (ValidationError, ValueError, TypeError):
            counts['targetInvalidRows'] += 1
        if operation and not (row.get('createdDate') or row.get('modifiedDate')):
            counts['targetRowsMissingChangeDate'] += 1
        if operation and row.get('companyId') != company_id:
            counts['targetOtherCompanyRows'] += 1
        if not operation and _day(row.get('reportDate')) is None:
            counts['targetInvalidDateRows'] += 1
    ready = dataset.get('ready') is True and not dataset.get('blockers')
    return identities, {
        'targetRows': len(dataset['rows']), 'targetIdentityCount': len(identities),
        'targetExtractionReady': ready,
        **{key: counts[key] for key in (
            'targetRowsWithoutIdentity', 'targetDuplicateIdentityRows', 'targetInvalidRows',
            'targetRowsMissingChangeDate', 'targetOtherCompanyRows', 'targetInvalidDateRows')},
    }


def _reference_checks(resource, rows, datasets, company_id, targets):
    """Mirror publication FK membership, but prove dependencies from this preview only.

    A target omitted from the preview might exist in the published store. This
    read-only report cannot establish that, so it reports uncertainty rather
    than declaring delivery ready. Deleted rows still participate, as they do
    in publication validation. List-valued FKs count each referenced value.
    """
    checks = []
    references_for = (OPERATION_REFERENCES.get(resource, {}) if resource in OPERATION_MODELS
                      else _s_references(resource) if resource in S_MODELS else {})
    for field, target in references_for.items():
        references = []
        row_count = 0
        for row in rows:
            if not isinstance(row, dict):
                continue
            value = row.get(field)
            if value is None:
                continue
            values = value if isinstance(value, list) else [value]
            if values:
                row_count += 1
                references.extend(values)
        valid = [value for value in references if isinstance(value, str) and value.strip()]
        invalid_count = len(references) - len(valid)
        check = {'field': field, 'targetResource': target,
                 'targetIdentityField': (OPERATION_IDENTITY[target] if target in OPERATION_IDENTITY
                                         else S_IDENTITY[target]),
                 'rowsWithReference': row_count, 'referenceCount': len(references),
                 'distinctReferenceCount': len(set(valid)), 'invalidReferenceCount': invalid_count}
        if not references:
            check.update(status='not_required', missingReferenceCount=0, missingDistinctReferenceCount=0)
        elif target not in datasets:
            check.update(status='target_not_in_preview', missingReferenceCount=None,
                         missingDistinctReferenceCount=None)
        else:
            if target not in targets:
                targets[target] = _reference_target(datasets[target], target, company_id)
            identities, summary = targets[target]
            missing = [value for value in valid if value not in identities]
            check.update(summary, missingReferenceCount=len(missing),
                         missingDistinctReferenceCount=len(set(missing)))
            if not summary['targetExtractionReady']:
                check['status'] = 'target_not_ready'
            elif any(summary[key] for key in (
                    'targetRowsWithoutIdentity', 'targetDuplicateIdentityRows', 'targetInvalidRows',
                    'targetRowsMissingChangeDate', 'targetOtherCompanyRows', 'targetInvalidDateRows')):
                check['status'] = 'target_invalid'
            elif invalid_count:
                check['status'] = 'invalid_reference'
            elif missing:
                check['status'] = 'missing_identity'
            else:
                check['status'] = 'ready'
        checks.append(check)
    return checks


def reconciliation_report(preview, profile):
    report = {'companyId': preview['companyId'], 'sourceReadAt': preview['sourceReadAt'],
              'period': [preview.get('startDate'), preview.get('endDate')], 'resources': {}}
    targets = {}
    for name, dataset in preview['datasets'].items():
        rows = dataset['rows']
        if name in S_MODELS:
            valid_rows, validation = _s_validation(name, dataset, preview)
        elif name in OPERATION_MODELS:
            valid_rows, validation = _operation_validation(name, dataset, preview)
        else:
            valid_rows, validation = rows, {}
        totals = Counter()
        daily = defaultdict(Counter)
        for row in valid_rows:
            for field in ('containerWeight', 'containerTEU', 'bulkWeight'):
                if field in row:
                    value = Decimal(str(row[field]))
                    totals[field] += value
                    daily[(row['finishDate'], row.get('shipId'))][field] += value
        nulls = unaccepted_nulls(name, rows, profile) if name in PRODUCTION else {}
        checks = _reference_checks(name, rows, preview['datasets'], preview['companyId'], targets)
        references_ready = all(check['status'] in ('ready', 'not_required') for check in checks)
        schema_ready = validation.get('schemaReady', True)
        coverage_ready = validation.get('coverageReady') is not False
        report['resources'][name] = {
            'extractionReady': dataset.get('ready', False),
            'deliveryReady': (dataset.get('ready') is True and not dataset.get('blockers')
                              and not nulls and profile.get('approved') is True and references_ready
                              and schema_ready and coverage_ready),
            'referenceValidationScope': 'preview_only' if name in OPERATION_MODELS or name in S_MODELS else 'not_checked',
            'referenceReady': references_ready if name in OPERATION_MODELS or name in S_MODELS else None,
            'referenceChecks': checks,
            'rowValidation': validation,
            'rows': len(rows), 'blockers': dataset.get('blockers', []),
            **_source_counts(dataset, profile),
            'sourceDiagnostics': dataset.get('source_diagnostics', []),
            'excludedTimeRows': dataset.get('excluded_time_rows', 0),
            'excludedRoroRows': dataset.get('excluded_roro_rows', 0),
            'issueCounts': dataset.get('issue_counts', {}),
            'issueSamples': dataset.get('issue_samples', []),
            'identityConflicts': dataset.get('identity_conflicts', []),
            'identityConflictCount': dataset.get('identity_conflict_count', 0),
            'identityDifferences': dataset.get('identity_differences', []),
            'attributeCompletedIdCount': dataset.get('attribute_completed_id_count', 0),
            'attributeCompletedFields': dataset.get('attribute_completed_fields', []),
            'nullFieldsAwaitingConfirmation': nulls,
            'totals': {key: str(value) for key, value in totals.items()},
            'byDayShip': [{'date': day, 'shipId': ship, **{k: str(v) for k, v in measures.items()}}
                          for (day, ship), measures in sorted(daily.items(), key=lambda item: (item[0][0], item[0][1] or ''))][:1000],
            'byDayShipTruncated': len(daily) > 1000,
        }
    return report

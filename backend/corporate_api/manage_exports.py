"""Extract, review and publish corporate exports without exposing source DB to callers."""
import argparse
from datetime import date, datetime, timezone
from decimal import Decimal
import hashlib
import json
from pathlib import Path
import sys

from .registry import IDENTITY, MODELS, PRODUCTION, S_MODELS, resources_for_domain
from .operation_contracts import OPERATION_MODELS, OPERATION_REFERENCES
from .errors import CorporateError
from .sql import query_source
from .store import ExportStore, operation_dependencies, ensure_dependencies_fresh
from .freshness import max_age_seconds
from .reconciliation import unaccepted_nulls, reconciliation_report
from .metadata_identity import policies as metadata_policies, validate_variants
from .attribute_completion import policies as attribute_policies
from .organization_scope import settings as team_scope_settings, selected_terminals

FORMAT = 'CNT-S-v3-preview-1'


def json_default(value):
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    raise TypeError('Unsupported preview value')


def profile_digest(profile):
    return hashlib.sha256(json.dumps(profile, ensure_ascii=False, sort_keys=True,
                                     separators=(',', ':')).encode()).hexdigest()


def extract(profile, start, end, resources, query_fn=query_source, *, reference_scope=None):
    # Lazy imports keep account management and HTTP reads independent of pyodbc.
    from .catalog_source import extract_catalogs
    from .source import ProductionSource
    if start > end or (set(resources) & PRODUCTION and (end - start).days > 30):
        raise ValueError('Extract one to 31 inclusive calendar days at a time.')
    if not resources or set(resources) - set(MODELS):
        raise ValueError('Unknown or empty extraction resource selection.')
    if reference_scope is not None and (set(resources) & PRODUCTION or set(resources) & set(OPERATION_MODELS)):
        raise ValueError('Reference-scoped extraction is only available for S catalogs.')
    selected_terminals(profile)
    read_started = datetime.now(timezone.utc).isoformat()
    datasets = {}
    if set(resources) & (set(S_MODELS) - PRODUCTION):
        datasets.update(extract_catalogs(query_fn, profile=profile,
                        resources=[key for key in resources if key in S_MODELS and key not in PRODUCTION],
                        reference_scope=reference_scope))
    if set(resources) & set(OPERATION_MODELS):
        from .operation_source import extract_operation_catalogs
        datasets.update(extract_operation_catalogs(query_fn, profile=profile,
                         resources=[key for key in resources if key in OPERATION_MODELS]))
    if set(resources) & PRODUCTION:
        datasets.update(ProductionSource(query_fn).extract(start, end, profile,
                        resources=[key for key in resources if key in PRODUCTION]))
    return {'format': FORMAT, 'companyId': 'CNT', 'profileDigest': profile_digest(profile),
            'sourceReadAt': read_started,
            'startDate': start.strftime('%Y%m%d'), 'endDate': end.strftime('%Y%m%d'),
            'datasets': {key: datasets[key] for key in resources}}


REFERENCES = {
    'shipId': ('shipDetails', 'shipId'), 'shipAgentId': ('customers', 'customerCode'),
    'shipOperatorId': ('customers', 'customerCode'), 'containerOperatorId': ('customers', 'customerCode'),
    'customerCode': ('customers', 'customerCode'), 'handlingMethodId': ('handlingMethodList', 'handlingMethodId'),
    'classId': ('class', 'classId'), 'shipClassId': ('class', 'classId'),
    'originId': ('origins', 'originId'), 'bulkOriginId': ('origins', 'originId'),
    'containerSizeId': ('containerSize', 'containerSizeId'), 'cargoTypeId': ('cargoType', 'cargoTypeId'),
    'cargoCategoryId': ('cargoCategory', 'cargoId'), 'cargoParentId': ('cargoCategory', 'cargoId'),
}


def references_for(resource):
    # Identically named fields in different domains need different catalogs.
    if resource in OPERATION_MODELS:
        return {field: (target, IDENTITY[target])
                for field, target in OPERATION_REFERENCES.get(resource, {}).items()}
    return REFERENCES


def validate_preview(preview, profile, store, resources):
    date_merge_policies = metadata_policies(profile)
    attribute_policies(profile)
    team_scope_settings(profile)
    if preview.get('format') != FORMAT or preview.get('companyId') != 'CNT':
        raise ValueError('Unknown preview format or company.')
    if preview.get('profileDigest') != profile_digest(profile):
        raise ValueError('Mapping profile changed after extraction. Extract a new preview.')
    if profile.get('approved') is not True:
        raise ValueError('Complete and review the source mapping profile before publication (approved=true).')
    datasets = preview.get('datasets', {})
    catalog_ids = {}
    for resource in resources:
        result = datasets.get(resource, {})
        if result.get('ready') is not True or result.get('blockers'):
            raise ValueError(f'{resource}: extraction is not ready; inspect blockers in the preview.')
        if resource in PRODUCTION and unaccepted_nulls(resource, result['rows'], profile):
            raise ValueError(f'{resource}: NULL_FIELD_UNCONFIRMED; receiver acceptance is required for missing fields.')
        for row in result['rows']:
            validate_variants(resource, row, date_merge_policies.get(resource))
            MODELS[resource].model_validate({key: value for key, value in row.items() if not key.startswith('_')})
            if resource in OPERATION_MODELS and not (row.get('createdDate') or row.get('modifiedDate')):
                raise ValueError(f'{resource}: missing change dates; required date filtering would be unavailable.')
        if resource in {'oprt.portOpTeam', 'oprt.portOpStaff'}:
            from .operation_source import _mapping
            mapping = _mapping(resource, profile)
            if mapping.get('team_scope'):
                terminals = selected_terminals(profile)
                allowed = {str(identity) for terminal in terminals for identity in mapping['team_scope'][terminal]}
                if any(row['teamId'] not in allowed for row in result['rows']):
                    raise ValueError(f'{resource}: preview contains a team outside the configured native scope.')
                if resource == 'oprt.portOpTeam' and {row['teamId'] for row in result['rows']} != allowed:
                    raise ValueError('oprt.portOpTeam: preview does not cover every selected native team ID.')
        if resource in IDENTITY:
            catalog_ids[resource] = {row[IDENTITY[resource]] for row in result['rows']}
    # References may use a catalog in this publication or one already published.
    with store.db() as db:
        for resource in IDENTITY:
            if resource not in catalog_ids:
                catalog_ids[resource] = {row[0] for row in db.execute('''SELECT r.identity FROM export_rows r
                    JOIN export_versions v ON r.snapshot_id=v.snapshot_id
                    WHERE v.current=1 AND v.resource=? AND v.company_id='CNT' ''', (resource,))}
    for resource in resources:
        result = datasets[resource]
        if resource in PRODUCTION and result.get('coverage') != [[preview['startDate'], preview['endDate']]]:
            raise ValueError('Production coverage must match the complete extracted period.')
        for row in result['rows']:
            for field, (target, _) in references_for(resource).items():
                if field == IDENTITY.get(resource) or field not in row or row[field] is None:
                    continue
                values = row[field] if isinstance(row[field], list) else [row[field]]
                if any(value not in catalog_ids[target] for value in values):
                    raise ValueError(f'{resource}: unresolved reference {field}; publish the corresponding catalog first.')
    # Reusing a catalog remains supported, but a fresh extraction must not
    # acquire dependencies whose source data has already expired.
    existing_dependencies = set()
    for resource in resources:
        existing_dependencies.update(set(operation_dependencies(resource, datasets[resource]['rows'])) - set(resources))
    if existing_dependencies:
        with store.db() as db:
            ensure_dependencies_fresh(db, sorted(existing_dependencies), preview['companyId'], max_age_seconds())


def publish_preview(preview, profile, store, resources, *, replace_all=False):
    with store.atomic_publication():
        result = _publish_preview(preview, profile, store, resources, replace_all=replace_all)
        validate_current_references(store)
        return result


class _PublicationCheckComplete(Exception):
    """Internal signal that rolls back a successful publication rehearsal."""


def check_publication(preview, profile, store, resources):
    """Validate the full publication, including retained references, then roll back.

    The write transaction is never committed or visible to API readers. This
    also restores version retention/deletion and indexes altered by the trial.
    """
    try:
        with store.atomic_publication():
            _publish_preview(preview, profile, store, resources)
            validate_current_references(store)
            raise _PublicationCheckComplete()
    except _PublicationCheckComplete:
        return


def validate_current_references(store):
    """A new catalog must also preserve references in retained historical periods."""
    with store.db() as db:
        ids = {resource: {row[0] for row in db.execute('''SELECT r.identity FROM export_rows r
            JOIN export_versions v ON r.snapshot_id=v.snapshot_id
            WHERE v.current=1 AND v.resource=? AND v.company_id='CNT' ''', (resource,))}
            for resource in IDENTITY}
        for record in db.execute('''SELECT v.resource,r.payload FROM export_rows r
                JOIN export_versions v ON r.snapshot_id=v.snapshot_id
                WHERE v.current=1 AND v.company_id='CNT' '''):
            resource, row = record['resource'], json.loads(record['payload'])
            for field, (target, _) in references_for(resource).items():
                if field == IDENTITY.get(resource) or field not in row or row[field] is None:
                    continue
                values = row[field] if isinstance(row[field], list) else [row[field]]
                if any(value not in ids[target] for value in values):
                    raise ValueError(f'{resource}: current or retained historical rows still reference a removed {field}.')


def _publish_preview(preview, profile, store, resources, *, replace_all=False):
    validate_preview(preview, profile, store, resources)
    date_merge_policies = metadata_policies(profile)
    result = {}
    for resource in sorted(resources, key=lambda key: (key in PRODUCTION, key)):
        dataset = preview['datasets'][resource]
        params = dict(read_at=preview['sourceReadAt'], warnings=dataset.get('warnings', []),
                      rule_version=preview['profileDigest'])
        if resource in PRODUCTION and not replace_all:
            result[resource] = store.publish_period(resource, 'CNT', dataset['rows'],
                start=preview['startDate'], end=preview['endDate'], **params)
        else:
            result[resource] = store.publish(resource, 'CNT', dataset['rows'],
                coverage=dataset.get('coverage') if resource in PRODUCTION else None,
                metadata_merge_policy=date_merge_policies.get(resource), **params)
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    read = commands.add_parser('extract', help='Read-only source extraction; never changes the public export.')
    read.add_argument('--profile', required=True)
    read.add_argument('--start', type=date.fromisoformat, required=True)
    read.add_argument('--end', type=date.fromisoformat, required=True)
    read.add_argument('--output', required=True)
    read.add_argument('--resource', action='append', choices=sorted(MODELS))
    read.add_argument('--domain', choices=('production', 'operations', 'all'), default='production',
                      help='Default keeps the existing S-domain extraction. Explicit --resource overrides this selection.')
    read.add_argument('--report', help='Write private reconciliation totals and diagnostics as JSON.')
    write = commands.add_parser('publish', help='Publish a reviewed extraction into the local API export store.')
    write.add_argument('--profile', required=True)
    write.add_argument('--input', required=True)
    write.add_argument('--resource', action='append', choices=sorted(MODELS))
    write.add_argument('--replace-all', action='store_true', help='Replace all prior dates for selected resources; use for a deliberate full rebuild.')
    commands.add_parser('status', help='Show resource readiness and coverage without payloads.')
    args = parser.parse_args(argv)
    try:
        if args.command == 'status':
            print(json.dumps(ExportStore().describe(), ensure_ascii=False, indent=2))
            return 0
        profile = json.loads(Path(args.profile).read_text(encoding='utf-8-sig'))
        if args.command == 'extract':
            resources = list(dict.fromkeys(args.resource or resources_for_domain(args.domain)))
            preview = extract(profile, args.start, args.end, resources)
            output = Path(args.output)
            output.parent.mkdir(parents=True, exist_ok=True)
            # Exclusive create avoids overwriting a previous reconciliation artifact.
            with output.open('x', encoding='utf-8') as handle:
                json.dump(preview, handle, ensure_ascii=False, indent=2, default=json_default, allow_nan=False)
            if args.report:
                with Path(args.report).open('x', encoding='utf-8') as handle:
                    json.dump(reconciliation_report(preview, profile), handle, ensure_ascii=False,
                              indent=2, default=json_default, allow_nan=False)
            print(json.dumps({key: {'ready': value['ready'], 'rows': len(value['rows']),
                                   'blockers': value.get('blockers', [])} for key, value in preview['datasets'].items()},
                             ensure_ascii=False, indent=2))
            return 0 if all(item['ready'] for item in preview['datasets'].values()) else 2
        preview = json.loads(Path(args.input).read_text(encoding='utf-8-sig'))
        resources = list(dict.fromkeys(args.resource or preview['datasets']))
        if any(key not in MODELS for key in resources):
            raise ValueError('Unsupported resource.')
        print(json.dumps(publish_preview(preview, profile, ExportStore(), resources, replace_all=args.replace_all), indent=2))
        return 0
    except CorporateError as exc:
        print(f'{exc.code}: {exc.message}', file=sys.stderr)
    except (ValueError, OSError, KeyError, TypeError):
        # ValidationError can contain source rows and customer contact information.
        print('Export command failed validation or file access. Check the profile, preview blockers, coverage and catalog references. No source SQL was modified.', file=sys.stderr)
    return 1


if __name__ == '__main__':
    raise SystemExit(main())

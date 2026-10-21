"""Read-only production API verification against an explicitly approved MISBI reference.

Run from the repository root with ``python -m backend.corporate_api.verify_delivery``.
Credentials come from CORPORATE_API_USERNAME / CORPORATE_API_PASSWORD, or a bounded
stdin JSON object with Username and Password when --credentials-stdin is selected.
Only login/logout and GET requests are sent; nothing is delivered to Lakehouse.

Illustrative JSON shape only: the values below are synthetic, not an approved
MISBI measurement. Replace them with the approved period and figures::

    {"source": "MISBI", "approved": true, "referenceDate": "2026-10-14",
     "comparisons": [{"startDate": "20260101", "endDate": "20260131",
       "scope": "nghe_tinh", "resources": ["contQuayVolumesCB"],
       "metric": "TEU", "expected": "1234", "tolerance": "0"}]}

Each comparison names its own resources. Quay and gate must never be combined.
Scope is an operator declaration: the public contract does not prove berth scope.
Matching totals alone do not certify receiver acceptance or delivery readiness.
Every production page must supply X-Result-Digest for the complete filtered
business result. A changed digest invalidates that entire resource/month.
Published-mode snapshots are followed within each resource/month; live reads
remain fresh requests. Failed windows are not retried or combined automatically.
Optional --verify-references checks the actual non-null foreign keys through
public catalog GETs, including cargo parents. It never grants null acceptance
or changes retrieval/comparison results. Reads are sequential and bounded by
--reference-limit (default 200, maximum 2000); incomplete windows are skipped.
"""
import argparse
import calendar
from collections import deque
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation, localcontext
from http.client import HTTPException
import json
import os
from pathlib import Path
import re
import sys
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode, urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

from pydantic import ValidationError

from .contracts import IDENTITY, MODELS, Query
from .diagnostics import CODES as DIAGNOSTIC_CODES


RESOURCES = ('contQuayVolumesCB', 'contGateVolumesCB',
             'bulkQuayVolumesCB', 'bulkGateVolumesCB')
SCOPES = ('nghe_tinh', 'vietsun', 'all_activity')
MAX_PAGE_BYTES = 2 * 1024 * 1024
MAX_REFERENCE_BYTES = 512 * 1024
MAX_PAGES = 500
PAGE_SIZE = 100
MAX_RETRY_AFTER_SECONDS = 86_400
DEFAULT_PUBLIC_REFERENCE_LIMIT = 200
MAX_PUBLIC_REFERENCE_LIMIT = 2000
MAX_PUBLIC_REFERENCE_SAMPLES = 10
# These S-domain foreign keys match manage_exports.references_for(). Keeping the
# client-side verifier independent avoids importing source/SQL adapters here.
PUBLIC_REFERENCES = {
    'shipId': 'shipDetails', 'shipAgentId': 'customers', 'shipOperatorId': 'customers',
    'containerOperatorId': 'customers', 'customerCode': 'customers',
    'handlingMethodId': 'handlingMethodList', 'classId': 'class', 'shipClassId': 'class',
    'originId': 'origins', 'bulkOriginId': 'origins', 'containerSizeId': 'containerSize',
    'cargoTypeId': 'cargoType', 'cargoCategoryId': 'cargoCategory', 'cargoParentId': 'cargoCategory',
}
MEASURES = {'containerWeight', 'containerTEU', 'bulkWeight', 'reportDate'}
PUBLIC_ERROR_CODES = DIAGNOSTIC_CODES | frozenset({
    'CORPORATE_API_DISABLED', 'SOURCE_CONFIGURATION_INVALID', 'SOURCE_PROFILE_REQUIRED',
    'SOURCE_PROFILE_INVALID', 'SOURCE_MAPPING_REQUIRED', 'SOURCE_BUSY',
    'SOURCE_NULL_POLICY_REQUIRED', 'SOURCE_RANGE_TOO_LARGE', 'SOURCE_RESULT_TOO_LARGE',
    'DATASET_NOT_READY', 'PERIOD_NOT_READY', 'SOURCE_STALE',
    'MACHINE_TOKEN_INVALID', 'MACHINE_SCOPE_FORBIDDEN', 'MACHINE_STORAGE_UNAVAILABLE',
    'FORBIDDEN', 'INVALID_REQUEST', 'RATE_LIMITED', 'FILTER_ID_NOT_FOUND', 'NOT_FOUND',
    'SOURCE_FILTER_VALIDATION_UNAVAILABLE',
    'SNAPSHOT_REQUIRED', 'SNAPSHOT_EXPIRED', 'SNAPSHOT_NOT_SUPPORTED',
    'DATASET_STALE', 'SOURCE_TIME_INVALID', 'FRESHNESS_CONFIG_INVALID',
    'REFERENCE_DATASET_NOT_READY', 'REFERENCE_SOURCE_TIME_INVALID',
    'REFERENCE_METADATA_NOT_READY', 'SNAPSHOT_REFERENCES_CHANGED', 'FILTER_NOT_READY',
})


class VerificationError(ValueError):
    """Fixed local error code only; never includes credentials or server text."""


@dataclass
class Response:
    status: int
    headers: dict
    body: bytes


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, request, response, code, message, headers, target):
        return None


def validate_base_url(value):
    if not isinstance(value, str) or any(c.isspace() for c in value):
        raise VerificationError('INVALID_BASE_URL')
    try:
        parsed = urlsplit(value)
        _ = parsed.port
    except (ValueError, TypeError):
        raise VerificationError('INVALID_BASE_URL') from None
    if (parsed.scheme not in {'https', 'http'} or not parsed.hostname
            or parsed.username is not None or parsed.password is not None or parsed.query or parsed.fragment
            or parsed.path not in {'', '/'}
            or (parsed.scheme == 'http' and parsed.hostname not in {'localhost', '127.0.0.1', '::1'})):
        raise VerificationError('INVALID_BASE_URL')
    return value.rstrip('/')


class HttpTransport:
    def __init__(self, base_url, timeout=60):
        self.base_url = validate_base_url(base_url)
        self.timeout = timeout
        self.opener = build_opener(_NoRedirect())

    def request(self, method, path, *, params=None, headers=None, json_body=None):
        url = self.base_url + path + ('?' + urlencode(params) if params else '')
        data = None if json_body is None else json.dumps(json_body).encode('utf-8')
        request = Request(url, data=data, method=method,
                          headers={**(headers or {}), 'Content-Type': 'application/json'})
        try:
            try:
                response = self.opener.open(request, timeout=self.timeout)
            except HTTPError as exc:
                response = exc
            with response:
                body = response.read(MAX_PAGE_BYTES + 1)
                if len(body) > MAX_PAGE_BYTES:
                    raise VerificationError('RESPONSE_TOO_LARGE')
                return Response(response.code, dict(response.headers.items()), body)
        except (URLError, TimeoutError, OSError, HTTPException):
            raise VerificationError('TRANSPORT_ERROR') from None


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise VerificationError('DUPLICATE_JSON_FIELD')
        result[key] = value
    return result


def _invalid_constant(value):
    raise VerificationError('INVALID_JSON_NUMBER')


def _json(raw):
    try:
        return json.loads(raw, parse_float=Decimal, parse_constant=_invalid_constant,
                          object_pairs_hook=_unique_object)
    except (ValueError, UnicodeError, TypeError, RecursionError):
        raise VerificationError('INVALID_JSON') from None


def _day(value):
    if not isinstance(value, str) or not re.fullmatch(r'[0-9]{8}', value):
        raise VerificationError('INVALID_PERIOD')
    try:
        return datetime.strptime(value, '%Y%m%d').date()
    except ValueError:
        raise VerificationError('INVALID_PERIOD') from None


def _number(value):
    if isinstance(value, bool) or not isinstance(value, (str, int, Decimal)):
        raise VerificationError('INVALID_MEASURE')
    try:
        number = Decimal(value)
    except (ValueError, InvalidOperation):
        raise VerificationError('INVALID_MEASURE') from None
    if (not number.is_finite() or number < 0 or number > Decimal('1000000000000000')
            or len(number.as_tuple().digits) > 38 or number.as_tuple().exponent < -12):
        raise VerificationError('INVALID_MEASURE')
    return number


def month_windows(start, end):
    if (type(start) is not date or type(end) is not date or end < start or end == date.max
            or (end - start).days > 365):
        raise VerificationError('INVALID_PERIOD')
    cursor = start
    while cursor <= end:
        last = min(end, date(cursor.year, cursor.month, calendar.monthrange(cursor.year, cursor.month)[1]))
        yield cursor, last
        cursor = last + timedelta(days=1)


def validate_reference(reference, start, end, production_scope):
    if reference is None:
        return None
    if (not isinstance(reference, dict) or reference.get('source') != 'MISBI'
            or reference.get('approved') is not True
            or set(reference) != {'source', 'approved', 'referenceDate', 'comparisons'}):
        raise VerificationError('REFERENCE_NOT_APPROVED')
    try:
        if not isinstance(reference['referenceDate'], str) or not re.fullmatch(r'[0-9]{4}-[0-9]{2}-[0-9]{2}', reference['referenceDate']):
            raise ValueError()
        reference_day = date.fromisoformat(reference['referenceDate'])
    except (ValueError, TypeError, KeyError):
        raise VerificationError('INVALID_REFERENCE_DATE') from None
    entries = reference['comparisons']
    if not isinstance(entries, list) or not 1 <= len(entries) <= 500:
        raise VerificationError('INVALID_REFERENCE')
    prepared, seen = [], set()
    for entry in entries:
        if (not isinstance(entry, dict)
                or set(entry) - {'startDate', 'endDate', 'scope', 'resources', 'metric', 'expected', 'tolerance'}
                or not {'startDate', 'endDate', 'scope', 'resources', 'metric', 'expected'} <= set(entry)):
            raise VerificationError('INVALID_REFERENCE')
        first, last = _day(entry['startDate']), _day(entry['endDate'])
        resources = entry['resources']
        if (not start <= first <= last <= end or reference_day < last
                or not isinstance(resources, list) or not resources
                or any(not isinstance(r, str) or r not in RESOURCES for r in resources)
                or len(set(resources)) != len(resources)):
            raise VerificationError('INVALID_REFERENCE')
        sides = {'quay' if 'Quay' in r else 'gate' for r in resources}
        if len(sides) != 1:
            raise VerificationError('MIXED_QUAY_GATE_REFERENCE')
        expected_scope = production_scope if 'quay' in sides else 'all_yards'
        if entry['scope'] != expected_scope:
            raise VerificationError('REFERENCE_SCOPE_MISMATCH')
        metric = entry['metric']
        if not isinstance(metric, str) or metric not in {'tonnes', 'TEU'} or (metric == 'TEU' and any(not r.startswith('cont') for r in resources)):
            raise VerificationError('INVALID_REFERENCE_METRIC')
        identity = (first, last, expected_scope, tuple(sorted(resources)), metric)
        if identity in seen:
            raise VerificationError('DUPLICATE_REFERENCE')
        seen.add(identity)
        prepared.append({**entry, 'resources': sorted(resources),
                         'expected': str(_number(entry['expected'])),
                         'tolerance': str(_number(entry.get('tolerance', '0')))})
    return {**reference, 'comparisons': prepared}


def _header_int(headers, key):
    value = headers.get(key)
    if not isinstance(value, str) or not re.fullmatch(r'[0-9]{1,9}', value):
        raise VerificationError('INVALID_PAGINATION')
    return int(value)


def _source_time(headers):
    raw = headers.get('x-source-read-at')
    try:
        if not isinstance(raw, str) or len(raw) > 64:
            raise ValueError()
        timestamp = datetime.fromisoformat(raw)
        if timestamp.tzinfo is None:
            raise ValueError()
        return timestamp.isoformat()
    except (ValueError, TypeError):
        raise VerificationError('SOURCE_READ_TIME_MISSING') from None


def _result_digest(headers):
    value = headers.get('x-result-digest')
    if value is None:
        raise VerificationError('RESULT_DIGEST_MISSING')
    if not isinstance(value, str) or not re.fullmatch(r'[a-f0-9]{64}', value):
        raise VerificationError('RESULT_DIGEST_INVALID')
    return value


def _snapshot_id(headers):
    value = headers.get('x-snapshot-id')
    if value is not None and (not isinstance(value, str) or not re.fullmatch(r'[a-f0-9]{32}', value)):
        raise VerificationError('SNAPSHOT_HEADER_INVALID')
    return value


def _failure_metadata(headers):
    """Copy fixed public codes and bounded retry seconds, never arbitrary text."""
    result = {}
    code = headers.get('x-error-code')
    if isinstance(code, str) and code in PUBLIC_ERROR_CODES:
        result['serverErrorCode'] = code
    retry_after = headers.get('retry-after')
    if (isinstance(retry_after, str) and re.fullmatch(r'[0-9]{1,5}', retry_after)
            and int(retry_after) <= MAX_RETRY_AFTER_SECONDS):
        result['retryAfterSeconds'] = int(retry_after)
    return result


def _fetch_window(transport, headers, resource, start, end, company_id, *,
                  collect_references=False, reference_limit=DEFAULT_PUBLIC_REFERENCE_LIMIT):
    result = {'resource': resource, 'startDate': start.strftime('%Y%m%d'),
              'endDate': end.strftime('%Y%m%d'), 'complete': False,
              'rowsReceived': 0, 'pages': [], 'totals': None, 'error': None}
    totals = {'tonnes': Decimal(0), 'TEU': Decimal(0) if resource.startswith('cont') else None}
    daily, identities, expected = {}, set(), None
    expected_digest, snapshot_id = None, None
    references, references_truncated = set(), False
    relations, relations_truncated = set(), False
    try:
        for page in range(1, MAX_PAGES + 1):
            query = Query(companyId=company_id, startDate=result['startDate'],
                          endDate=result['endDate'], page=page, limit=PAGE_SIZE,
                          snapshotId=snapshot_id)
            response = transport.request('GET', '/api/' + resource,
                                         params=query.model_dump(exclude_none=True), headers=headers)
            metadata = {k.lower(): v for k, v in response.headers.items()}
            page_info = {'page': page, 'http': response.status, 'rows': 0}
            result['pages'].append(page_info)
            if response.status != 200:
                page_info.update(_failure_metadata(metadata))
                raise VerificationError('HTTP_ERROR')
            body = _json(response.body)
            if (not isinstance(body, dict) or set(body) != {'data', 'code', 'message'}
                    or body['code'] != '1' or not isinstance(body['message'], str)
                    or not isinstance(body['data'], list)):
                raise VerificationError('INVALID_PUBLIC_ENVELOPE')
            digest, current_snapshot = _result_digest(metadata), _snapshot_id(metadata)
            page_info['resultDigest'] = digest
            if current_snapshot is not None:
                page_info['snapshotId'] = current_snapshot
            if page == 1:
                expected_digest, snapshot_id = digest, current_snapshot
            else:
                if current_snapshot != snapshot_id:
                    raise VerificationError('SNAPSHOT_CHANGED_BETWEEN_PAGES')
                if digest != expected_digest:
                    raise VerificationError('RESULT_CHANGED_BETWEEN_PAGES')
            count = _header_int(metadata, 'x-total-count')
            if count > MAX_PAGES * PAGE_SIZE:
                raise VerificationError('RESULT_LIMIT_EXCEEDED')
            if (_header_int(metadata, 'x-page') != page
                    or _header_int(metadata, 'x-limit') != PAGE_SIZE
                    or metadata.get('x-has-next') not in {'true', 'false'}):
                raise VerificationError('INVALID_PAGINATION')
            if expected is not None and expected != count:
                raise VerificationError('TOTAL_CHANGED_BETWEEN_PAGES')
            expected = count
            next_page = page * PAGE_SIZE < count
            expected_rows = max(0, min(PAGE_SIZE, count - (page - 1) * PAGE_SIZE))
            if metadata['x-has-next'] != str(next_page).lower() or len(body['data']) != expected_rows:
                raise VerificationError('MISSING_OR_UNEXPECTED_PAGE_ROWS')
            page_info.update(sourceReadAt=_source_time(metadata), total=count, rows=len(body['data']))
            for raw in body['data']:
                if not isinstance(raw, dict):
                    raise VerificationError('INVALID_ROW')
                # The S response contract uses JSON numbers. Reference files
                # may encode decimals as strings to preserve exact values, but
                # accepting those on the wire would hide a schema regression.
                # Validate before Decimal/model coercion (e.g. "30" -> 30).
                for field in ('containerWeight', 'containerTEU') if resource.startswith('cont') else ('bulkWeight',):
                    if type(raw.get(field)) not in (int, Decimal):
                        raise VerificationError('INVALID_MEASURE')
                    _number(raw.get(field))
                try:
                    row = MODELS[resource].model_validate(raw).model_dump(mode='python')
                except (ValidationError, ValueError, TypeError):
                    raise VerificationError('INVALID_ROW') from None
                if row['companyId'] != company_id or not start <= _day(row['finishDate']) <= end:
                    raise VerificationError('ROW_OUTSIDE_REQUEST_SCOPE')
                if collect_references:
                    for field, target in PUBLIC_REFERENCES.items():
                        if row.get(field) is not None:
                            key = (target, row[field])
                            if key not in references:
                                if len(references) >= reference_limit:
                                    references_truncated = True
                                else:
                                    references.add(key)
                    if resource.startswith('bulk'):
                        relation = (row['cargoCategoryId'], row['cargoTypeId'])
                        if relation not in relations:
                            if len(relations) >= reference_limit:
                                relations_truncated = True
                            else:
                                relations.add(relation)
                key = tuple(sorted((k, v) for k, v in row.items() if k not in MEASURES))
                if key in identities:
                    raise VerificationError('DUPLICATE_BUSINESS_KEY')
                identities.add(key)
                values = {'tonnes': _number(row.get('containerWeight', row.get('bulkWeight')))}
                if resource.startswith('cont'):
                    values['TEU'] = _number(row['containerTEU'])
                day_totals = daily.setdefault(row['finishDate'], {key: Decimal(0) for key in values})
                for metric, value in values.items():
                    totals[metric] += value
                    day_totals[metric] += value
                result['rowsReceived'] += 1
            if not next_page:
                if result['rowsReceived'] != expected:
                    raise VerificationError('MISSING_OR_UNEXPECTED_PAGE_ROWS')
                result['complete'] = True
                result['totals'] = {k: str(v) if v is not None else None for k, v in totals.items()}
                break
        else:
            raise VerificationError('PAGE_LIMIT_EXCEEDED')
    except VerificationError as exc:
        result['error'] = str(exc)
    result['receivedSubtotal'] = {k: str(v) if v is not None else None for k, v in totals.items()}
    result['_daily'] = daily
    if collect_references and result['complete']:
        # Only complete, digest-validated windows contribute reference lookups.
        # These bounded private keys are removed before serializing the report.
        result['_publicReferenceKeys'] = references
        result['_publicReferencesTruncated'] = references_truncated
        # The number of distinct pairs can exceed the number of IDs (e.g. a
        # category returned under several types). Bound pairs separately and
        # retain truncation so an unchecked relationship cannot silently pass.
        result['_publicReferenceRelations'] = relations
        result['_publicReferenceRelationsTruncated'] = relations_truncated
    return result


def _fetch_public_reference(transport, headers, resource, identity):
    """Read exactly one public S master; never bypass a public ID collision.

    Seven catalogs have a detail route. containerSize only specifies a list
    with an exact-ID filter. Both contracts must end on page one with exactly
    one matching ID; fetching an extra page would hide a broken uniqueness
    contract. Catalogs do not promise production's X-Result-Digest header.
    """
    result = {'error': None, 'dependencies': set()}
    params = {'companyId': 'CNT', 'page': 1, 'limit': PAGE_SIZE}
    if resource == 'containerSize':
        path = '/api/containerSize'
        params['containerSizeId'] = identity
    else:
        path = '/api/' + resource + '/' + quote(identity, safe='')
    try:
        response = transport.request('GET', path, params=params, headers=headers)
        result['http'] = response.status
        metadata = {key.lower(): value for key, value in response.headers.items()}
        if response.status != 200:
            result.update(_failure_metadata(metadata))
            result['error'] = 'HTTP_ERROR'
            return result
        body = _json(response.body)
        if (not isinstance(body, dict) or set(body) != {'data', 'code', 'message'}
                or body['code'] != '1' or not isinstance(body['message'], str)
                or not isinstance(body['data'], list)):
            raise VerificationError('INVALID_PUBLIC_ENVELOPE')
        if (metadata.get('cache-control') != 'no-store'
                or _header_int(metadata, 'x-page') != 1
                or _header_int(metadata, 'x-limit') != PAGE_SIZE
                or metadata.get('x-has-next') != 'false'):
            raise VerificationError('INVALID_REFERENCE_PAGINATION')
        total = _header_int(metadata, 'x-total-count')
        if total == 0 and body['data'] == []:
            raise VerificationError('REFERENCE_NOT_FOUND')
        if total != 1 or len(body['data']) != 1:
            raise VerificationError('REFERENCE_NOT_UNIQUE')
        _source_time(metadata)
        _snapshot_id(metadata)
        raw = body['data'][0]
        if not isinstance(raw, dict) or raw.get(IDENTITY[resource]) != identity:
            raise VerificationError('REFERENCE_ID_MISMATCH')
        # Pydantic deliberately accepts some internal/coercible values. The
        # receiver sees JSON numbers and integer 0/1 flags, so validate those
        # wire types before coercion can hide a public serialization regression.
        if resource == 'shipDetails':
            if any(raw.get(field) is not None and type(raw[field]) not in (int, Decimal)
                   for field in ('shipLOA', 'shipBeam', 'shipGRT', 'shipDWT')):
                raise VerificationError('INVALID_REFERENCE_ROW')
        if resource == 'customers':
            if any(raw.get(field) is not None and (type(raw[field]) is not int or raw[field] not in (0, 1))
                   for field in ('isCarrier', 'isAgent')):
                raise VerificationError('INVALID_REFERENCE_ROW')
            flags = raw.get('metadata')
            if (not isinstance(flags, dict) or type(flags.get('isDeleted')) is not int
                    or flags['isDeleted'] not in (0, 1)):
                raise VerificationError('INVALID_REFERENCE_ROW')
        try:
            row = MODELS[resource].model_validate(raw).model_dump(mode='python')
        except (ValidationError, ValueError, TypeError):
            raise VerificationError('INVALID_REFERENCE_ROW') from None
        if resource == 'cargoCategory':
            result['dependencies'].add(('cargoType', row['cargoTypeId']))
            if row['cargoParentId'] is not None:
                result['dependencies'].add(('cargoCategory', row['cargoParentId']))
    except VerificationError as exc:
        # A caller-supplied transport must not inject exception text into reports.
        safe = {'TRANSPORT_ERROR', 'RESPONSE_TOO_LARGE', 'INVALID_JSON', 'DUPLICATE_JSON_FIELD',
                'INVALID_JSON_NUMBER', 'INVALID_PUBLIC_ENVELOPE', 'INVALID_PAGINATION',
                'INVALID_REFERENCE_PAGINATION', 'REFERENCE_NOT_FOUND', 'REFERENCE_NOT_UNIQUE',
                'SOURCE_READ_TIME_MISSING', 'SNAPSHOT_HEADER_INVALID', 'REFERENCE_ID_MISMATCH',
                'INVALID_REFERENCE_ROW'}
        result['error'] = str(exc) if str(exc) in safe else 'REFERENCE_CHECK_FAILED'
    except Exception:
        result['error'] = 'REFERENCE_CHECK_FAILED'
    return result


def _reference_failure_sample(key, result):
    sample = {'resource': key[0], 'error': result['error']}
    # Native numeric IDs are useful for source review. Do not copy arbitrary
    # identifiers (which can contain personal data or credential-shaped text).
    if re.fullmatch(r'[1-9][0-9]{0,18}', key[1]):
        sample['nativeId'] = key[1]
    for name in ('http', 'serverErrorCode', 'retryAfterSeconds'):
        if name in result:
            sample[name] = result[name]
    return sample


def _reference_relation_failure_sample(category, expected, actual):
    sample = _reference_failure_sample(('cargoCategory', category),
                                      {'error': 'REFERENCE_RELATION_MISMATCH'})
    sample['field'] = 'cargoTypeId'
    for name, value in (('expectedTypeId', expected), ('actualTypeId', actual)):
        if re.fullmatch(r'[1-9][0-9]{0,18}', value):
            sample[name] = value
    return sample


def _verify_public_references(transport, headers, windows, limit):
    """Bounded graph of public GETs, deduplicated for this verification run only."""
    nodes, pending = {}, deque()
    limit_reached = False

    def enqueue(key):
        nonlocal limit_reached
        if key in nodes:
            return
        if len(nodes) >= limit:
            limit_reached = True
            return
        nodes[key] = None
        pending.append(key)

    for window in windows:
        if window['complete']:
            for key in sorted(window['_publicReferenceKeys']):
                enqueue(key)
    requests = 0
    while pending:
        key = pending.popleft()
        nodes[key] = _fetch_public_reference(transport, headers, *key)
        requests += 1
        for dependency in sorted(nodes[key]['dependencies']):
            enqueue(dependency)

    # Each cargo category has at most one parent. Iterative walks avoid recursion
    # depth limits; only genuine cycle members get this error, then closure below
    # propagates their failure to every dependent production window.
    finished = set()
    for key in nodes:
        current, path, positions = key, [], {}
        while current in nodes and current not in finished:
            if current in positions:
                for member in path[positions[current]:]:
                    nodes[member]['error'] = 'REFERENCE_PARENT_CYCLE'
                break
            positions[current] = len(path)
            path.append(current)
            current = next((dep for dep in nodes[current]['dependencies'] if dep[0] == 'cargoCategory'), None)
        finished.update(path)

    complete, passed, samples = 0, 0, []
    for window in windows:
        if not window['complete']:
            window['publicReferences'] = {'status': 'skipped_incomplete', 'directReferenceCount': None,
                'checkedReferenceCount': 0, 'blockedReferenceCount': None,
                'checkedRelationCount': 0, 'mismatchedRelationCount': None,
                'uncheckedRelationCount': None, 'relationCountIsLowerBound': False, 'failureSamples': []}
            continue
        complete += 1
        roots = window.pop('_publicReferenceKeys')
        truncated = window.pop('_publicReferencesTruncated')
        relations = window.pop('_publicReferenceRelations')
        relations_truncated = window.pop('_publicReferenceRelationsTruncated')
        seen, work, failures, checked = set(), list(sorted(roots)), [], 0
        failure_count = 0
        while work:
            key = work.pop()
            if key in seen:
                continue
            seen.add(key)
            result = nodes.get(key)
            if result is None:
                result = {'error': 'REFERENCE_LIMIT_EXCEEDED', 'dependencies': set()}
            else:
                checked += 1
            if result['error']:
                failure_count += 1
                if len(failures) < MAX_PUBLIC_REFERENCE_SAMPLES:
                    failures.append(_reference_failure_sample(key, result))
            work.extend(sorted(result['dependencies']))
        relation_checked, relation_mismatched, relation_unchecked = 0, 0, 0
        for category, expected_type in sorted(relations):
            category_node = nodes.get(('cargoCategory', category))
            actual_type = next((identity for resource, identity in category_node['dependencies']
                                if resource == 'cargoType'), None) if category_node else None
            # Failed/missing lookups are already represented by the closure
            # above. They cannot establish a contradictory public relationship.
            required = (category_node, nodes.get(('cargoType', expected_type)),
                        nodes.get(('cargoType', actual_type)))
            if actual_type is None or any(node is None or node['error'] for node in required):
                relation_unchecked += 1
                continue
            relation_checked += 1
            if actual_type != expected_type:
                relation_mismatched += 1
                if len(failures) < MAX_PUBLIC_REFERENCE_SAMPLES:
                    failures.append(_reference_relation_failure_sample(category, expected_type, actual_type))
        if truncated or relations_truncated:
            limit_reached = True
            if len(failures) < MAX_PUBLIC_REFERENCE_SAMPLES:
                failures.append({'error': 'REFERENCE_LIMIT_EXCEEDED'})
        successful = not (failure_count or truncated or relations_truncated or relation_mismatched or relation_unchecked)
        passed += int(successful)
        window['publicReferences'] = {'status': 'passed' if successful else 'blocked',
            'directReferenceCount': len(roots), 'directReferenceCountIsLowerBound': truncated,
            'checkedReferenceCount': checked, 'blockedReferenceCount': failure_count,
            'blockedReferenceCountIsLowerBound': truncated,
            'checkedRelationCount': relation_checked, 'mismatchedRelationCount': relation_mismatched,
            'uncheckedRelationCount': relation_unchecked, 'relationCountIsLowerBound': relations_truncated,
            'failureSamples': failures}
        for sample in failures:
            if sample not in samples and len(samples) < MAX_PUBLIC_REFERENCE_SAMPLES:
                samples.append(sample)
    return {'enabled': True, 'referenceLimit': limit, 'httpRequests': requests,
        'distinctReferencesChecked': len(nodes), 'successfulLookups': sum(not node['error'] for node in nodes.values()),
        'limitReached': limit_reached, 'completeWindowsChecked': complete, 'passedWindows': passed,
        'blockedWindows': complete - passed, 'skippedIncompleteWindows': len(windows) - complete,
        'allCompleteWindowsPassed': complete > 0 and passed == complete,
        'failureSamples': samples,
        'meaning': 'Public reference closure and direct cargo category/type agreement for complete windows only; not MISBI reconciliation, null-policy acceptance, or delivery approval.'}


def _compare(reference, windows):
    results = []
    for entry in (reference or {}).get('comparisons', []):
        relevant = [w for w in windows if w['resource'] in entry['resources']
                    and w['startDate'] <= entry['endDate'] and w['endDate'] >= entry['startDate']]
        item = {**entry, 'actual': None, 'difference': None, 'status': 'incomplete'}
        if relevant and all(w['complete'] for w in relevant):
            actual = sum((values[entry['metric']] for w in relevant for day, values in w['_daily'].items()
                          if entry['startDate'] <= day <= entry['endDate']), Decimal(0))
            difference = actual - Decimal(entry['expected'])
            item.update(actual=str(actual), difference=str(difference),
                        status='match' if abs(difference) <= Decimal(entry['tolerance']) else 'mismatch')
        results.append(item)
    return results


def _reference_coverage(reference, start, end, production_scope):
    """Coverage is about supplied references, not a claim their figures match.

    Conservatively, a combined-tonnage comparison does not independently verify
    its component endpoints. Those comparisons retain their explicit grouping.
    """
    entries = (reference or {}).get('comparisons', [])
    coverage = []
    for resource in RESOURCES:
        for metric in (('tonnes', 'TEU') if resource.startswith('cont') else ('tonnes',)):
            intervals = sorted((_day(c['startDate']), _day(c['endDate'])) for c in entries
                               if c['resources'] == [resource] and c['metric'] == metric)
            merged = []
            for first, last in intervals:
                if merged and first <= merged[-1][1] + timedelta(days=1):
                    merged[-1][1] = max(last, merged[-1][1])
                else:
                    merged.append([first, last])
            gaps, cursor = [], start
            for first, last in merged:
                if cursor < first:
                    gaps.append([cursor, first - timedelta(days=1)])
                cursor = max(cursor, last + timedelta(days=1))
            if cursor <= end:
                gaps.append([cursor, end])
            coverage.append({'resource': resource, 'metric': metric,
                             'scope': production_scope if 'Quay' in resource else 'all_yards',
                             'coveredPeriods': [[a.strftime('%Y%m%d'), b.strftime('%Y%m%d')] for a, b in merged],
                             'uncoveredPeriods': [[a.strftime('%Y%m%d'), b.strftime('%Y%m%d')] for a, b in gaps],
                             'fullPeriodCovered': not gaps})
    return {'individualMetrics': coverage,
            'allIndividualMetricsCovered': all(item['fullPeriodCovered'] for item in coverage),
            'groupedComparisonIndexes': [i for i, c in enumerate(entries) if len(c['resources']) > 1],
            'meaning': 'Supplied reference coverage only. Grouped tonnes comparisons do not establish individual endpoint agreement.'}


def verify(transport, credentials, *, start, end, production_scope, reference=None,
           verify_references=False, reference_limit=DEFAULT_PUBLIC_REFERENCE_LIMIT):
    """Fetch all four resources; keep failures explicit and compare only full periods."""
    windows = list(month_windows(start, end))
    if not isinstance(production_scope, str) or production_scope not in SCOPES:
        raise VerificationError('INVALID_SCOPE')
    if (type(verify_references) is not bool or type(reference_limit) is not int
            or not 1 <= reference_limit <= MAX_PUBLIC_REFERENCE_LIMIT):
        raise VerificationError('INVALID_REFERENCE_CHECK_OPTIONS')
    reference = validate_reference(reference, start, end, production_scope)
    if (not isinstance(credentials, dict) or set(credentials) != {'Username', 'Password'}
            or any(not isinstance(v, str) or not v or len(v) > 2048 for v in credentials.values())):
        raise VerificationError('INVALID_CREDENTIALS')
    response = transport.request('POST', '/api/login', json_body=credentials)
    if response.status != 200:
        raise VerificationError('LOGIN_FAILED')
    login = _json(response.body)
    token = login.get('accessToken') if isinstance(login, dict) else None
    if (not isinstance(login, dict) or login.get('code') != '1' or not isinstance(token, str)
            or not re.fullmatch(r'[A-Za-z0-9._~-]{16,4096}', token)):
        raise VerificationError('INVALID_LOGIN_RESPONSE')
    headers = {'Authorization': 'Bearer ' + token}
    del token, login, response
    report = {'format': 'corporate-api-delivery-check-v1',
              'startedAt': datetime.now(timezone.utc).isoformat(),
              'startDate': start.strftime('%Y%m%d'), 'endDate': end.strftime('%Y%m%d'),
              'companyId': 'CNT', 'productionScope': production_scope,
              'scopeVerification': 'operator_declared_not_verified_by_public_api',
              'gateScope': 'all_yards', 'consistentSnapshot': False,
              'reference': None if reference is None else {
                  k: reference[k] for k in ('source', 'approved', 'referenceDate')},
              'windows': [], 'logoutHttp': None}
    try:
        with localcontext() as context:
            context.prec = 60
            for first, last in windows:
                for resource in RESOURCES:
                    report['windows'].append(_fetch_window(transport, headers, resource, first, last, 'CNT',
                        collect_references=verify_references, reference_limit=reference_limit))
            report['comparisons'] = _compare(reference, report['windows'])
            report['referenceCoverage'] = _reference_coverage(reference, start, end, production_scope)
            report['resources'] = {}
            for resource in RESOURCES:
                parts = [w for w in report['windows'] if w['resource'] == resource]
                complete = all(w['complete'] for w in parts)
                metrics = ('tonnes', 'TEU') if resource.startswith('cont') else ('tonnes',)
                report['resources'][resource] = {'complete': complete, 'totals': None if not complete else {
                    metric: str(sum((Decimal(w['totals'][metric]) for w in parts), Decimal(0))) for metric in metrics}}
            report['retrievalComplete'] = all(w['complete'] for w in report['windows'])
            report['suppliedComparisonsMatched'] = bool(reference and all(
                c['status'] == 'match' for c in report['comparisons']))
            report['status'] = ('incomplete' if not report['retrievalComplete'] else
                                'retrieved_without_reference' if reference is None else
                                'supplied_references_matched' if report['suppliedComparisonsMatched'] else 'reference_mismatch')
            if verify_references:
                report['publicReferences'] = _verify_public_references(transport, headers,
                                                                     report['windows'], reference_limit)
            for window in report['windows']:
                window.pop('_daily')
    finally:
        try:
            report['logoutHttp'] = transport.request('POST', '/api/logout', headers=headers).status
        except VerificationError:
            report['logoutError'] = 'LOGOUT_FAILED'
        headers.clear()
    report['finishedAt'] = datetime.now(timezone.utc).isoformat()
    report['limitations'] = [
        'Production pages must share a valid full-result digest; a changed result or snapshot invalidates the resource/month without automatic retry.',
        'Live pages read the source independently. Equal result digests do not establish one database transaction across source queries, resources, or months.',
        'Incomplete resource totals are null. receivedSubtotal is diagnostic and must not replace missing production.',
        'Only explicitly listed reference metrics/scopes are compared; no automatic quay-plus-gate total exists.',
        'Reference approval and production scope are operator declarations, not verified by the public API.',
        'Matching values do not establish receiver field-policy acceptance or readiness for Lakehouse delivery.',
    ]
    if verify_references:
        report['limitations'].append(
            'Public references are deduplicated only within this run; catalogs are separate fresh reads, not a transaction with production. '
            'Null foreign keys are not looked up or approved. Direct cargo category/type pairs have a separate per-window bound equal to the reference limit. '
            'A reference or pair limit blocks affected windows; no automatic retry or row exclusion occurs.')
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--base-url', required=True)
    parser.add_argument('--start', type=date.fromisoformat, required=True)
    parser.add_argument('--end', type=date.fromisoformat, required=True)
    parser.add_argument('--production-scope', choices=SCOPES, required=True)
    parser.add_argument('--benchmark', type=Path)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--credentials-stdin', action='store_true')
    parser.add_argument('--verify-references', action='store_true')
    parser.add_argument('--reference-limit', type=int, default=DEFAULT_PUBLIC_REFERENCE_LIMIT,
                        help='Maximum distinct public catalog GETs per run and category/type pairs per complete window (1-2000; default 200).')
    args = parser.parse_args(argv)
    credentials = {}
    try:
        transport = HttpTransport(args.base_url)
        list(month_windows(args.start, args.end))
        if args.output.exists():
            raise VerificationError('OUTPUT_ALREADY_EXISTS')
        reference = None
        if args.benchmark:
            with args.benchmark.open('rb') as handle:
                raw = handle.read(MAX_REFERENCE_BYTES + 1)
            if len(raw) > MAX_REFERENCE_BYTES:
                raise VerificationError('REFERENCE_TOO_LARGE')
            reference = validate_reference(_json(raw), args.start, args.end, args.production_scope)
        if args.credentials_stdin:
            raw = sys.stdin.read(8193)
            if len(raw) > 8192:
                raise VerificationError('INVALID_CREDENTIALS')
            credentials = _json(raw)
        else:
            credentials = {'Username': os.environ.get('CORPORATE_API_USERNAME'),
                           'Password': os.environ.get('CORPORATE_API_PASSWORD')}
        report = verify(transport, credentials, start=args.start, end=args.end,
                        production_scope=args.production_scope, reference=reference,
                        verify_references=args.verify_references, reference_limit=args.reference_limit)
        args.output.parent.mkdir(parents=True, exist_ok=True)
        with args.output.open('x', encoding='utf-8') as handle:
            json.dump(report, handle, ensure_ascii=False, indent=2, allow_nan=False)
            handle.write('\n')
        summary = {k: report[k] for k in ('status', 'retrievalComplete', 'suppliedComparisonsMatched')}
        if args.verify_references:
            summary['publicReferencesPassed'] = report['publicReferences']['allCompleteWindowsPassed']
        print(json.dumps(summary, ensure_ascii=False))
        references_passed = not args.verify_references or report['publicReferences']['allCompleteWindowsPassed']
        return 0 if (report['retrievalComplete'] and references_passed
                     and (reference is None or report['suppliedComparisonsMatched'])) else 2
    except VerificationError as exc:
        print(str(exc), file=sys.stderr)
    except (OSError, ValueError, TypeError, KeyError):
        print('VERIFICATION_CONFIGURATION_OR_FILE_ERROR', file=sys.stderr)
    finally:
        if isinstance(credentials, dict):
            credentials.clear()
    return 1


if __name__ == '__main__':
    raise SystemExit(main())

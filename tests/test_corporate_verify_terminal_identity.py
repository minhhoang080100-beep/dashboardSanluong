"""Public verifier preserves terminal-qualified IDs without leaking source text.

All transport responses are fixtures; no socket, SQL, Railway or real account.
"""
import json

import pytest

from backend.corporate_api import verify_delivery as verify
from test_corporate_delivery_public_references import Transport, master, run, window
from test_corporate_verify_delivery import CREDENTIALS, TOKEN, page, response, row


def qualified_row(resource, day, prefix):
    item = row(resource, day)
    for field in verify.PUBLIC_REFERENCES:
        if item.get(field) is not None:
            item[field] = prefix + ':' + item[field]
    return item


def production(resource, params):
    return page([qualified_row(resource, params['startDate'], prefix) for prefix in ('CL', 'BT')], params)


def catalog(resource, identity, params, *, type_id=None):
    item = master(resource, identity)
    if resource == 'cargoCategory':
        item['cargoTypeId'] = type_id or identity.split(':', 1)[0] + ':1'
    result = page([item], params)
    result.headers['Cache-Control'] = 'no-store'
    result.headers.pop('X-Result-Digest')
    return result


def test_prefixed_details_are_percent_encoded_once_and_sources_are_not_deduplicated_together():
    transport = Transport(production=production, catalog=catalog)
    report = run(transport)
    assert report['retrievalComplete'] and report['publicReferences']['allCompleteWindowsPassed']
    keys = {(resource, identity) for resource, identity, _ in transport.catalog_calls}
    assert len(keys) == len(transport.catalog_calls) == 12
    assert ('shipDetails', 'CL:10') in keys and ('shipDetails', 'BT:10') in keys
    details = [path for method, path, _ in transport.calls if method == 'GET' and path.count('/') == 3]
    assert '/api/shipDetails/CL%3A10' in details and '/api/shipDetails/BT%3A10' in details
    assert all('%3A' in path and ':' not in path and '%253A' not in path for path in details)
    size_calls = [(path, params) for method, path, params in transport.calls if path == '/api/containerSize']
    assert {params['containerSizeId'] for _, params in size_calls} == {'CL:20', 'BT:20'}
    assert all(params['companyId'] == 'CNT' for _, params in size_calls)
    assert all(item['totals']['tonnes'] == '0.2' for item in report['windows'])
    serialized = json.dumps(report)
    assert TOKEN not in serialized and CREDENTIALS['Password'] not in serialized
    assert 'synthetic-reference-name-never-exported' not in serialized


def test_failure_sample_keeps_exact_prefixed_id_and_never_exposes_response_messages_or_auth_headers():
    def broken(resource, identity, params):
        if (resource, identity) == ('shipDetails', 'BT:10'):
            return response({'data': [], 'code': '0', 'message': TOKEN + CREDENTIALS['Password']}, 503,
                {'X-Error-Code': 'SOURCE_ID_CONFLICT', 'Retry-After': '5', 'Authorization': TOKEN})
        return catalog(resource, identity, params)
    transport = Transport(production=production, catalog=broken)
    report = run(transport)
    assert report['retrievalComplete'] and report['publicReferences']['blockedWindows'] == 2
    for resource in ('contQuayVolumesCB', 'bulkQuayVolumesCB'):
        samples = window(report, resource)['publicReferences']['failureSamples']
        assert samples == [{'resource': 'shipDetails', 'error': 'HTTP_ERROR', 'apiId': 'BT:10',
                            'http': 503, 'serverErrorCode': 'SOURCE_ID_CONFLICT', 'retryAfterSeconds': 5}]
        assert window(report, resource)['totals']['tonnes'] == '0.2'
    assert all(window(report, resource)['publicReferences']['status'] == 'passed'
               for resource in ('contGateVolumesCB', 'bulkGateVolumesCB'))
    assert ('shipDetails', 'CL:10') in {(r, i) for r, i, _ in transport.catalog_calls}
    serialized = json.dumps(report)
    assert TOKEN not in serialized and CREDENTIALS['Password'] not in serialized
    assert 'Authorization' not in serialized


@pytest.mark.parametrize('identity,field', [('1', 'nativeId'), ('CL:1', 'apiId'), ('BT:1', 'apiId'),
                                          ('BT:9223372036854775807', 'apiId')])
def test_numeric_and_terminal_qualified_failure_ids_remain_distinct(identity, field):
    def values(resource, params):
        item = qualified_row(resource, params['startDate'], 'CL')
        item['handlingMethodId'] = identity
        return page([item], params)
    def broken(resource, requested, params):
        if resource == 'handlingMethodList':
            return response({'data': [], 'code': '0', 'message': 'private-driver-text'}, 404,
                            {'X-Error-Code': 'NOT_FOUND'})
        return catalog(resource, requested, params)
    report = run(Transport(production=values, catalog=broken))
    sample, = report['publicReferences']['failureSamples']
    assert sample == {'resource': 'handlingMethodList', 'error': 'HTTP_ERROR', field: identity,
                      'http': 404, 'serverErrorCode': 'NOT_FOUND'}
    assert ('apiId' if field == 'nativeId' else 'nativeId') not in sample
    assert 'private-driver-text' not in json.dumps(report)


@pytest.mark.parametrize('identity', ['CL:' + TOKEN, 'CL:0', 'CL:01', 'cl:1', 'XX:1',
                                    'CL:1:' + TOKEN, 'CL:1/' + TOKEN, 'CL:1?token=' + TOKEN,
                                    'CL:1\n' + TOKEN, 'BT:' + '9' * 20])
def test_noncanonical_or_credential_shaped_id_is_omitted_from_public_failure_samples(identity):
    def values(resource, params):
        item = qualified_row(resource, params['startDate'], 'CL')
        item['handlingMethodId'] = identity
        return page([item], params)
    def broken(resource, requested, params):
        if resource == 'handlingMethodList':
            return response({'data': [], 'code': '0', 'message': 'unsafe-source-' + identity}, 502,
                            {'X-Error-Code': 'unsafe-' + TOKEN, 'Retry-After': TOKEN})
        return catalog(resource, requested, params)
    report = run(Transport(production=values, catalog=broken))
    assert report['retrievalComplete'] and report['publicReferences']['blockedWindows'] == 4
    assert report['publicReferences']['failureSamples'] == [
        {'resource': 'handlingMethodList', 'error': 'HTTP_ERROR', 'http': 502}]
    serialized = json.dumps(report)
    assert identity not in serialized and TOKEN not in serialized and 'unsafe-source-' not in serialized


def test_equal_native_type_numbers_in_different_namespaces_are_a_real_relation_mismatch():
    def wrong_type(resource, identity, params):
        return catalog(resource, identity, params,
            type_id='CL:1' if resource == 'cargoCategory' and identity == 'BT:2' else None)
    report = run(Transport(production=production, catalog=wrong_type))
    assert report['retrievalComplete'] and report['publicReferences']['blockedWindows'] == 2
    for resource in ('bulkQuayVolumesCB', 'bulkGateVolumesCB'):
        checks = window(report, resource)['publicReferences']
        assert checks['checkedRelationCount'] == 2 and checks['mismatchedRelationCount'] == 1
        assert checks['blockedReferenceCount'] == 0
        assert checks['failureSamples'] == [{'resource': 'cargoCategory', 'error': 'REFERENCE_RELATION_MISMATCH',
            'apiId': 'BT:2', 'field': 'cargoTypeId', 'expectedTypeId': 'BT:1', 'actualTypeId': 'CL:1'}]


@pytest.mark.parametrize('unsafe_side', ['expected', 'actual'])
@pytest.mark.parametrize('unsafe', ['CL:' + TOKEN, 'cl:1', 'BT:01', 'BT:1/' + TOKEN, 'XX:1'])
def test_relation_samples_preserve_only_canonical_expected_and_actual_type_ids(unsafe_side, unsafe):
    def values(resource, params):
        item = qualified_row(resource, params['startDate'], 'CL')
        if resource.startswith('bulk') and unsafe_side == 'expected':
            item['cargoTypeId'] = unsafe
        return page([item], params)
    def mapping(resource, identity, params):
        return catalog(resource, identity, params,
                       type_id=unsafe if unsafe_side == 'actual' else None)
    report = run(Transport(production=values, catalog=mapping))
    assert report['publicReferences']['blockedWindows'] == 2
    sample, = report['publicReferences']['failureSamples']
    assert sample['error'] == 'REFERENCE_RELATION_MISMATCH' and sample['apiId'] == 'CL:2'
    omitted = 'expectedTypeId' if unsafe_side == 'expected' else 'actualTypeId'
    retained = 'actualTypeId' if unsafe_side == 'expected' else 'expectedTypeId'
    assert omitted not in sample and sample[retained] == 'CL:1'
    serialized = json.dumps(report)
    assert unsafe not in serialized and TOKEN not in serialized


def test_arbitrary_transport_exception_for_prefixed_detail_is_sanitized():
    def broken(resource, identity, params):
        if resource == 'shipDetails' and identity == 'CL:10':
            raise verify.VerificationError('private-host-' + TOKEN)
        return catalog(resource, identity, params)
    report = run(Transport(production=production, catalog=broken))
    sample, = report['publicReferences']['failureSamples']
    assert sample == {'resource': 'shipDetails', 'error': 'REFERENCE_CHECK_FAILED', 'apiId': 'CL:10'}
    assert TOKEN not in json.dumps(report)


def test_other_terminal_returned_for_exact_detail_is_not_accepted_as_same_identity():
    def wrong_source(resource, identity, params):
        result = catalog(resource, identity, params)
        if resource == 'shipDetails' and identity == 'BT:10':
            body = json.loads(result.body)
            body['data'][0]['shipId'] = 'CL:10'
            result.body = json.dumps(body).encode()
        return result
    report = run(Transport(production=production, catalog=wrong_source))
    sample, = report['publicReferences']['failureSamples']
    assert sample == {'resource': 'shipDetails', 'error': 'REFERENCE_ID_MISMATCH', 'apiId': 'BT:10', 'http': 200}
    assert report['publicReferences']['blockedWindows'] == 2

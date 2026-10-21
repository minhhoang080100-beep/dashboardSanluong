"""Delivery reports retain actionable fixed failure metadata, never server text.

Fake HTTP only; these tests never call a server or configured source database.
"""
import json

import pytest

from backend.corporate_api import verify_delivery as verify
from test_corporate_verify_delivery import FakeTransport, reference, response, run


@pytest.mark.parametrize('code,status', [
    ('DATASET_STALE', 503),
    ('SOURCE_TIME_INVALID', 503),
    ('FRESHNESS_CONFIG_INVALID', 503),
    ('REFERENCE_DATASET_NOT_READY', 503),
    ('REFERENCE_SOURCE_TIME_INVALID', 503),
    ('REFERENCE_METADATA_NOT_READY', 503),
    ('SNAPSHOT_REFERENCES_CHANGED', 409),
    ('FILTER_NOT_READY', 503),
])
def test_current_public_failure_codes_survive_in_report(code, status):
    transport = FakeTransport(lambda resource, params: response(
        {'data': [], 'code': '0', 'message': 'private-source-details'}, status,
        {'X-Error-Code': code}))

    report = run(transport, reference=reference())

    assert report['status'] == 'incomplete'
    assert not report['retrievalComplete'] and not report['suppliedComparisonsMatched']
    assert report['comparisons'][0]['actual'] is None
    assert all(window['error'] == 'HTTP_ERROR' and window['totals'] is None
               and window['pages'][0]['serverErrorCode'] == code
               for window in report['windows'])
    assert all(resource['totals'] is None for resource in report['resources'].values())
    assert 'private-source-details' not in json.dumps(report)
    assert len([call for call in transport.calls if call[0] == 'GET']) == len(verify.RESOURCES)


@pytest.mark.parametrize('header,seconds', [('0', 0), ('5', 5), ('300', 300), ('86400', 86400)])
def test_retry_after_seconds_are_recorded_without_automatic_retry(header, seconds):
    transport = FakeTransport(lambda resource, params: response(
        {'data': [], 'code': '0', 'message': 'private-source-details'}, 503,
        {'x-error-code': 'SOURCE_BUSY', 'rEtRy-AfTeR': header}))

    report = run(transport)

    assert all(window['pages'][0]['retryAfterSeconds'] == seconds
               and window['pages'][0]['serverErrorCode'] == 'SOURCE_BUSY'
               and window['totals'] is None for window in report['windows'])
    assert len([call for call in transport.calls if call[0] == 'GET']) == len(verify.RESOURCES)
    assert report['logoutHttp'] == 200


@pytest.mark.parametrize('header', [None, '', '-1', '1.5', '+5', ' 5 ', '5\r\nPRIVATE',
                                  '999999999', '86401', 'NaN', 'PRIVATE_RETRY_TEXT',
                                  'Wed, 21 Oct 2026 07:28:00 GMT', 5, True])
def test_untrusted_retry_header_is_omitted(header):
    report = run(FakeTransport(lambda resource, params: response(
        {'data': [], 'code': '0', 'message': 'PRIVATE_BODY'}, 503,
        {'X-Error-Code': 'PRIVATE_ERROR', 'Retry-After': header})))

    assert all('retryAfterSeconds' not in window['pages'][0]
               and 'serverErrorCode' not in window['pages'][0]
               and window['totals'] is None for window in report['windows'])
    assert 'PRIVATE' not in json.dumps(report)

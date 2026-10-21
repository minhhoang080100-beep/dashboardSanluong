"""Delivery checks reject wire-type drift after actual ASGI serialization."""
import json

import pytest

from backend.corporate_api import verify_delivery as verify
from test_corporate_delivery_http import (CREDENTIALS, START, END, boundary)  # noqa: F401


MEASURES = [(resource, measure) for resource in verify.RESOURCES
            for measure in (('containerWeight', 'containerTEU')
                            if resource.startswith('cont') else ('bulkWeight',))]


class QuotedMeasureTransport:
    def __init__(self, transport, resource, measure, page=1, replacement=None):
        self.transport, self.resource, self.measure = transport, resource, measure
        self.page, self.replacement = page, replacement
        self.modified = False

    def request(self, method, path, **kwargs):
        response = self.transport.request(method, path, **kwargs)
        if (method == 'GET' and path == '/api/' + self.resource
                and kwargs['params']['page'] == self.page and response.status == 200):
            body = json.loads(response.body)
            # Confirm the application serialized a JSON number before emulating
            # a client-visible schema regression/proxy transform.
            assert type(body['data'][0][self.measure]) in (int, float)
            body['data'][0][self.measure] = (self.replacement if self.replacement is not None
                                            else str(body['data'][0][self.measure]))
            self.modified = True
            return verify.Response(response.status, response.headers, json.dumps(body).encode())
        return response


def run(transport, reference=None):
    return verify.verify(transport, dict(CREDENTIALS), start=START, end=END,
                         production_scope='all_activity', reference=reference)


@pytest.mark.parametrize('resource,measure', MEASURES)
def test_quoted_measure_is_not_accepted_as_complete_delivery(boundary, resource, measure):
    transport = QuotedMeasureTransport(boundary['transport'], resource, measure)
    report = run(transport)
    assert transport.modified
    window = next(item for item in report['windows'] if item['resource'] == resource)
    assert window['error'] == 'INVALID_MEASURE'
    assert window['totals'] is None and window['complete'] is False
    assert window['rowsReceived'] == 0
    assert report['resources'][resource]['totals'] is None
    assert report['retrievalComplete'] is False and report['status'] == 'incomplete'
    assert report['logoutHttp'] == 200


@pytest.mark.parametrize('replacement', ['0', '3.0125e1', '30.125'])
def test_numeric_string_formats_do_not_bypass_wire_number_validation(boundary, replacement):
    report = run(QuotedMeasureTransport(boundary['transport'], 'contQuayVolumesCB',
                                       'containerWeight', replacement=replacement))
    assert report['windows'][0]['error'] == 'INVALID_MEASURE'
    assert report['windows'][0]['totals'] is None


def test_quoted_measure_on_later_page_keeps_accepted_subtotal_diagnostic_only(boundary):
    report = run(QuotedMeasureTransport(boundary['transport'], 'contQuayVolumesCB',
                                       'containerWeight', page=2))
    window = report['windows'][0]
    assert window['error'] == 'INVALID_MEASURE'
    assert window['rowsReceived'] == 1 and window['totals'] is None
    assert window['receivedSubtotal']['tonnes'] == '30.125'
    assert report['resources']['contQuayVolumesCB']['totals'] is None


def test_reference_can_retain_decimal_strings_while_http_uses_json_numbers(boundary):
    reference = {'source': 'MISBI', 'approved': True, 'referenceDate': '2026-10-19',
        'comparisons': [{'startDate': '20260916', 'endDate': '20260917',
            'scope': 'all_activity', 'resources': ['contQuayVolumesCB'], 'metric': 'tonnes',
            'expected': '60.250', 'tolerance': '0.000'}]}
    report = run(boundary['transport'], reference)
    assert report['retrievalComplete'] and report['suppliedComparisonsMatched']
    assert report['comparisons'][0]['difference'] == '0.000'

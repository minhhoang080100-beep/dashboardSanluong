"""Reference verification checks actual ASGI JSON types before model coercion."""
import json

import pytest

from backend.corporate_api import verify_delivery as verify
from test_corporate_api import api  # noqa: F401 -- isolated store, real router/auth
from test_corporate_delivery_http import AppTransport


class MutatedCatalogTransport:
    def __init__(self, transport, resource, field, replacement):
        self.transport = transport
        self.resource, self.field, self.replacement = resource, field, replacement
        self.modified = False

    def request(self, method, path, **kwargs):
        response = self.transport.request(method, path, **kwargs)
        if method == 'GET' and path.startswith('/api/' + self.resource + '/') and response.status == 200:
            body = json.loads(response.body)
            row = body['data'][0]
            target = row['metadata'] if self.field.startswith('metadata.') else row
            field = self.field.removeprefix('metadata.')
            if self.resource == 'shipDetails':
                assert target[field] is None or type(target[field]) in (int, float)
            else:
                assert target[field] is None or (type(target[field]) is int and target[field] in (0, 1))
            # Transform only after FastAPI has emitted the valid wire contract.
            target[field] = self.replacement
            self.modified = True
            return verify.Response(response.status, response.headers, json.dumps(body).encode())
        return response


@pytest.mark.parametrize('resource,field,replacement', [
    ('shipDetails', 'shipLOA', '100.125'),
    ('shipDetails', 'shipBeam', '25.5'),
    ('shipDetails', 'shipGRT', '0'),
    ('shipDetails', 'shipDWT', '2500'),
    ('shipDetails', 'shipLOA', True),
    ('customers', 'isCarrier', '0'),
    ('customers', 'isAgent', True),
    ('customers', 'metadata.isDeleted', 'false'),
])
def test_reference_wire_type_drift_blocks_public_reference_check(api, resource, field, replacement):
    identity = 'SYN-SHIP-1' if resource == 'shipDetails' else 'SYN-CUSTOMER-1'
    transport = MutatedCatalogTransport(AppTransport(api['client']), resource, field, replacement)
    result = verify._fetch_public_reference(transport, api['headers'], resource, identity)
    assert transport.modified
    assert result['http'] == 200
    assert result['error'] == 'INVALID_REFERENCE_ROW'
    assert result['dependencies'] == set()


def test_real_numeric_master_fields_and_integer_flags_pass_without_coercion(api):
    transport = AppTransport(api['client'])
    for resource, identity in [('shipDetails', 'SYN-SHIP-1'), ('customers', 'SYN-CUSTOMER-1')]:
        result = verify._fetch_public_reference(transport, api['headers'], resource, identity)
        assert result['http'] == 200 and result['error'] is None
    ship, customer = [response.json()['data'][0] for response in transport.get_responses]
    assert type(ship['shipLOA']) is float and type(ship['shipGRT']) in (int, float)
    assert ship['shipBeam'] is None and ship['shipDWT'] is None
    assert type(customer['isCarrier']) is int and customer['isCarrier'] == 0
    assert type(customer['metadata']['isDeleted']) is int and customer['metadata']['isDeleted'] == 0
    assert customer['isAgent'] is None

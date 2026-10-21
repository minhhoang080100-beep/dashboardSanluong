"""Filtered production digests identify the whole public result, not a page."""
from copy import deepcopy
import re

import pytest

from backend.corporate_api.contracts import Query
from backend.corporate_api.store import ExportStore
from test_corporate_api import api, fixture_rows, publish  # noqa: F401 - shared isolated HTTP fixture


RESOURCE = 'bulkQuayVolumesCB'


def query(**changes):
    return Query(**{'companyId': 'CNT', 'startDate': '20260901',
                    'endDate': '20260930', **changes})


def rows():
    first = fixture_rows()[RESOURCE][0]
    second = {**deepcopy(first), 'shipId': 'SYN-SHIP-2', 'bulkWeight': 23.5}
    return [first, second]


def digest(store, resource=RESOURCE, **filters):
    result = store.read(resource, query(**filters))
    value = result['pagination']['resultDigest']
    assert re.fullmatch('[0-9a-f]{64}', value)
    assert all('resultDigest' not in row for row in result['data'])
    return value


@pytest.fixture
def store(tmp_path):
    return ExportStore(tmp_path / 'digest.sqlite3')


def test_digest_covers_all_rows_and_is_stable_across_pages_and_limits(store):
    publish(store, RESOURCE, rows())
    first = store.read(RESOURCE, query(limit=1))
    second = store.read(RESOURCE, query(page=2, limit=1,
                                       snapshotId=first['pagination']['snapshotId']))
    whole = store.read(RESOURCE, query(limit=100))
    assert first['data'] != second['data']
    assert len(whole['data']) == 2
    assert first['pagination']['resultDigest'] == second['pagination']['resultDigest'] == whole['pagination']['resultDigest']
    assert re.fullmatch('[0-9a-f]{64}', first['pagination']['resultDigest'])


def test_pinned_published_result_retains_digest_after_new_same_count_publication(store):
    original = rows()
    publish(store, RESOURCE, original)
    first = store.read(RESOURCE, query(limit=1))
    snapshot = first['pagination']['snapshotId']
    assert re.fullmatch('[0-9a-f]{32}', snapshot)
    changed = deepcopy(original)
    changed[0]['bulkWeight'] = 199
    publish(store, RESOURCE, changed)
    pinned = store.read(RESOURCE, query(page=2, limit=1, snapshotId=snapshot))
    current = store.read(RESOURCE, query(limit=100))
    assert pinned['pagination']['snapshotId'] == snapshot
    assert pinned['pagination']['resultDigest'] == first['pagination']['resultDigest']
    assert pinned['pagination']['resultDigest'] != current['pagination']['resultDigest']
    assert current['pagination']['total'] == first['pagination']['total'] == 2
    assert [{key: value for key, value in item.items() if key != 'reportDate'}
            for item in pinned['data']] == [
        {key: value for key, value in original[1].items() if key != 'reportDate'}]


def test_report_date_source_order_and_private_metadata_do_not_change_digest(store):
    data = rows()
    publish(store, RESOURCE, data)
    before = digest(store)
    for row in data:
        row['reportDate'] = '20261014'
        row['_sourceTerminals'] = ['ben_thuy']
        row['_internalAuditReference'] = 'private-fixture-value'
    publish(store, RESOURCE, list(reversed(data)))
    assert digest(store) == before
    result = store.read(RESOURCE, query())
    assert all(not key.startswith('_') for row in result['data'] for key in row)


@pytest.mark.parametrize('change', [
    {'bulkWeight': 12.376},
    {'handlingMethodId': 'SYN-METHOD-2'},
])
def test_same_count_changed_measure_or_business_id_changes_digest(store, change):
    data = rows()
    publish(store, RESOURCE, data)
    before = digest(store)
    data[0].update(change)
    publish(store, RESOURCE, data)
    assert store.read(RESOURCE, query())['pagination']['total'] == 2
    assert digest(store) != before


def test_changes_outside_applied_filters_do_not_change_digest(store):
    data = rows()
    data.append({**deepcopy(data[0]), 'finishDate': '20260917', 'bulkWeight': 50})
    publish(store, RESOURCE, data)
    filters = {'shipId': 'SYN-SHIP-1', 'startDate': '20260916', 'endDate': '20260916'}
    before = digest(store, **filters)
    unfiltered = digest(store)
    data[1]['bulkWeight'] = 100  # Different ship, same day.
    data[2]['bulkWeight'] = 200  # Same ship, different day.
    publish(store, RESOURCE, data)
    assert digest(store, **filters) == before
    assert digest(store) != unfiltered


def test_empty_results_have_valid_stable_digest_and_resource_context(store):
    publish(store, RESOURCE, [])
    first = digest(store)
    publish(store, RESOURCE, [])
    assert digest(store) == first
    publish(store, 'contGateVolumesCB', [])
    assert digest(store, 'contGateVolumesCB') != first


def test_catalog_does_not_expose_a_production_digest(store):
    publish(store, 'shipDetails', fixture_rows()['shipDetails'])
    result = store.read('shipDetails', Query(companyId='CNT'))
    assert 'resultDigest' not in result['pagination']


def test_authenticated_router_exposes_digest_in_header_only_and_omits_catalog_header(api):
    response = api['client'].get('/api/bulkQuayVolumesCB',
        params=query().model_dump(exclude_none=True), headers=api['headers'])
    assert response.status_code == 200
    assert re.fullmatch('[0-9a-f]{64}', response.headers['x-result-digest'])
    assert set(response.json()) == {'data', 'code', 'message'}
    assert 'resultDigest' not in response.text and 'sourceDatabase' not in response.text
    catalog = api['client'].get('/api/shipDetails', params={'companyId': 'CNT'}, headers=api['headers'])
    assert catalog.status_code == 200
    assert 'x-result-digest' not in catalog.headers

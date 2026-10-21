"""A successful FK lookup must agree with the bulk row's category/type pair.

All HTTP is synthetic. No source SQL, deployed service, credentials or source
mapping is accessed or changed by this suite.
"""
from datetime import date
import json

import pytest

from backend.corporate_api import verify_delivery as verify
from test_corporate_delivery_public_references import Transport, catalog_page, run, window
from test_corporate_verify_delivery import CREDENTIALS, TOKEN, page, reference, response, row


def category_type(resource, identity, params, *, type_id='7', parent=None):
    result = catalog_page(resource, identity, params, parent=parent)
    if resource == 'cargoCategory':
        body = json.loads(result.body)
        body['data'][0]['cargoTypeId'] = type_id
        result.body = json.dumps(body).encode()
    return result


def test_existing_public_ids_with_wrong_category_type_block_bulk_only_and_keep_totals():
    transport = Transport(catalog=category_type)
    report = run(transport, reference=reference())
    assert report['retrievalComplete'] and report['suppliedComparisonsMatched']
    assert report['status'] == 'supplied_references_matched'
    assert report['publicReferences']['successfulLookups'] == report['publicReferences']['httpRequests'] == 7
    assert report['publicReferences']['blockedWindows'] == 2
    for resource in verify.RESOURCES:
        item = window(report, resource)
        assert item['totals']['tonnes'] == '0.1'
        references = item['publicReferences']
        if resource.startswith('cont'):
            assert references['status'] == 'passed'
            assert references['checkedRelationCount'] == references['mismatchedRelationCount'] == 0
        else:
            assert references['status'] == 'blocked'
            assert references['blockedReferenceCount'] == 0  # Both IDs resolve; the relation is wrong.
            assert references['checkedRelationCount'] == references['mismatchedRelationCount'] == 1
            assert references['uncheckedRelationCount'] == 0
            assert references['failureSamples'] == [{'resource': 'cargoCategory', 'nativeId': '2',
                'error': 'REFERENCE_RELATION_MISMATCH', 'field': 'cargoTypeId',
                'expectedTypeId': '1', 'actualTypeId': '7'}]


def test_matching_category_type_is_checked_without_extra_http_or_public_rows():
    transport = Transport()
    report = run(transport)
    assert report['publicReferences']['allCompleteWindowsPassed']
    assert len(transport.catalog_calls) == 6
    for resource in ('bulkQuayVolumesCB', 'bulkGateVolumesCB'):
        values = window(report, resource)['publicReferences']
        assert values['checkedRelationCount'] == 1 and values['mismatchedRelationCount'] == 0
        assert values['uncheckedRelationCount'] == 0 and not values['relationCountIsLowerBound']
    assert '_publicReference' not in json.dumps(report)


def test_one_category_used_with_two_types_blocks_only_the_inconsistent_resource():
    def production(resource, params):
        item = row(resource, params['startDate'])
        if resource == 'bulkGateVolumesCB':
            item['cargoTypeId'] = '7'
        return page([item], params)
    report = run(Transport(production=production))
    assert report['publicReferences']['blockedWindows'] == 1
    assert window(report, 'bulkGateVolumesCB')['publicReferences']['mismatchedRelationCount'] == 1
    assert window(report, 'bulkQuayVolumesCB')['publicReferences']['status'] == 'passed'


def test_shared_catalog_cache_checks_each_months_original_expected_relation():
    def production(resource, params):
        item = row(resource, params['startDate'])
        if resource.startswith('bulk') and params['startDate'].startswith('202602'):
            item['cargoTypeId'] = '7'
        return page([item], params)
    transport = Transport(production=production, catalog=category_type)
    report = run(transport, end=date(2026, 2, 28))
    assert report['publicReferences']['blockedWindows'] == 2
    assert sum(resource == 'cargoCategory' for resource, _, _ in transport.catalog_calls) == 1
    for item in report['windows']:
        expected = 'blocked' if item['resource'].startswith('bulk') and item['startDate'] == '20260101' else 'passed'
        assert item['publicReferences']['status'] == expected


def test_multiple_expected_relations_for_one_id_in_a_single_window_are_not_overwritten():
    def production(resource, params):
        first = row(resource, params['startDate'])
        rows = [first, {**first, 'cargoTypeId': '7'}] if resource == 'bulkQuayVolumesCB' else [first]
        return page(rows, params)
    report = run(Transport(production=production))
    item = window(report, 'bulkQuayVolumesCB')
    assert item['totals']['tonnes'] == '0.2'
    assert item['publicReferences']['checkedRelationCount'] == 2
    assert item['publicReferences']['mismatchedRelationCount'] == 1
    assert item['publicReferences']['failureSamples'][0]['expectedTypeId'] == '7'


@pytest.mark.parametrize('status,code', [(404, 'NOT_FOUND'), (503, 'SOURCE_ID_CONFLICT'), (502, None)])
def test_failed_category_lookup_is_unchecked_relation_not_a_fabricated_mismatch(status, code):
    def catalog(resource, identity, params):
        if resource == 'cargoCategory':
            return response({'data': [], 'code': '0', 'message': 'unavailable'}, status,
                            {'X-Error-Code': code} if code else {})
        return catalog_page(resource, identity, params)
    report = run(Transport(catalog=catalog))
    for resource in ('bulkQuayVolumesCB', 'bulkGateVolumesCB'):
        values = window(report, resource)['publicReferences']
        assert values['status'] == 'blocked'
        assert values['checkedRelationCount'] == values['mismatchedRelationCount'] == 0
        assert values['uncheckedRelationCount'] == 1
        assert not any(s['error'] == 'REFERENCE_RELATION_MISMATCH' for s in values['failureSamples'])


def test_limit_preventing_category_lookup_is_not_a_relation_mismatch():
    report = run(reference_limit=1)
    assert report['retrievalComplete'] and report['publicReferences']['limitReached']
    for resource in ('bulkQuayVolumesCB', 'bulkGateVolumesCB'):
        values = window(report, resource)['publicReferences']
        assert values['status'] == 'blocked' and values['mismatchedRelationCount'] == 0
        assert values['checkedRelationCount'] == 0 and values['uncheckedRelationCount'] == 1


@pytest.mark.parametrize('failed_type', ['1', '7'])
def test_failed_type_lookup_cannot_be_reported_as_a_relation_mismatch(failed_type):
    def catalog(resource, identity, params):
        if resource == 'cargoType' and identity == failed_type:
            return response({'data': [], 'code': '0', 'message': 'unavailable'}, 503,
                            {'X-Error-Code': 'SOURCE_ID_CONFLICT'})
        return category_type(resource, identity, params)
    report = run(Transport(catalog=catalog))
    for resource in ('bulkQuayVolumesCB', 'bulkGateVolumesCB'):
        values = window(report, resource)['publicReferences']
        assert values['status'] == 'blocked'
        assert values['checkedRelationCount'] == values['mismatchedRelationCount'] == 0
        assert values['uncheckedRelationCount'] == 1
        assert not any(s['error'] == 'REFERENCE_RELATION_MISMATCH' for s in values['failureSamples'])


def test_parent_category_may_use_another_type_without_an_invented_same_group_rule():
    def catalog(resource, identity, params):
        return category_type(resource, identity, params, type_id='1' if identity == '2' else '7',
                             parent='3' if resource == 'cargoCategory' and identity == '2' else None)
    report = run(Transport(catalog=catalog))
    assert report['publicReferences']['allCompleteWindowsPassed']
    assert window(report, 'bulkQuayVolumesCB')['publicReferences']['checkedRelationCount'] == 1


def test_partial_window_never_compares_its_received_category_type_pairs(monkeypatch):
    monkeypatch.setattr(verify, 'PAGE_SIZE', 1)
    def production(resource, params):
        if resource == 'bulkQuayVolumesCB':
            if params['page'] == 1:
                return page([row(resource, params['startDate'])], params, total=2)
            return response({'data': [], 'code': '0', 'message': 'blocked'}, 503)
        return page([row(resource, params['startDate'])], params)
    report = run(Transport(production=production, catalog=category_type))
    item = window(report, 'bulkQuayVolumesCB')
    assert not item['complete'] and item['totals'] is None
    assert item['publicReferences']['status'] == 'skipped_incomplete'
    assert item['publicReferences']['checkedRelationCount'] == 0
    assert item['publicReferences']['mismatchedRelationCount'] is None


def test_relation_pair_budget_is_bounded_even_when_distinct_ids_fit_in_budget():
    def production(resource, params):
        rows = []
        if resource == 'bulkGateVolumesCB':
            for cargo in range(20, 24):
                for group in range(1, 5):
                    rows.append({**row(resource, params['startDate']), 'cargoCategoryId': str(cargo),
                                 'cargoTypeId': str(group)})
        return page(rows, params)
    transport = Transport(production=production)
    report = run(transport, reference_limit=12)
    item = window(report, 'bulkGateVolumesCB')
    assert item['complete'] and item['rowsReceived'] == 16 and item['totals']['tonnes'] == '1.6'
    assert len(transport.catalog_calls) == 9  # 4 categories + 4 groups + one method.
    values = item['publicReferences']
    assert values['status'] == 'blocked' and values['relationCountIsLowerBound'] is True
    assert values['checkedRelationCount'] <= 12
    assert report['publicReferences']['limitReached']
    assert any(s['error'] == 'REFERENCE_LIMIT_EXCEEDED' for s in values['failureSamples'])
    assert '_publicReference' not in json.dumps(report)


def test_relation_mismatch_does_not_copy_arbitrary_identifiers_or_catalog_values():
    def production(resource, params):
        item = row(resource, params['startDate'])
        if resource.startswith('bulk'):
            item['cargoTypeId'] = TOKEN
        return page([item], params)
    report = run(Transport(production=production, catalog=category_type))
    assert report['publicReferences']['blockedWindows'] == 2
    text = json.dumps(report)
    assert TOKEN not in text and CREDENTIALS['Password'] not in text
    assert 'synthetic-reference-name-never-exported' not in text
    sample = window(report, 'bulkQuayVolumesCB')['publicReferences']['failureSamples'][0]
    assert sample['error'] == 'REFERENCE_RELATION_MISMATCH'
    assert 'expectedTypeId' not in sample


def test_disabled_reference_verification_remains_backward_compatible():
    transport = Transport(catalog=category_type)
    report = run(transport, verify_references=False)
    assert report['retrievalComplete'] and 'publicReferences' not in report
    assert transport.catalog_calls == []

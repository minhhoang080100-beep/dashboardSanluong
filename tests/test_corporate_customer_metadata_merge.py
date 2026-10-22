"""Same-created customer copies may differ only in modification metadata.

Synthetic adapters only; native IDs, lifecycle and creation-date filters remain
strict. In particular this does not resolve real active/deleted collisions.
"""
from copy import deepcopy
from datetime import datetime

import pytest

from backend.corporate_api.catalog_source import extract_catalogs
from backend.corporate_api.contracts import Query
from backend.corporate_api.errors import CorporateError
from test_corporate_catalog_request_scope import reader, customer_profile
from test_corporate_reference_scope import ScopedQuery, NATIVE_PROFILE


CREATED = datetime(2026, 9, 1, 12)
EARLIER = datetime(2026, 9, 2, 9)
LATER = datetime(2026, 9, 20, 14)


@pytest.fixture(autouse=True)
def no_real_sql(monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail('This test must not connect to any SQL Server.')
    monkeypatch.setattr('pyodbc.connect', forbidden)


def source():
    result = ScopedQuery()
    for database, modified in (('SmartTOS', EARLIER), ('SmartTOS_BenThuy', LATER)):
        result.data[database]['Partner'][0].update(
            createTime=CREATED, updateTime=modified, rowDeleted=False)
    return result


@pytest.mark.parametrize('terminals', [('cua_lo', 'ben_thuy'), ('ben_thuy', 'cua_lo')])
def test_same_created_customer_keeps_one_native_id_and_latest_whole_source_pair(terminals):
    query = source()
    before = deepcopy(query.data)
    result = extract_catalogs(query, resources=['customers'],
        profile={**NATIVE_PROFILE, 'terminals': list(terminals)})['customers']
    assert result['ready'], result['blockers']
    assert len(result['rows']) == 1
    row = result['rows'][0]
    assert row['customerCode'] == '20'
    assert row['metadata'] == {
        'isDeleted': 0, 'createdDate': CREATED.isoformat(), 'modifiedDate': LATER.isoformat()}
    assert row['_createdDate'] == '20260901'
    assert query.data == before  # Neither source object was rewritten.
    assert {database for database, _, _ in query.calls} == {'SmartTOS', 'SmartTOS_BenThuy'}


@pytest.mark.parametrize('filters', [
    {}, {'startDate': '20260901', 'endDate': '20260901'},
    {'customerTaxCode': 'TAX-20'}, {'customerType': 'LOGISTICS'},
])
def test_public_list_and_detail_keep_created_day_and_filter_fields(filters):
    query = source()
    profile = customer_profile(query)
    api = reader(query, profile)
    try:
        for identity in (None, '20'):
            result = api.read('customers', Query(companyId='CNT', **filters), identity=identity)
            assert result['pagination']['total'] == 1
            row = result['data'][0]
            assert row['customerCode'] == '20'
            assert row['customerTaxCode'] == 'TAX-20'
            assert row['metadata']['createdDate'] == CREATED.isoformat()
            assert row['metadata']['modifiedDate'] == LATER.isoformat()
            assert not any(key.startswith('_') for key in row)
    finally:
        api.close()


def test_modification_day_does_not_move_customer_into_creation_date_filter():
    query = source()
    api = reader(query)
    try:
        for day in ('20260902', '20260920'):
            result = api.read('customers', Query(companyId='CNT', startDate=day, endDate=day))
            assert result['data'] == [] and result['pagination']['total'] == 0
    finally:
        api.close()


@pytest.mark.parametrize('changed', [
    {'createTime': datetime(2026, 9, 2, 12)},
    {'createTime': datetime(2026, 9, 1, 13)},  # Same day is not the same timestamp.
    {'createTime': None}, {'rowDeleted': True},
    {'partnerFullName': 'Different synthetic customer'},
])
def test_creation_lifecycle_or_business_difference_is_not_metadata_only(changed):
    query = source()
    query.data['SmartTOS_BenThuy']['Partner'][0].update(changed)
    result = extract_catalogs(query, resources=['customers'])['customers']
    assert not result['ready'] and result['rows'] == []
    assert {error['code'] for error in result['blockers']} == {'SOURCE_ID_CONFLICT'}


def test_both_missing_creation_timestamps_do_not_enable_public_merge():
    query = source()
    for database in query.data:
        query.data[database]['Partner'][0]['createTime'] = None
    result = extract_catalogs(query, resources=['customers'])['customers']
    assert not result['ready'] and result['blockers'][0]['code'] == 'SOURCE_ID_CONFLICT'


@pytest.mark.parametrize('field,value', [('verifiedTax', 'OTHER-TAX'), ('verifiedType', 'OTHER')])
def test_modified_date_merge_does_not_hide_another_variant_behind_customer_filter(field, value):
    query = source()
    profile = customer_profile(query)
    query.data['SmartTOS_BenThuy']['Partner'][0][field] = value
    api = reader(query, profile)
    try:
        with pytest.raises(CorporateError) as error:
            api.read('customers', Query(companyId='CNT', customerTaxCode='TAX-20',
                customerType='LOGISTICS', startDate='20260901', endDate='20260901'))
        assert error.value.code == 'SOURCE_ID_CONFLICT'
    finally:
        api.close()


@pytest.mark.parametrize('database', ['SmartTOS', 'SmartTOS_BenThuy'])
def test_metadata_merge_never_substitutes_for_an_unavailable_terminal(database):
    query = source()
    query.fail_db = database
    result = extract_catalogs(query, resources=['customers'])['customers']
    assert not result['ready'] and result['rows'] == []
    assert result['blockers'][0]['code'] == 'SOURCE_UNAVAILABLE'

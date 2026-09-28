from datetime import datetime, timezone
from decimal import Decimal

import pytest

from backend.corporate_api.contracts import Query
from backend.corporate_api.errors import CorporateError
from backend.corporate_api.store import ExportStore

READ_AT = '2026-09-21T10:00:00+07:00'


def ship(number, name=None):
    return dict(reportDate='20260921', shipId=f'CNT-CL-{number}', shipFullName=name or f'Synthetic ship {number}',
                shipIMO=None, shipGroup=None, flagState=None, shipLOA=None, shipBeam=None,
                shipGRT=None, shipType=None, shipDWT=None, shipOwner=None, createdDate=None, modifiedDate=None)


def bulk(day='20260916', weight='8011', ship_id='CNT-CL-1'):
    return dict(reportDate='20260921', finishDate=day, companyId='CNT', shipId=ship_id,
                shipAgentId=None, cargoTypeId='BULK', cargoCategoryId='CNT-CL-1',
                handlingMethodId='CNT-CL-2', shipClassId='CNT-CL-1', bulkOriginId=None,
                bulkWeight=Decimal(weight))


def publish(store, resource, rows, **kwargs):
    return store.publish(resource, 'CNT', rows, read_at=READ_AT, rule_version='test-v1', **kwargs)


def test_snapshot_pagination_does_not_shift_during_publication(tmp_path):
    store = ExportStore(tmp_path / 'exports.sqlite3')
    version = publish(store, 'shipDetails', [ship(2), ship(1), ship(3)])
    first = store.read('shipDetails', Query(companyId='CNT', limit=1))
    assert first['data'][0]['shipId'] == 'CNT-CL-1'
    assert first['pagination']['total'] == 3 and first['pagination']['hasNext']
    publish(store, 'shipDetails', [ship(9)])
    second = store.read('shipDetails', Query(companyId='CNT', limit=1, page=2, snapshotId=version['snapshotId']))
    assert second['data'][0]['shipId'] == 'CNT-CL-2'
    assert second['pagination']['total'] == 3
    with pytest.raises(CorporateError, match='snapshotId') as error:
        store.read('shipDetails', Query(companyId='CNT', page=2))
    assert error.value.status == 409


def test_unknown_dataset_and_uncovered_period_are_not_empty_success(tmp_path):
    store = ExportStore(tmp_path / 'exports.sqlite3')
    query = Query(companyId='CNT', startDate='20260901', endDate='20260930')
    with pytest.raises(CorporateError) as error:
        store.read('bulkQuayVolumesCB', query)
    assert error.value.code == 'DATASET_NOT_READY'
    publish(store, 'bulkQuayVolumesCB', [], coverage=[['20260901', '20260915'], ['20260917', '20260930']])
    with pytest.raises(CorporateError) as error:
        store.read('bulkQuayVolumesCB', query)
    assert error.value.code == 'PERIOD_NOT_READY'
    publish(store, 'bulkQuayVolumesCB', [], coverage=[['20260901', '20260915'], ['20260916', '20260930']])
    assert store.read('bulkQuayVolumesCB', query)['data'] == []


def test_bulk_precision_nulls_and_filtering(tmp_path):
    store = ExportStore(tmp_path / 'exports.sqlite3')
    publish(store, 'bulkQuayVolumesCB', [bulk(weight='8011.125'), bulk('20260917', '0', 'CNT-BT-1')],
            coverage=[['20260916', '20260917']], warnings=['origin chưa có ánh xạ'])
    result = store.read('bulkQuayVolumesCB', Query(companyId='CNT', startDate='20260916', endDate='20260917', shipId='CNT-CL-1'))
    assert len(result['data']) == 1
    assert result['data'][0]['bulkWeight'] == 8011.125
    assert result['data'][0]['bulkOriginId'] is None
    assert result['pagination']['warnings'] == ['origin chưa có ánh xạ']


@pytest.mark.parametrize('change', [{'bulkWeight': None}, {'bulkWeight': -1}, {'bulkWeight': 'NaN'},
                                   {'companyId': 'OTHER'}, {'finishDate': '20260230'}])
def test_invalid_publication_preserves_current_version(tmp_path, change):
    store = ExportStore(tmp_path / 'exports.sqlite3')
    version = publish(store, 'bulkQuayVolumesCB', [bulk()], coverage=[['20260901', '20260930']])
    with pytest.raises(ValueError):
        publish(store, 'bulkQuayVolumesCB', [{**bulk(), **change}], coverage=[['20260901', '20260930']])
    assert store.describe()[0]['snapshot_id'] == version['snapshotId']


def test_duplicate_dimensions_and_keys_rejected(tmp_path):
    store = ExportStore(tmp_path / 'exports.sqlite3')
    with pytest.raises(ValueError, match='Duplicate'):
        publish(store, 'shipDetails', [ship(1), ship(1)])
    with pytest.raises(ValueError, match='Duplicate'):
        publish(store, 'bulkQuayVolumesCB', [bulk(weight='1'), bulk(weight='2')], coverage=[['20260916', '20260916']])


def test_retention_and_cross_resource_snapshot(tmp_path):
    store = ExportStore(tmp_path / 'exports.sqlite3')
    old = publish(store, 'shipDetails', [ship(1)], retain=2)
    for number in [2, 3]:
        publish(store, 'shipDetails', [ship(number)], retain=2)
    with pytest.raises(CorporateError) as error:
        store.read('shipDetails', Query(companyId='CNT', snapshotId=old['snapshotId']))
    assert error.value.status == 410
    version = store.describe()[0]['snapshot_id']
    with pytest.raises(CorporateError):
        store.read('origins', Query(companyId='CNT', snapshotId=version))


def test_optional_unsupported_filter_is_rejected(tmp_path):
    with pytest.raises(CorporateError):
        Query(companyId='CNT', shipId='x').check_resource('contGateVolumesCB')
    with pytest.raises(CorporateError):
        Query(companyId='CNT', startDate='20260901').check_resource('customers')


def test_reextract_replaces_corrected_and_deleted_rows_but_keeps_other_days(tmp_path):
    store = ExportStore(tmp_path / 'exports.sqlite3')
    params = dict(company_id='CNT', read_at=READ_AT, rule_version='test-v1')
    store.publish_period('bulkQuayVolumesCB', rows=[bulk()], start='20260916', end='20260916', **params)
    store.publish_period('bulkQuayVolumesCB', rows=[bulk('20260917', '12')], start='20260917', end='20260917', **params)
    query = Query(companyId='CNT', startDate='20260916', endDate='20260917')
    assert store.read('bulkQuayVolumesCB', query)['pagination']['total'] == 2
    store.publish_period('bulkQuayVolumesCB', rows=[], start='20260916', end='20260916', **params)
    assert [row['finishDate'] for row in store.read('bulkQuayVolumesCB', query)['data']] == ['20260917']
    with pytest.raises(CorporateError, match='thay đổi'):
        store.publish_period('bulkQuayVolumesCB', rows=[], start='20260916', end='20260916',
                             **{**params, 'rule_version': 'changed'})


def test_multi_resource_publication_rolls_back_on_later_failure(tmp_path):
    store = ExportStore(tmp_path / 'exports.sqlite3')
    old = publish(store, 'shipDetails', [ship(1)])
    with pytest.raises(ValueError):
        with store.atomic_publication():
            publish(store, 'shipDetails', [ship(2)])
            publish(store, 'bulkQuayVolumesCB', [bulk(), bulk()], coverage=[['20260916', '20260916']])
    assert store.describe()[0]['snapshot_id'] == old['snapshotId']


def test_customer_filter_uses_source_created_day_not_latest_modified_day(tmp_path):
    store = ExportStore(tmp_path / 'exports.sqlite3')
    row = dict(reportDate='20260921', customerCode='CNT-CL-1', customerNameVN='Synthetic customer',
               customerNameEN=None, customerTaxCode='tax-example', customerPhoneNum=None, customerAddress=None,
               customerEmail=None, isCarrier=None, isAgent=None, customerStatus=None,
               metadata={'isDeleted': False, 'createdDate': '2026-01-01', 'modifiedDate': '2026-09-16'},
               _changedDate='20260916', _customerType='CARRIER')
    publish(store, 'customers', [row])
    query = Query(companyId='CNT', startDate='20260916', endDate='20260916', customerTaxCode='tax-example')
    assert store.read('customers', query)['pagination']['total'] == 0
    result = store.read('customers', Query(companyId='CNT', startDate='20260101', endDate='20260101'))
    assert result['pagination']['total'] == 1
    assert result['data'][0]['metadata'] == row['metadata']

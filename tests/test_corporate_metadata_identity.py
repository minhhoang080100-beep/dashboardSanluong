"""Opt-in same-business date variants; synthetic data and isolated stores only."""
from copy import deepcopy
from datetime import date, datetime, timezone

import pytest

from backend.corporate_api import manage_exports
from backend.corporate_api.errors import CorporateError
from backend.corporate_api.metadata_identity import POLICY, VARIANTS_KEY, merge_rows
from backend.corporate_api.operation_contracts import OperationQuery
from backend.corporate_api.operation_source import extract_operation_catalogs
from backend.corporate_api.store import ExportStore
from test_corporate_operation_source import Query
from test_corporate_operation_api import api

RESOURCE = 'oprt.cargoDirect'
START, END = date(2026, 9, 1), date(2026, 9, 30)


def profile():
    return {'approved': True, 'operation_metadata_merge': {RESOURCE: POLICY}}


def sources(rows=1):
    source = Query()
    for db, changed in [('SmartTOS', datetime(2026, 9, 14)), ('SmartTOS_BenThuy', datetime(2026, 9, 16))]:
        original = source.data[db]['CargoDirect'][0]
        source.data[db]['CargoDirect'] = [dict(original, cargoDirectId=index, updateTime=changed)
                                         for index in range(1, rows + 1)]
    return source


def extract(source=None, configured=None):
    return manage_exports.extract(profile() if configured is None else configured, START, END,
                                  [RESOURCE], source or sources())


def public_query(start='20260901', end='20260930', **kwargs):
    return OperationQuery(companyId='CNT', startDate=start, endDate=end, **kwargs)


def publish(store, source=None):
    draft = extract(source)
    published = manage_exports.publish_preview(draft, profile(), store, [RESOURCE])
    return draft, published[RESOURCE]


def test_default_is_strict_and_opt_in_preserves_both_actual_date_pairs():
    strict = extract(configured={'approved': True})['datasets'][RESOURCE]
    assert not strict['ready'] and strict['rows'] == []
    assert strict['blockers'][0]['code'] == 'SOURCE_ID_CONFLICT'
    merged = extract()['datasets'][RESOURCE]
    assert merged['ready'] and len(merged['rows']) == 1
    assert merged['metadata_merged_id_count'] == 1
    row = merged['rows'][0]
    assert row['cargoDirectId'] == '1'
    assert row[VARIANTS_KEY] == [
        {'createdDate': '2025-01-01T00:00:00', 'modifiedDate': '2026-09-14T00:00:00'},
        {'createdDate': '2025-01-01T00:00:00', 'modifiedDate': '2026-09-16T00:00:00'},
    ]
    assert row['modifiedDate'] == '2026-09-16T00:00:00'


@pytest.mark.parametrize('changes', [{'approved': False},
    {'operation_metadata_merge': {RESOURCE: 'pick_cua_lo'}},
    {'operation_metadata_merge': {'class': POLICY}}, {'operation_metadata_merge': []}])
def test_invalid_or_unapproved_policy_never_reads_source(changes):
    source = sources()
    with pytest.raises(ValueError):
        extract(source, {**profile(), **changes})
    assert source.calls == []


@pytest.mark.parametrize('field,value', [('cargoDirectCode', 'DIFFERENT'), ('cargoDirectName', None),
                                        ('rowDeleted', True), ('rowDeleted', None)])
def test_opt_in_never_resolves_business_or_null_or_lifecycle_conflict(field, value):
    source = sources()
    source.data['SmartTOS_BenThuy']['CargoDirect'][0][field] = value
    result = extract(source)['datasets'][RESOURCE]
    assert not result['ready'] and result['rows'] == []
    assert result['blockers'][0]['code'] == 'SOURCE_ID_CONFLICT'


@pytest.mark.parametrize('field,left_value,right_value', [
    ('isUpdated', 0, 1), ('equipmentTypeId', '1', '2'),
    ('teu', None, 2), ('isDeleted', None, 0),
])
def test_merge_compares_every_non_date_field_including_fk_measure_and_update_flag(field, left_value, right_value):
    row = extract()['datasets'][RESOURCE]['rows'][0]
    left = {**row, field: left_value}
    right = {**row, field: right_value}
    assert merge_rows(RESOURCE, left, right, POLICY) is None


def test_policy_is_resource_specific():
    result = extract(configured={'approved': True, 'operation_metadata_merge': {'oprt.jobType': POLICY}})
    assert not result['datasets'][RESOURCE]['ready']


def test_opt_in_never_fills_missing_source_dates_or_missing_database():
    source = sources()
    source.data['SmartTOS_BenThuy']['CargoDirect'][0].update(createTime=None, updateTime=None)
    result = extract(source)['datasets'][RESOURCE]
    assert not result['ready'] and result['rows'] == []
    assert result['blockers'][0]['code'] == 'SOURCE_CHANGE_DATE_MISSING'
    source = sources()
    source.fail_db = 'SmartTOS_BenThuy'
    assert not extract(source)['datasets'][RESOURCE]['ready']


def test_metadata_merge_does_not_depend_on_source_iteration_order():
    left = extract_operation_catalogs(sources(), resources=[RESOURCE], profile=profile(), report_date=END)
    right = extract_operation_catalogs(sources(), resources=[RESOURCE],
        profile={**profile(), 'terminals': ['ben_thuy', 'cua_lo']}, report_date=END)
    assert left[RESOURCE]['rows'] == right[RESOURCE]['rows']


def test_each_date_filter_returns_one_actual_matching_pair_and_wide_range_counts_id_once(tmp_path):
    store = ExportStore(tmp_path / 'exports.sqlite3')
    draft, _ = publish(store)
    pairs = draft['datasets'][RESOURCE]['rows'][0][VARIANTS_KEY]
    for day, expected in [('20260914', '2026-09-14T00:00:00'), ('20260916', '2026-09-16T00:00:00')]:
        result = store.read(RESOURCE, public_query(day, day))
        assert result['pagination']['total'] == 1
        assert result['data'][0]['modifiedDate'] == expected
        assert {key: result['data'][0][key] for key in ('createdDate', 'modifiedDate')} in pairs
    assert store.read(RESOURCE, public_query())['pagination']['total'] == 1
    assert store.read(RESOURCE, public_query('20260915', '20260915'))['data'] == []
    created = store.read(RESOURCE, public_query('20250101', '20250101'))
    assert created['pagination']['total'] == 1  # Created OR modified, not latest only.


def test_timezone_day_filter_uses_original_pair_without_invented_timestamp(tmp_path):
    source = sources()
    actual = datetime(2026, 9, 14, 23, tzinfo=timezone.utc)
    source.data['SmartTOS']['CargoDirect'][0]['updateTime'] = actual
    store = ExportStore(tmp_path / 'exports.sqlite3')
    publish(store, source)
    row = store.read(RESOURCE, public_query('20260915', '20260915'))['data'][0]
    assert row['modifiedDate'] == '2026-09-14T23:00:00Z'
    assert store.read(RESOURCE, public_query('20260914', '20260914'))['data'] == []


def test_hidden_date_variant_changes_snapshot_digest_and_old_pages_stay_immutable(tmp_path):
    store = ExportStore(tmp_path / 'exports.sqlite3')
    _, first = publish(store, sources(3))
    page1 = store.read(RESOURCE, public_query('20260914', '20260914', limit=1))
    source = sources(3)
    for row in source.data['SmartTOS']['CargoDirect']:
        row['updateTime'] = datetime(2026, 9, 13)
    _, second = publish(store, source)
    assert second['digest'] != first['digest']  # Latest public pair stays Sep16.
    page2 = store.read(RESOURCE, public_query('20260914', '20260914', page=2, limit=1,
        snapshotId=page1['pagination']['snapshotId']))
    assert page2['data'][0]['cargoDirectId'] == '2'
    assert page2['data'][0]['modifiedDate'] == '2026-09-14T00:00:00'
    assert page2['pagination']['total'] == 3
    assert store.read(RESOURCE, public_query('20260913', '20260913',
        snapshotId=first['snapshotId']))['data'] == []
    assert store.read(RESOURCE, public_query('20260914', '20260914',
        snapshotId=first['snapshotId']))['pagination']['total'] == 3
    assert store.read(RESOURCE, public_query('20260914', '20260914'))['data'] == []
    assert store.read(RESOURCE, public_query('20260913', '20260913'))['pagination']['total'] == 3


def test_digest_is_independent_of_variant_and_row_order(tmp_path):
    store = ExportStore(tmp_path / 'exports.sqlite3')
    draft = extract(sources(3))
    first = manage_exports.publish_preview(draft, profile(), store, [RESOURCE])[RESOURCE]
    reordered = deepcopy(draft)
    reordered['datasets'][RESOURCE]['rows'].reverse()
    for row in reordered['datasets'][RESOURCE]['rows']:
        row[VARIANTS_KEY].reverse()
    second = manage_exports.publish_preview(reordered, profile(), store, [RESOURCE])[RESOURCE]
    assert first['digest'] == second['digest']
    assert first['snapshotId'] != second['snapshotId']


def test_preview_variants_cannot_bypass_explicit_resource_policy(tmp_path):
    store = ExportStore(tmp_path / 'exports.sqlite3')
    draft = extract()
    strict = {'approved': True}
    draft['profileDigest'] = manage_exports.profile_digest(strict)
    with pytest.raises(ValueError, match='opt-in'):
        manage_exports.publish_preview(draft, strict, store, [RESOURCE])
    assert store.describe() == []
    with pytest.raises(ValueError, match='opt-in'):
        store.publish(RESOURCE, 'CNT', draft['datasets'][RESOURCE]['rows'],
                      read_at=draft['sourceReadAt'], rule_version='no-policy')


@pytest.mark.parametrize('mutate', [
    lambda row: row[VARIANTS_KEY].append(deepcopy(row[VARIANTS_KEY][0])),
    lambda row: row[VARIANTS_KEY][0].update(cargoDirectCode='forged-business-value'),
    lambda row: row[VARIANTS_KEY][0].update(createdDate=None, modifiedDate=None),
    lambda row: row[VARIANTS_KEY][0].update(modifiedDate='bad-date'),
    lambda row: row.update(createdDate='2030-01-01T00:00:00'),
])
def test_malformed_or_forged_private_variants_are_rejected_at_publication(tmp_path, mutate):
    store = ExportStore(tmp_path / 'exports.sqlite3')
    draft = extract()
    mutate(draft['datasets'][RESOURCE]['rows'][0])
    with pytest.raises(ValueError):
        manage_exports.publish_preview(draft, profile(), store, [RESOURCE])
    assert store.describe() == []


@pytest.mark.parametrize('value', ['20260925', '2026-09-25', '2026-09-25 12:00:00',
    '2026-09-25T12:00:00+00:00', '2026-09-25T12:00:00+0700', '1790308800'])
def test_noncanonical_private_timestamp_cannot_bypass_wire_datetime_serializer(tmp_path, value):
    store = ExportStore(tmp_path / 'exports.sqlite3')
    draft = extract()
    draft['datasets'][RESOURCE]['rows'][0][VARIANTS_KEY][0]['modifiedDate'] = value
    with pytest.raises(ValueError, match='variant timestamp'):
        manage_exports.publish_preview(draft, profile(), store, [RESOURCE])
    assert store.describe() == []


def test_public_http_contains_only_contract_and_an_actual_matching_pair(api):
    draft, _ = publish(api['exports'])
    path = '/api/oprt/catalog/cargoDirect'
    for day in ('20260914', '20260916'):
        response = api['client'].get(path, params={'companyId': 'CNT', 'startDate': day, 'endDate': day},
                                      headers=api['headers'])
        assert response.status_code == 200, response.text
        body = response.json()
        assert set(body) == {'data', 'code', 'message'}
        assert len(body['data']) == 1 and response.headers['X-Total-Count'] == '1'
        assert body['data'][0]['modifiedDate'][:10].replace('-', '') == day
        assert VARIANTS_KEY not in response.text and 'sourceDatabase' not in response.text


def test_snapshot_cleanup_cascades_private_variants(tmp_path):
    store = ExportStore(tmp_path / 'exports.sqlite3')
    draft = extract()
    snapshots = []
    for _ in range(3):
        snapshots.append(store.publish(RESOURCE, 'CNT', draft['datasets'][RESOURCE]['rows'],
            read_at=draft['sourceReadAt'], rule_version='v1', retain=2, metadata_merge_policy=POLICY)['snapshotId'])
    with store.db() as db:
        assert db.execute('SELECT COUNT(*) FROM export_row_date_variants WHERE snapshot_id=?',
                          (snapshots[0],)).fetchone()[0] == 0
        assert db.execute('SELECT COUNT(*) FROM export_row_date_variants').fetchone()[0] == 4
    with pytest.raises(CorporateError) as expired:
        store.read(RESOURCE, public_query(snapshotId=snapshots[0]))
    assert expired.value.code == 'SNAPSHOT_EXPIRED'


def test_atomic_publication_rolls_back_private_variants_together_with_rows(tmp_path):
    store = ExportStore(tmp_path / 'exports.sqlite3')
    draft = extract()
    with pytest.raises(ValueError, match='later validation failed'):
        with store.atomic_publication():
            store.publish(RESOURCE, 'CNT', draft['datasets'][RESOURCE]['rows'],
                read_at=draft['sourceReadAt'], rule_version='v1', metadata_merge_policy=POLICY)
            raise ValueError('later validation failed')
    assert store.describe() == []
    with store.db() as db:
        assert db.execute('SELECT COUNT(*) FROM export_row_date_variants').fetchone()[0] == 0

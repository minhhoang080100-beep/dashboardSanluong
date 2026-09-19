"""Plan governance and explicit cumulative milestones; temporary state only."""
import json
import sqlite3

import pytest
from fastapi.testclient import TestClient

from backend.control_store import ControlError, ControlStore, milestone_pace
from backend.main import app
from test_control_store import state, account, plan, PASSWORD


def approved(store, actor, **changes):
    row = store.create_plan(actor, **plan(**changes))
    return store.approve_plan(actor, row['id'], row['revision'])


def user_with_permissions(store, admin, username, permissions, terminals=None):
    created = store.create_user(admin, username=username, display_name=username, role='manager',
                                terminals=terminals or ['cua_lo'], plan_permissions=permissions)
    first = store.login(username, created['temporary_password'])
    return store.change_password(first['token'], created['temporary_password'], PASSWORD)


def test_plan_filters_apply_before_count_and_page_without_reviving_tombstoned_approvals(state):
    store, admin, _, _ = state
    old = approved(store, admin, reference='Document 100%_A')
    newest = approved(store, admin, reference='Document 100%_A')
    store.delete_plan(admin, newest['id'], newest['revision'])
    draft = store.create_plan(admin, **plan(reference='ĐIỀU CHỈNH'))
    live = approved(store, admin, month='2026-08', reference='Document 100%_A')
    assert store.list_plans(admin, version_scope='current')['items'][0]['id'] == live['id']
    assert store.list_plans(admin, version_scope='current', month='2026-09', include_deleted=True)['total'] == 0
    previous = store.list_plans(admin, version_scope='previous', q='100%_A', page_size=1)
    assert previous['total'] == 1 and previous['items'][0]['id'] == old['id']
    assert store.list_plans(admin, version_scope='previous', q='100%_A', page=2, page_size=1)['items'] == []
    assert store.list_plans(admin, q='100%_A', include_deleted=True)['total'] == 3
    assert store.list_plans(admin, q='100_A')['total'] == 0
    assert store.list_plans(admin, q='điều chỉnh')['total'] == 1
    assert store.list_plans(admin, status='draft', version_scope='current')['total'] == 0
    assert store.list_plans(admin, status='draft')['items'][0]['id'] == draft['id']
    for filters in [{'q': 'x' * 201}, {'version_scope': 'latest'}]:
        with pytest.raises(ControlError):
            store.list_plans(admin, **filters)


def test_milestones_are_draft_version_content_and_survive_approval_history_and_deletion(state):
    store, admin, _, _ = state
    milestones = [{'date': '2026-09-10', 'amount': '40.125'}, {'date': '2026-09-30', 'amount': '100'}]
    row = store.create_plan(admin, **plan(amount='100', milestones=milestones))
    assert row['milestones'] == milestones
    with pytest.raises(ControlError) as failure:
        store.update_plan(admin, row['id'], row['revision'], amount='99')
    assert failure.value.code == 'INVALID_PLAN_MILESTONES'
    changed = store.update_plan(admin, row['id'], row['revision'], amount='80', milestones=milestones[:1])
    frozen = store.approve_plan(admin, changed['id'], changed['revision'])
    assert frozen['milestones'] == milestones[:1]
    assert store.get_plan(admin, row['id'])['history'][0]['snapshot']['milestones'] == milestones
    with sqlite3.connect(store.path) as db, pytest.raises(sqlite3.IntegrityError):
        db.execute("UPDATE plans SET milestones_json='[]' WHERE id=?", (row['id'],))
    deleted = store.delete_plan(admin, frozen['id'], frozen['revision'])
    assert deleted['milestones'] == milestones[:1]


@pytest.mark.parametrize('milestones', [None, {}, [{'date': '2026-08-31', 'amount': '1'}],
    [{'date': '2026-09-31', 'amount': '1'}], [{'date': '2026-09-01', 'amount': '-1'}],
    [{'date': '2026-09-01', 'amount': '101'}], [{'date': '2026-09-01', 'amount': '0.0000001'}],
    [{'date': '2026-09-01', 'amount': 'NaN'}], [{'date': '2026-09-01', 'amount': '1', 'label': 'extra'}],
    [{'date': '2026-09-02', 'amount': '1'}, {'date': '2026-09-01', 'amount': '2'}],
    [{'date': '2026-09-01', 'amount': '2'}, {'date': '2026-09-02', 'amount': '1'}],
    [{'date': '2026-09-01', 'amount': '1'}, {'date': '2026-09-01', 'amount': '2'}]])
def test_invalid_milestones_are_rejected_before_storage(milestones):
    with pytest.raises(ControlError) as failure:
        ControlStore.validate_plan(plan(amount='100', milestones=milestones))
    assert failure.value.code == 'INVALID_PLAN_MILESTONES'


def test_voyage_milestones_are_rejected_and_legacy_targets_have_no_implicit_schedule():
    with pytest.raises(ControlError):
        ControlStore.validate_plan(plan(period_type='voyage', month=None, voyage_id=123,
            milestones=[{'date': '2026-09-01', 'amount': '1'}]))
    assert json.loads(ControlStore.validate_plan(plan())['milestones_json']) == []


def pace_fixture():
    period = {'period_start': '2026-09-01', 'period_end': '2026-09-30'}
    plans = [{'milestones': [{'date': '2026-09-02', 'amount': '20'}, {'date': '2026-09-04', 'amount': '100'}]}]
    report = {'meta': {'filters': {'end_date': '2026-09-03'}}, 'daily_history': [
        {'date': '2026-09-01', 'tonnage': 6, 'tonnage_status': 'ready'},
        {'date': '2026-09-02', 'tonnage': 7, 'tonnage_status': 'ready'},
        {'date': '2026-09-03', 'tonnage': 200, 'tonnage_status': 'ready'}]}
    return report, period, plans


def test_pace_uses_actual_only_through_latest_approved_milestone_not_report_end():
    report, period, plans = pace_fixture()
    result = milestone_pace(report, period, plans)
    assert result == {'status': 'ready', 'reason': None, 'milestone_date': '2026-09-02',
                      'target': 20, 'actual': 13, 'difference': -7, 'completion_percent': 65}
    # Unavailable later actuals do not invalidate complete actuals at the checkpoint.
    report['daily_history'][-1].update(tonnage=None, tonnage_status='unavailable')
    assert milestone_pace(report, period, plans) == result


@pytest.mark.parametrize('mutation', ['missing', 'partial', 'duplicate', 'negative', 'missing_status'])
def test_pace_is_unknown_for_missing_or_partial_coverage(mutation):
    report, period, plans = pace_fixture()
    if mutation == 'missing':
        report['daily_history'].pop(0)
    elif mutation == 'duplicate':
        report['daily_history'].append(report['daily_history'][0])
    elif mutation == 'missing_status':
        del report['daily_history'][0]['tonnage_status']
    else:
        report['daily_history'][0].update(tonnage_status='partial' if mutation == 'partial' else 'ready',
                                           tonnage=-1 if mutation == 'negative' else 6)
    result = milestone_pace(report, period, plans)
    assert result['status'] == 'unknown' and result['actual'] is None and result['completion_percent'] is None


def test_company_pace_requires_common_explicit_milestone_no_interpolation():
    report, period, plans = pace_fixture()
    plans.append({'milestones': [{'date': '2026-09-03', 'amount': '30'}]})
    assert milestone_pace(report, period, plans)['status'] == 'unknown'
    plans[-1]['milestones'].insert(0, {'date': '2026-09-02', 'amount': '10'})
    result = milestone_pace(report, period, plans)
    assert result['target'] == 30 and result['actual'] == 13
    assert milestone_pace(report, period, plans, complete_target=False)['status'] == 'unknown'


def test_endpoint_progress_includes_pace_without_changing_total_completion(state):
    store, admin, _, _ = state
    approved(store, admin, amount='100', milestones=[{'date': '2026-09-02', 'amount': '20'}])
    report, _, _ = pace_fixture()
    report['meta'].update(report_id='synthetic', berth_rule_version='initial-berth-v1')
    report['meta']['filters'].update(start_date='2026-09-01', terminal='cua_lo', production_scope='nghe_tinh')
    report['overview'] = {'total_tonnage': 213, 'tonnage_status': 'ready'}
    item = store.throughput_progress(admin, report)['items'][0]
    assert item['actual'] == 213 and item['completion_percent'] == 213
    assert item['pace']['actual'] == 13 and item['pace']['completion_percent'] == 65


def test_distinct_create_approve_permissions_and_revocation_are_enforced_in_store(state):
    store, admin, _, _ = state
    creator = user_with_permissions(store, admin, 'creator', ['create'])
    approver = user_with_permissions(store, admin, 'approver', ['approve'])
    row = store.create_plan(creator['user'], **plan())
    assert row['can_approve'] is False and row['approval_block_reason']
    assert store.get_plan(approver['user'], row['id'])['can_approve'] is True
    with pytest.raises(ControlError) as failure:
        store.approve_plan(creator['user'], row['id'], row['revision'])
    assert failure.value.code == 'PLAN_PERMISSION_REQUIRED'
    for action in [lambda: store.create_plan(approver['user'], **plan()),
                   lambda: store.update_plan(approver['user'], row['id'], row['revision'], amount=1),
                   lambda: store.cancel_plan(approver['user'], row['id'], row['revision'], 'test'),
                   lambda: store.delete_plan(approver['user'], row['id'], row['revision'])]:
        with pytest.raises(ControlError) as failure:
            action()
        assert failure.value.code == 'PLAN_PERMISSION_REQUIRED'
    frozen = store.approve_plan(approver['user'], row['id'], row['revision'])
    assert frozen['approved_by'] == approver['user']['id']
    store.update_user(admin, approver['user']['id'], plan_permissions=[])
    with pytest.raises(ControlError):
        store.authenticate(approver['token'])
    with pytest.raises(ControlError):
        store.approve_plan(approver['user'], row['id'], frozen['revision'])


def test_deleting_approved_targets_requires_approval_permission_and_returns_actor_metadata(state):
    store, admin, _, _ = state
    creator = user_with_permissions(store, admin, 'creator', ['create'])['user']
    approver = user_with_permissions(store, admin, 'approver', ['approve'])['user']
    live = approved(store, admin)
    denied = store.get_plan(creator, live['id'])
    assert denied['can_delete'] is False and denied['delete_block_reason']
    allowed = store.list_plans(approver)['items'][0]
    assert allowed['can_delete'] is True and allowed['delete_block_reason'] is None
    with pytest.raises(ControlError) as failure:
        store.delete_plan(creator, live['id'], live['revision'])
    assert failure.value.code == 'PLAN_PERMISSION_REQUIRED'
    assert store.get_plan(admin, live['id'])['is_current'] is True
    deleted = store.delete_plan(approver, live['id'], live['revision'])
    assert deleted['is_deleted'] is True and deleted['can_delete'] is False
    for status in ['draft', 'cancelled']:
        row = store.create_plan(admin, **plan())
        if status == 'cancelled':
            row = store.cancel_plan(admin, row['id'], row['revision'], 'Synthetic duplicate')
        assert store.get_plan(creator, row['id'])['can_delete'] is True
        assert store.get_plan(approver, row['id'])['can_delete'] is False
        with pytest.raises(ControlError) as failure:
            store.delete_plan(approver, row['id'], row['revision'])
        assert failure.value.code == 'PLAN_PERMISSION_REQUIRED'
        assert store.delete_plan(creator, row['id'], row['revision'])['is_deleted'] is True


def test_http_create_only_cannot_delete_an_approved_target(state):
    store, admin, _, _ = state
    creator = user_with_permissions(store, admin, 'creator', ['create'])
    approver = user_with_permissions(store, admin, 'approver', ['approve'])
    row = approved(store, admin)
    app.state.control_store = store
    try:
        with TestClient(app) as client:
            response = client.request('DELETE', f"/api/plans/{row['id']}", json={'revision': row['revision']},
                                      headers={'Authorization': 'Bearer ' + creator['token']})
            assert response.status_code == 403
            assert response.json()['detail']['code'] == 'PLAN_PERMISSION_REQUIRED'
            assert store.get_plan(admin, row['id'])['is_current'] is True
            response = client.request('DELETE', f"/api/plans/{row['id']}", json={'revision': row['revision']},
                                      headers={'Authorization': 'Bearer ' + approver['token']})
            assert response.status_code == 200 and response.json()['is_deleted'] is True
    finally:
        del app.state.control_store


def test_separate_approval_blocks_any_prior_editor_and_policy_keeps_old_defaults(state):
    store, admin, _, _ = state
    creator = account(store, admin, role='manager', username='creator')['user']
    editor = account(store, admin, role='manager', username='editor')['user']
    row = store.create_plan(creator, **plan())
    row = store.update_plan(editor, row['id'], row['revision'], note='reviewed')
    row = store.update_plan(admin, row['id'], row['revision'], note='changed again')
    assert store.get_plan(creator, row['id'])['can_approve'] is True
    strict = ControlStore(store.path, plan_approval_policy='separate_approver')
    for user in [creator, editor, admin]:
        assert strict.get_plan(user, row['id'])['can_approve'] is False
        with pytest.raises(ControlError) as failure:
            strict.approve_plan(user, row['id'], row['revision'])
        assert failure.value.code == 'PLAN_SELF_APPROVAL_FORBIDDEN'
    independent = account(store, admin, role='manager', username='independent')['user']
    assert strict.approve_plan(independent, row['id'], row['revision'])['status'] == 'approved'


def test_unconfigured_application_defaults_to_separate_approver_without_admin_bypass(state, monkeypatch):
    store, admin, _, _ = state
    row = store.create_plan(admin, **plan())
    monkeypatch.delenv('DASHBOARD_PLAN_APPROVAL_POLICY', raising=False)
    strict = ControlStore(store.path)
    assert strict.plan_approval_policy == 'separate_approver'
    assert strict.get_plan(admin, row['id'])['can_approve'] is False
    with pytest.raises(ControlError) as failure:
        strict.approve_plan(admin, row['id'], row['revision'])
    assert failure.value.code == 'PLAN_SELF_APPROVAL_FORBIDDEN'
    approver = user_with_permissions(store, admin, 'independent', ['approve'])
    assert strict.approve_plan(approver['user'], row['id'], row['revision'])['status'] == 'approved'


@pytest.mark.parametrize('comparison,expected', [(None, 'previous_period'), ('previous_period', 'previous_period'),
                                               ('previous_year', 'previous_year')])
def test_closed_report_headers_retain_saved_comparison_without_rewriting_snapshot(state, comparison, expected):
    store, admin, _, _ = state
    report = {'meta': {'filters': {'terminal': 'cua_lo', 'start_date': '2026-09-01', 'end_date': '2026-09-03',
                                   'production_scope': 'nghe_tinh'}, 'berth_rule_version': 'initial-berth-v1'}}
    if comparison is not None:
        report['meta']['filters']['comparison'] = comparison
    created = store.close_report(admin, 'cua_lo', '2026-09-01', '2026-09-03', report, [])
    assert created['comparison'] == expected
    assert store.list_closed_reports(admin)['items'][0]['comparison'] == expected
    saved = store.get_closed_report(admin, created['id'])
    assert saved['comparison'] == expected
    assert saved['report'] == report


def test_legacy_permissions_migrate_without_changing_existing_accounts(state):
    store, admin, _, _ = state
    manager = account(store, admin, role='manager', username='manager')['user']
    viewer = account(store, admin, username='viewer')['user']
    with sqlite3.connect(store.path) as db:
        original = db.execute('SELECT id,password_hash,is_active,must_change_password FROM users ORDER BY id').fetchall()
        db.execute('ALTER TABLE users DROP COLUMN plan_permissions')
    migrated = ControlStore(store.path)
    users = {row['id']: row for row in migrated.list_users(admin)['items']}
    assert set(users[manager['id']]['plan_permissions']) == {'create', 'approve'}
    assert users[viewer['id']]['plan_permissions'] == []
    with sqlite3.connect(store.path) as db:
        assert db.execute('SELECT id,password_hash,is_active,must_change_password FROM users ORDER BY id').fetchall() == original
    for invalid in [['create', 'create'], ['delete'], 'create', None]:
        with pytest.raises(ControlError):
            migrated.update_user(admin, manager['id'], plan_permissions=invalid)
    with pytest.raises(ControlError):
        migrated.update_user(admin, viewer['id'], plan_permissions=['approve'])


def test_admin_events_are_immutable_sanitized_scoped_and_transactional(state):
    store, admin, _, _ = state
    left = account(store, admin, role='admin', username='leftadmin')['user']
    right = account(store, admin, terminals=['ben_thuy'], username='right')['user']
    staff = account(store, admin, username='staff')['user']
    store.update_user(admin, staff['id'], display_name='Revised')
    store.reset_password(admin, staff['id'])
    events = store.list_admin_events(left, user_id=staff['id'])
    assert events['total'] == 3
    assert {row['action'] for row in events['items']} == {'user_created', 'user_updated', 'password_reset'}
    assert store.list_admin_events(left, user_id=right['id'])['total'] == 0
    for row in events['items']:
        assert set(row) == {'id', 'action', 'actor_id', 'user_id', 'before', 'after', 'created_at'}
        for snapshot in [row['before'], row['after']]:
            if snapshot:
                assert not {'password', 'password_hash', 'token', 'temporary_password'} & set(snapshot)
    count = store.list_admin_events(admin)['total']
    with pytest.raises(ControlError):
        store.update_user(left, right['id'], display_name='Forbidden')
    assert store.list_admin_events(admin)['total'] == count
    store.update_user(admin, staff['id'], terminals=['ben_thuy'])
    assert store.list_admin_events(left, user_id=staff['id'])['total'] == 3  # cross-scope event not disclosed
    with sqlite3.connect(store.path) as db:
        for statement in ['DELETE FROM admin_events', "UPDATE admin_events SET action='changed'"]:
            with pytest.raises(sqlite3.IntegrityError):
                db.execute(statement)
    with pytest.raises(ControlError):
        store.list_admin_events(right)


def test_http_permissions_filters_milestones_and_admin_events(state):
    store, admin, session, _ = state
    approver = user_with_permissions(store, admin, 'approver', ['approve'])
    app.state.control_store = store
    try:
        with TestClient(app) as client:
            headers = {'Authorization': 'Bearer ' + session['token']}
            body = plan(amount='100', milestones=[{'date': '2026-09-10', 'amount': '45.000001'}])
            made = client.post('/api/plans', json=body, headers=headers)
            assert made.status_code == 201, made.text
            row = made.json()
            denied = client.post('/api/plans', json=body, headers={'Authorization': 'Bearer ' + approver['token']})
            assert denied.status_code == 403
            accepted = client.post(f"/api/plans/{row['id']}/approve", json={'expected_revision': row['revision']},
                                   headers={'Authorization': 'Bearer ' + approver['token']})
            assert accepted.status_code == 200, accepted.text
            assert accepted.json()['milestones'] == body['milestones']
            listing = client.get('/api/plans', params={'version_scope': 'current', 'q': 'internal'}, headers=headers)
            assert listing.status_code == 200 and listing.json()['total'] == 1
            assert client.get('/api/admin/events', headers=headers).status_code == 200
            assert client.get('/api/admin/events', headers={'Authorization': 'Bearer ' + approver['token']}).status_code == 403
    finally:
        del app.state.control_store

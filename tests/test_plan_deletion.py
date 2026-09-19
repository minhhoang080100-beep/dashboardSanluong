"""Soft deletion uses temporary control databases, never the source TOS."""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
import json
import sqlite3
from threading import Barrier, Event

import pytest

from backend import control_store
from backend.control_store import ControlError, ControlStore
from test_control_store import state, account, plan
from test_plan_http import plan_api
from test_target_periods import report, target


def approved(store, actor, **changes):
    created = store.create_plan(actor, **plan(**changes))
    return store.approve_plan(actor, created['id'], created['revision'])


@pytest.mark.parametrize('status', ['draft', 'approved', 'cancelled'])
def test_delete_preserves_content_events_and_readable_history_for_every_status(state, status):
    store, actor, _, now = state
    before = store.create_plan(actor, **plan())
    if status == 'approved':
        before = store.approve_plan(actor, before['id'], before['revision'])
    if status == 'cancelled':
        before = store.cancel_plan(actor, before['id'], before['revision'], 'Duplicate entry')
    history = store.get_plan(actor, before['id'])['history']
    now[0] += 7
    deleted = store.delete_plan(actor, before['id'], before['revision'])
    assert deleted['status'] == status and deleted['is_deleted'] and not deleted['is_current']
    assert deleted['revision'] == before['revision'] + 1
    assert deleted['deleted_by'] == actor['id'] == deleted['updated_by']
    assert deleted['deleted_at'] == deleted['updated_at']
    actor_metadata = {'can_approve', 'approval_block_reason', 'can_delete', 'delete_block_reason'}
    metadata = {'revision', 'updated_at', 'updated_by', 'deleted_at', 'deleted_by', 'is_deleted', 'is_current'} | actor_metadata
    assert {key: value for key, value in before.items() if key not in metadata} == {
        key: value for key, value in deleted.items() if key not in metadata}
    assert store.list_plans(actor)['total'] == 0
    assert store.list_plans(actor, status=status, include_deleted=True)['items'] == [deleted]
    detail = store.get_plan(actor, deleted['id'])
    assert detail['history'][:-1] == history
    assert detail['history'][-1]['action'] == 'deleted'
    assert detail['history'][-1]['snapshot'] == {key: value for key, value in deleted.items() if key not in actor_metadata}
    with sqlite3.connect(store.path) as db:
        for sql in ["UPDATE plans SET amount='0' WHERE id=?", 'UPDATE plans SET deleted_at=NULL WHERE id=?',
                    'UPDATE plans SET revision=revision+1 WHERE id=?', 'DELETE FROM plans WHERE id=?']:
            with pytest.raises(sqlite3.IntegrityError):
                db.execute(sql, (deleted['id'],))


@pytest.mark.parametrize('status', ['approved', 'cancelled'])
def test_tombstone_exception_cannot_rewrite_immutable_content(state, status):
    store, actor, _, _ = state
    item = store.create_plan(actor, **plan())
    item = (store.approve_plan(actor, item['id'], item['revision']) if status == 'approved' else
            store.cancel_plan(actor, item['id'], item['revision'], 'Cancelled'))
    modifications = ["amount='0'", "terminal='ben_thuy'", "period_type='year'", "period_key='2027'",
                     "metric='teu'", "reference='changed'", "note='changed'", 'version=version+1',
                     "status='draft'", 'created_at=0', 'approved_at=0', 'approved_by=NULL',
                     'cancelled_at=0', 'cancelled_by=NULL', 'id=id+100', 'created_by=0']
    with sqlite3.connect(store.path) as db:
        for modification in modifications:
            # Assign a genuinely different value even if this metadata is NULL.
            if modification in {'approved_by=NULL', 'cancelled_by=NULL'}:
                key = modification.split('=')[0]
                modification = f'{key}=' + ('0' if item[key] is None else 'NULL')
            with pytest.raises(sqlite3.IntegrityError):
                db.execute(f'''UPDATE plans SET deleted_at=123,deleted_by=?,revision=revision+1,
                    updated_at=123,updated_by=?,{modification} WHERE id=?''', (actor['id'], actor['id'], item['id']))
        for tail in ['revision=revision', 'updated_at=124', 'updated_by=NULL', 'deleted_by=NULL']:
            with pytest.raises(sqlite3.IntegrityError):
                db.execute(f'''UPDATE plans SET deleted_at=123,deleted_by=?,revision=revision+1,
                    updated_at=123,updated_by=?,{tail} WHERE id=?''', (actor['id'], actor['id'], item['id']))
    assert not store.get_plan(actor, item['id'])['is_deleted']
    assert store.delete_plan(actor, item['id'], item['revision'])['is_deleted']


@pytest.mark.parametrize('period_type,identity', [('month', {'month': '2026-09'}),
                                                ('voyage', {'month': None, 'voyage_id': 101})])
def test_deleting_current_never_revives_old_versions_and_versions_keep_increasing(state, period_type, identity):
    store, actor, _, _ = state
    fields = {'period_type': period_type, **identity}
    first = approved(store, actor, **fields, amount=100)
    pending = store.create_plan(actor, **plan(**fields, amount=150))
    current = approved(store, actor, **fields, amount=200)
    lookup = {'month': '2026-09'} if period_type == 'month' else {'voyage_id': 101}
    store.delete_plan(actor, first['id'], first['revision'])
    assert store.effective_plans(actor, 'cua_lo', **lookup)[0]['id'] == current['id']
    store.delete_plan(actor, current['id'], current['revision'])
    assert store.effective_plans(actor, 'cua_lo', **lookup) == []
    with pytest.raises(ControlError) as caught:
        store.approve_plan(actor, pending['id'], pending['revision'])
    assert caught.value.code == 'PLAN_SUPERSEDED'
    replacement = approved(store, actor, **fields, amount=300)
    assert replacement['version'] == 4
    assert store.effective_plans(actor, 'cua_lo', **lookup)[0]['id'] == replacement['id']
    assert [row['id'] for row in store.list_plans(actor, include_deleted=True)['items'] if row['is_current']] == [replacement['id']]


def test_deleted_draft_does_not_disable_approved_plan_and_cannot_be_changed(state):
    store, actor, _, _ = state
    current = approved(store, actor)
    draft = store.create_plan(actor, **plan())
    deleted = store.delete_plan(actor, draft['id'], draft['revision'])
    assert store.effective_plans(actor, 'cua_lo', month='2026-09')[0]['id'] == current['id']
    for change in [lambda: store.update_plan(actor, draft['id'], deleted['revision'], amount=0),
                   lambda: store.approve_plan(actor, draft['id'], deleted['revision']),
                   lambda: store.cancel_plan(actor, draft['id'], deleted['revision'], 'Again')]:
        with pytest.raises(ControlError) as caught:
            change()
        assert caught.value.code == 'PLAN_DELETED'


@pytest.mark.parametrize('kind,period', [('month', {'month':'2026-01'}),
    ('week', {'week':'2026-W03'}),
    ('quarter', {'quarter':'2026-Q1'}), ('year', {'year':2026}),
    ('custom', {'start_date':'2026-01-01', 'end_date':'2026-01-31'})])
def test_every_period_progress_and_navigation_excludes_deleted_current_without_old_fallback(state, kind, period):
    store, actor, _, _ = state
    old = target(store, actor, period_type=kind, **period)
    current = target(store, actor, period_type=kind, amount=200, **period)
    snapshot = report(start='2026-01-12', end='2026-01-13') if kind == 'week' else report()
    assert store.throughput_progress(actor, snapshot)['items'][0]['target'] == 200
    store.delete_plan(actor, current['id'], current['revision'])
    progress = store.throughput_progress(actor, snapshot)
    assert progress['items'] == progress['available_periods'] == []
    assert not store.get_plan(actor, old['id'])['is_current']
    replacement = target(store, actor, period_type=kind, amount=300, **period)
    assert replacement['version'] == 3
    assert store.throughput_progress(actor, snapshot)['items'][0]['plans'][0]['id'] == replacement['id']


def test_deleted_company_target_does_not_fall_back_to_terminal_sum(state):
    store, actor, _, _ = state
    left = target(store, actor, terminal='cua_lo', amount=100)
    right = target(store, actor, terminal='ben_thuy', amount=200)
    company = target(store, actor, amount=400)
    store.delete_plan(actor, company['id'], company['revision'])
    assert store.throughput_progress(actor, report())['items'] == []
    assert store.throughput_progress(actor, report())['available_periods'] == []
    for terminal, expected in [('cua_lo', left), ('ben_thuy', right)]:
        assert store.throughput_progress(actor, report(terminal=terminal))['items'][0]['plans'][0]['id'] == expected['id']
    replacement = target(store, actor, amount=500)
    assert store.throughput_progress(actor, report())['items'][0]['plans'][0]['id'] == replacement['id']


def test_deleted_company_targets_offer_navigation_to_live_terminal_plan_without_company_actuals(state):
    store, actor, _, _ = state
    for kind, period in [('month', {'month': '2026-09'}), ('quarter', {'quarter': '2026-Q3'}), ('year', {'year': 2026})]:
        target(store, actor, period_type=kind, **period, amount=1000)
        current = target(store, actor, period_type=kind, **period, amount=2000)
        store.delete_plan(actor, current['id'], current['revision'])
    terminal = target(store, actor, terminal='cua_lo', period_type='month', month='2026-09', amount=700)
    company_report = report(start='2026-09-01', end='2026-09-18', value=999)
    company = store.throughput_progress(actor, company_report)
    assert company['items'] == company['available_periods'] == []
    assert company['other_scope_periods'] == [{
        'key': 'month:2026-09', 'period_type': 'month', 'period_key': '2026-09',
        'start_date': '2026-09-01', 'end_date': '2026-09-30', 'terminal': 'cua_lo', 'target': 700}]
    terminal_report = store.throughput_progress(actor, report(terminal='cua_lo', start='2026-09-01', end='2026-09-18', value=350))
    item = terminal_report['items'][0]
    assert item['plans'][0]['id'] == terminal['id']
    assert item['actual'] == 350 and item['target'] == 700 and item['completion_percent'] == 50
    assert terminal_report['other_scope_periods'] == []


def test_other_scope_navigation_does_not_revive_an_older_approval_after_latest_is_deleted(state):
    store, actor, _, _ = state
    target(store, actor, terminal='ben_thuy', amount=100)
    latest = target(store, actor, terminal='ben_thuy', amount=200)
    store.delete_plan(actor, latest['id'], latest['revision'])
    assert store.throughput_progress(actor, report(terminal='cua_lo'))['other_scope_periods'] == []
    replacement = target(store, actor, terminal='ben_thuy', amount=300)
    assert replacement['version'] == 3
    alternatives = store.throughput_progress(actor, report(terminal='cua_lo'))['other_scope_periods']
    assert len(alternatives) == 1 and alternatives[0]['target'] == 300


def test_deleted_terminal_target_is_missing_not_older_value_in_company_progress(state):
    store, actor, _, _ = state
    target(store, actor, terminal='cua_lo', amount=50)
    current = target(store, actor, terminal='cua_lo', amount=100)
    right = target(store, actor, terminal='ben_thuy', amount=200)
    store.delete_plan(actor, current['id'], current['revision'])
    progress = store.throughput_progress(actor, report())
    item = progress['items'][0]
    assert item['status'] == 'missing_plan' and item['target'] is None
    assert item['completion_percent'] is None and item['plans'][0]['id'] == right['id']


def test_delete_and_progress_do_not_modify_previously_closed_reports(state):
    store, actor, _, _ = state
    current = approved(store, actor, amount=100)
    snapshot = report(terminal='cua_lo', start='2026-09-01', end='2026-09-17')
    actuals = {'cua_lo': {'tonnage':50, 'tonnage_status':'ready', 'teu':0, 'teu_status':'empty'}}
    closed = store.close_report(actor, 'cua_lo', '2026-09-01', '2026-09-17', snapshot, [], planning_actuals=actuals)
    before = deepcopy(store.get_closed_report(actor, closed['id']))
    store.delete_plan(actor, current['id'], current['revision'])
    assert store.get_closed_report(actor, closed['id']) == before
    assert before['planning']['plans'][0]['id'] == current['id']
    assert before['report']['throughput_progress']['items'][0]['target'] == 100
    newer = store.close_report(actor, 'cua_lo', '2026-09-01', '2026-09-17', snapshot, [], planning_actuals=actuals)
    after = store.get_closed_report(actor, newer['id'])
    assert after['planning']['plans'] == []
    assert after['planning']['rows'][0]['status'] == 'missing_plan'
    assert after['report']['throughput_progress']['items'] == []


def test_delete_scope_and_editor_permissions_are_rechecked_in_transaction(state):
    store, actor, _, _ = state
    viewer = account(store, actor, username='viewer')['user']
    manager = account(store, actor, username='manager', role='manager')['user']
    own = store.create_plan(actor, **plan())
    foreign = store.create_plan(actor, **plan(terminal='ben_thuy'))
    company = store.create_plan(actor, **plan(terminal='all'))
    for who, item in [(viewer, own), (manager, foreign), (manager, company)]:
        with pytest.raises(ControlError) as caught:
            store.delete_plan(who, item['id'], item['revision'])
        assert caught.value.status_code == 403
    assert store.delete_plan(manager, own['id'], own['revision'])['deleted_by'] == manager['id']
    fresh = store.create_plan(actor, **plan())
    store.update_user(actor, manager['id'], role='viewer')
    with pytest.raises(ControlError) as caught:
        store.delete_plan(manager, fresh['id'], fresh['revision'])
    assert caught.value.code == 'EDITOR_REQUIRED'


def test_delete_conflict_retry_validation_and_failed_audit_leave_one_event(state, monkeypatch):
    store, actor, _, _ = state
    draft = store.create_plan(actor, **plan())
    edited = store.update_plan(actor, draft['id'], draft['revision'], amount=150)
    with pytest.raises(ControlError) as caught:
        store.delete_plan(actor, draft['id'], draft['revision'])
    assert caught.value.code == 'PLAN_CONFLICT'
    for revision in [None, True, 0, -1, 1.5, '2']:
        with pytest.raises(ControlError) as caught:
            store.delete_plan(actor, draft['id'], revision)
        assert caught.value.status_code == 422
    original = store._plan_event
    def fail_event(*args, **kwargs):
        raise RuntimeError('Synthetic audit failure')
    monkeypatch.setattr(store, '_plan_event', fail_event)
    with pytest.raises(RuntimeError):
        store.delete_plan(actor, draft['id'], edited['revision'])
    assert store.get_plan(actor, draft['id'])['revision'] == edited['revision']
    assert not store.get_plan(actor, draft['id'])['is_deleted']
    monkeypatch.setattr(store, '_plan_event', original)
    deleted = store.delete_plan(actor, draft['id'], edited['revision'])
    for revision, code in [(edited['revision'], 'PLAN_CONFLICT'), (deleted['revision'], 'PLAN_DELETED')]:
        with pytest.raises(ControlError) as caught:
            store.delete_plan(actor, draft['id'], revision)
        assert caught.value.status_code == 409 and caught.value.code == code
    assert [event['action'] for event in store.get_plan(actor, draft['id'])['history']].count('deleted') == 1


def test_concurrent_delete_commits_once_and_reports_revision_conflict(state):
    store, actor, _, _ = state
    item = approved(store, actor)
    start = Barrier(2)
    def deleting():
        start.wait(timeout=5)
        try:
            store.delete_plan(actor, item['id'], item['revision'])
            return 'deleted'
        except ControlError as error:
            return error.code
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = [future.result(timeout=10) for future in [pool.submit(deleting), pool.submit(deleting)]]
    assert sorted(results) == ['PLAN_CONFLICT', 'deleted']
    assert [event['action'] for event in store.get_plan(actor, item['id'])['history']].count('deleted') == 1


def test_close_and_delete_capture_plan_atomically(state, monkeypatch):
    store, actor, _, _ = state
    item = approved(store, actor, amount=100)
    entered, release, deleting = Event(), Event(), Event()
    original = control_store.plan_progress_rows
    def hold_capture(*args):
        entered.set()
        assert release.wait(5)
        return original(*args)
    monkeypatch.setattr(control_store, 'plan_progress_rows', hold_capture)
    def remove():
        deleting.set()
        return store.delete_plan(actor, item['id'], item['revision'])
    snapshot = report(terminal='cua_lo', start='2026-09-01', end='2026-09-17')
    actuals = {'cua_lo': {'tonnage':50, 'tonnage_status':'ready', 'teu':0, 'teu_status':'empty'}}
    with ThreadPoolExecutor(max_workers=2) as pool:
        closing = pool.submit(store.close_report, actor, 'cua_lo', '2026-09-01', '2026-09-17', snapshot, [], planning_actuals=actuals)
        assert entered.wait(5)
        deleting_future = pool.submit(remove)
        assert deleting.wait(5)
        try:
            assert not deleting_future.done()
        finally:
            release.set()
        closed = closing.result(timeout=10)
        assert deleting_future.result(timeout=10)['is_deleted']
    saved = store.get_closed_report(actor, closed['id'])
    assert saved['planning']['plans'][0]['id'] == item['id']
    assert saved['report']['throughput_progress']['items'][0]['target'] == 100
    assert store.effective_plans(actor, 'cua_lo', month='2026-09') == []


def test_backward_migration_is_idempotent_and_preserves_original_rows_events_and_sessions(state):
    store, actor, session, now = state
    item = approved(store, actor)
    with sqlite3.connect(store.path) as db:
        for trigger in ['approved_plans_no_update', 'cancelled_plans_no_update',
                        'deleted_plans_no_update', 'deleted_plans_no_delete']:
            db.execute(f'DROP TRIGGER {trigger}')
        db.execute('ALTER TABLE plans DROP COLUMN deleted_at')
        db.execute('ALTER TABLE plans DROP COLUMN deleted_by')
        for status in ['approved', 'cancelled']:
            db.execute(f"CREATE TRIGGER {status}_plans_no_update BEFORE UPDATE ON plans WHEN OLD.status='{status}' BEGIN SELECT RAISE(ABORT, 'immutable'); END")
        columns = [row[1] for row in db.execute('PRAGMA table_info(plans)')]
        prior = db.execute('SELECT * FROM plans').fetchall()
        events = db.execute('SELECT * FROM plan_events').fetchall()
        users = db.execute('SELECT * FROM users').fetchall()
        sessions = db.execute('SELECT * FROM sessions').fetchall()
    for _ in range(2):
        migrated = ControlStore(store.path, clock=lambda: now[0])
        with sqlite3.connect(store.path) as db:
            assert db.execute('SELECT '+','.join(columns)+' FROM plans').fetchall() == prior
            assert db.execute('SELECT * FROM plan_events').fetchall() == events
            assert db.execute('SELECT * FROM users').fetchall() == users
            assert db.execute('SELECT * FROM sessions').fetchall() == sessions
        assert migrated.authenticate(session['token'])['id'] == actor['id']
        assert migrated.get_plan(actor, item['id'])['is_deleted'] is False
    assert migrated.delete_plan(actor, item['id'], item['revision'])['is_deleted']


def test_http_delete_revision_contract_history_and_no_tos_dependency(plan_api):
    client, store, actor, headers = plan_api
    # The API fixture rejects any source-voyage validation call.
    draft = store.create_plan(actor, **plan(period_type='voyage', month=None, voyage_id=101))
    path = f"/api/plans/{draft['id']}"
    for body in [{}, {'expected_revision':1}, {'revision':True}, {'revision':'1'}, {'revision':0}, {'revision':1,'note':'x'}]:
        assert client.request('DELETE', path, headers=headers, json=body).status_code == 422
    response = client.request('DELETE', path, headers=headers, json={'revision':1})
    assert response.status_code == 200, response.text
    deleted = response.json()
    assert deleted['revision'] == 2 and deleted['is_deleted']
    assert client.get('/api/plans', headers=headers).json()['total'] == 0
    assert client.get('/api/plans?include_deleted=true', headers=headers).json()['items'][0]['id'] == draft['id']
    assert client.get(path, headers=headers).json()['history'][-1]['action'] == 'deleted'
    assert client.request('DELETE', path, headers=headers, json={'revision':1}).json()['detail']['code'] == 'PLAN_CONFLICT'
    assert client.request('DELETE', path, headers=headers, json={'revision':2}).json()['detail']['code'] == 'PLAN_DELETED'
    assert client.request('DELETE', '/api/plans/99999', headers=headers, json={'revision':1}).status_code == 404
    assert client.request('DELETE', path, json={'revision':2}).status_code == 401
    assert client.get('/api/plans?include_deleted=invalid', headers=headers).status_code == 422

"""Internal-state regressions use temporary SQLite files; never contact TOS."""
import json
import sqlite3

import pytest

from backend.control_store import ControlError, ControlStore, hash_password, require_scope, verify_password

PASSWORD = "Example-Only-Password-2026"


@pytest.fixture
def state(tmp_path):
    now = [1800000000.0]
    store = ControlStore(tmp_path / "control.sqlite3", clock=lambda: now[0], session_ttl_seconds=3600)
    created = store.bootstrap_admin()
    initial = store.login("admin", created["temporary_password"])
    session = store.change_password(initial["token"], created["temporary_password"], PASSWORD)
    return store, session["user"], session, now


def account(store, admin, role="viewer", terminals=None, username="reader"):
    created = store.create_user(admin, username=username, display_name=username, role=role, terminals=terminals or ["cua_lo"])
    first = store.login(username, created["temporary_password"])
    return store.change_password(first["token"], created["temporary_password"], PASSWORD)


def plan(terminal="cua_lo", **overrides):
    return {"terminal": terminal, "period_type": "month", "month": "2026-09", "metric": "tonnage",
            "amount": "123.456789", "reference": "Approved internal planning document", "note": "", **overrides}


def test_draft_edit_cancel_conflicts_and_append_only_history(state):
    store, admin, _, now = state
    created = store.create_plan(admin, **plan())
    now[0] += 1
    changed = store.update_plan(admin, created['id'], created['revision'], amount='125.123456', note='Corrected amount')
    assert changed['version'] == created['version'] and changed['revision'] == 2
    assert changed['amount_decimal'] == '125.123456' and changed['updated_by'] == admin['id']
    for action in [lambda: store.update_plan(admin, created['id'], 1, amount=200),
                   lambda: store.cancel_plan(admin, created['id'], 1, 'Duplicate'),
                   lambda: store.approve_plan(admin, created['id'], 1)]:
        with pytest.raises(ControlError) as failure:
            action()
        assert failure.value.code == 'PLAN_CONFLICT'
    cancelled = store.cancel_plan(admin, created['id'], 2, 'Duplicate document')
    assert cancelled['status'] == 'cancelled' and cancelled['revision'] == 3
    saved = store.get_plan(admin, created['id'])
    assert [event['action'] for event in saved['history']] == ['created', 'updated', 'cancelled']
    assert saved['history'][0]['snapshot']['amount_decimal'] == '123.456789'
    assert saved['history'][-1]['note'] == 'Duplicate document'
    assert store.list_plans(admin, status='cancelled')['total'] == 1
    for action in [lambda: store.update_plan(admin, created['id'], 3, amount=200),
                   lambda: store.cancel_plan(admin, created['id'], 3, 'Again'),
                   lambda: store.approve_plan(admin, created['id'], 3)]:
        with pytest.raises(ControlError) as failure:
            action()
        assert failure.value.code == 'PLAN_IMMUTABLE'
    with sqlite3.connect(store.path) as db:
        for statement in ["UPDATE plans SET amount='0'", 'DELETE FROM plans', 'DELETE FROM plan_events', "UPDATE plan_events SET note='rewrite'"]:
            with pytest.raises(sqlite3.IntegrityError):
                db.execute(statement)


def test_edit_cannot_change_foreign_scope_and_rekeys_version_safely(state):
    store, admin, _, _ = state
    manager = account(store, admin, role='manager')['user']
    first = store.create_plan(admin, **plan())
    other = store.create_plan(admin, **plan(metric='teu'))
    with pytest.raises(ControlError) as failure:
        store.update_plan(manager, first['id'], 1, terminal='ben_thuy')
    assert failure.value.status_code == 403
    moved = store.update_plan(manager, first['id'], 1, metric='teu')
    assert moved['version'] == other['version'] + 1
    store.approve_plan(admin, first['id'], moved['revision'])
    for action in [lambda: store.update_plan(admin, first['id'], 3, amount=0),
                   lambda: store.cancel_plan(admin, first['id'], 3, 'Wrong')]:
        with pytest.raises(ControlError) as failure:
            action()
        assert failure.value.code == 'PLAN_IMMUTABLE'


def test_additive_migration_keeps_approved_values_and_legacy_baseline(state):
    store, admin, _, _ = state
    approved = store.create_plan(admin, **plan())
    store.approve_plan(admin, approved['id'])
    draft = store.create_plan(admin, **plan(metric='teu'))
    with sqlite3.connect(store.path) as db:
        db.execute('DROP TRIGGER plan_events_no_delete')
        db.execute('DELETE FROM plan_events WHERE plan_id=?', (draft['id'],))
        for name in ['revision', 'updated_by', 'updated_at', 'cancelled_by', 'cancelled_at']:
            db.execute(f'ALTER TABLE plans DROP COLUMN {name}')
    migrated = ControlStore(store.path)
    before = migrated.get_plan(admin, approved['id'])
    assert before['status'] == 'approved' and before['amount_decimal'] == '123.456789'
    migrated.update_plan(admin, draft['id'], 1, note='After migration')
    assert [event['action'] for event in migrated.get_plan(admin, draft['id'])['history']] == ['legacy_baseline', 'updated']


def test_closed_plan_capture_and_approval_share_one_transaction(state, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event
    from backend import control_store
    store, admin, _, _ = state
    first = store.create_plan(admin, **plan(amount=100))
    store.approve_plan(admin, first['id'])
    revised = store.create_plan(admin, **plan(amount=200))
    captured, release, approving = Event(), Event(), Event()
    original = control_store.plan_progress_rows
    def hold_capture(plans, actuals):
        captured.set()
        assert release.wait(5)
        return original(plans, actuals)
    monkeypatch.setattr(control_store, 'plan_progress_rows', hold_capture)
    actuals = {'cua_lo': {'tonnage': 50, 'tonnage_status': 'ready', 'teu': 0, 'teu_status': 'empty'}}
    def approve():
        approving.set()
        return store.approve_plan(admin, revised['id'])
    with ThreadPoolExecutor(max_workers=2) as pool:
        closing = pool.submit(store.close_report, admin, 'cua_lo', '2026-09-01', '2026-09-13', {}, [], planning_actuals=actuals)
        assert captured.wait(5)
        updating = pool.submit(approve)
        assert approving.wait(5)
        try:
            assert not updating.done()
        finally:
            release.set()
        closed = closing.result(timeout=5)
        assert updating.result(timeout=5)['status'] == 'approved'
    saved = store.get_closed_report(admin, closed['id'])['planning']
    row = next(item for item in saved['rows'] if item['metric'] == 'tonnage')
    assert row['plan_id'] == first['id'] and row['target'] == 100 and row['completion_percent'] == 50
    assert store.effective_plans(admin, 'cua_lo', month='2026-09')[0]['id'] == revised['id']


def test_hashes_are_salted_and_tokens_are_not_persisted(state):
    store, admin, session, _ = state
    first, second = hash_password(PASSWORD), hash_password(PASSWORD)
    assert first != second
    assert verify_password(PASSWORD, first)
    assert not verify_password("wrong", first)
    with sqlite3.connect(store.path) as db:
        users = str(db.execute("SELECT * FROM users").fetchall())
        sessions = str(db.execute("SELECT * FROM sessions").fetchall())
    assert PASSWORD not in users and session["token"] not in sessions
    assert "password_hash" not in json.dumps(store.list_users(admin))


def test_bootstrap_is_explicit_and_cannot_replace_accounts(tmp_path):
    store = ControlStore(tmp_path / "state.sqlite3")
    with sqlite3.connect(store.path) as db:
        assert db.execute("SELECT COUNT(*) FROM users").fetchone()[0] == 0
    bootstrap = store.bootstrap_admin()
    assert bootstrap["user"]["must_change_password"]
    with pytest.raises(ControlError) as failure:
        store.bootstrap_admin()
    assert failure.value.code == "ALREADY_INITIALIZED"


def test_initial_reset_and_change_revoke_all_old_sessions(state):
    store, admin, _, _ = state
    created = store.create_user(admin, username="reader", display_name="Reader", role="viewer", terminals=["cua_lo"])
    initial = store.login("reader", created["temporary_password"])
    with pytest.raises(ControlError) as failure:
        store.list_plans(initial["user"])
    assert failure.value.code == "PASSWORD_CHANGE_REQUIRED"
    ready = store.change_password(initial["token"], created["temporary_password"], PASSWORD)
    with pytest.raises(ControlError):
        store.authenticate(initial["token"])
    other = store.login("reader", PASSWORD)
    reset = store.reset_password(admin, ready["user"]["id"])
    for session in (ready, other):
        with pytest.raises(ControlError):
            store.authenticate(session["token"])
    assert store.login("reader", reset["temporary_password"])["user"]["must_change_password"]


def test_password_change_rechecks_revocation_inside_write_transaction(state, monkeypatch):
    store, _, session, _ = state
    original = store.authenticate

    def revoked_after_first_check(token):
        user = original(token)
        store.logout(token)
        return user

    monkeypatch.setattr(store, "authenticate", revoked_after_first_check)
    with pytest.raises(ControlError) as failure:
        store.change_password(session["token"], PASSWORD, "Another-Example-Password-2026")
    assert failure.value.code == "SESSION_INVALID"
    assert store.login("admin", PASSWORD)["user"]["id"] == session["user"]["id"]


def test_session_expiry_logout_and_inactivation(state):
    store, admin, _, now = state
    session = account(store, admin)
    store.logout(session["token"])
    with pytest.raises(ControlError):
        store.authenticate(session["token"])
    session = store.login("reader", PASSWORD)
    now[0] += 3600
    with pytest.raises(ControlError):
        store.authenticate(session["token"])
    session = store.login("reader", PASSWORD)
    store.update_user(admin, session["user"]["id"], is_active=False)
    with pytest.raises(ControlError):
        store.authenticate(session["token"])
    with pytest.raises(ControlError) as failure:
        store.login("reader", PASSWORD)
    assert failure.value.code == "INVALID_CREDENTIALS"


def test_login_rate_limit_is_persisted_expires_and_prunes(state):
    store, _, _, now = state
    for _ in range(5):
        with pytest.raises(ControlError) as failure:
            store.login("unknown-user", "wrong-password", "client-one")
        assert failure.value.status_code == 401
    # Separate store instance shares the throttle across requests/processes.
    other = ControlStore(store.path, clock=lambda: now[0])
    with pytest.raises(ControlError) as failure:
        other.login("unknown-user", "wrong-password", "client-two")
    assert failure.value.status_code == 429
    now[0] += 901
    with pytest.raises(ControlError) as failure:
        other.login("unknown-user", "wrong-password", "client-two")
    assert failure.value.status_code == 401
    with sqlite3.connect(store.path) as db:
        assert db.execute("SELECT COUNT(*) FROM login_attempts").fetchone()[0] == 1


def test_client_throttle_applies_across_usernames_without_hash_work(state, monkeypatch):
    store, _, _, now = state
    with store._db(write=True) as db:
        import hashlib
        client = hashlib.sha256(b"same-client").hexdigest()
        db.executemany("INSERT INTO login_attempts(username,client_key,created_at) VALUES(?,?,?)",
                       [(f"user-{index}", client, now[0]) for index in range(30)])
    monkeypatch.setattr("backend.control_store.verify_password", lambda *args: pytest.fail("Throttled request did password hashing"))
    with pytest.raises(ControlError) as failure:
        store.login("new-user", "wrong-password", "same-client")
    assert failure.value.status_code == 429


def test_role_scope_and_stale_user_dict_cannot_bypass_authorization(state):
    store, admin, _, _ = state
    manager = account(store, admin, "manager")["user"]
    with pytest.raises(ControlError):
        store.create_plan(manager, **plan("ben_thuy"))
    with pytest.raises(ControlError):
        require_scope(manager, "all")
    with pytest.raises(ControlError):
        store.create_user(manager, username="other", display_name="Other", role="admin", terminals=["cua_lo"])
    store.update_user(admin, manager["id"], role="viewer")
    # Old dict still says manager, but the store reads the current role.
    with pytest.raises(ControlError) as failure:
        store.create_plan(manager, **plan())
    assert failure.value.code == "EDITOR_REQUIRED"
    assert store.list_plans(manager)["items"] == []


def test_last_admin_cannot_be_deleted_or_demoted(state):
    store, admin, _, _ = state
    for update in ({"is_active": False}, {"role": "viewer"}):
        with pytest.raises(ControlError) as failure:
            store.update_user(admin, admin["id"], **update)
        assert failure.value.code == "LAST_ADMIN"


def test_last_admin_cannot_permanently_remove_own_terminal_access(state):
    store, admin, _, _ = state
    with pytest.raises(ControlError) as failure:
        store.update_user(admin, admin["id"], terminals=["cua_lo"])
    assert failure.value.code == "LAST_TERMINAL_ADMIN"
    assert set(store.list_users(admin)["items"][0]["terminals"]) == {"cua_lo", "ben_thuy"}


def test_each_terminal_keeps_an_administrator_during_deactivation(state):
    store, admin, _, _ = state
    other = account(store, admin, "admin", ["cua_lo"], "second-admin")["user"]
    for changes in ({"is_active": False}, {"role": "manager"}, {"terminals": ["cua_lo"]}):
        with pytest.raises(ControlError) as failure:
            store.update_user(admin, admin["id"], **changes)
        assert failure.value.code == "LAST_TERMINAL_ADMIN"
    store.update_user(admin, other["id"], terminals=["cua_lo", "ben_thuy"])
    assert store.update_user(admin, admin["id"], terminals=["cua_lo"])["terminals"] == ["cua_lo"]


def test_concurrent_admin_scope_reduction_cannot_orphan_a_terminal(state):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier
    store, admin, _, _ = state
    other = account(store, admin, "admin", ["cua_lo", "ben_thuy"], "second-admin")["user"]
    ready = Barrier(2)

    def reduce(user):
        ready.wait(timeout=5)
        try:
            return store.update_user(user, user["id"], terminals=["cua_lo"])
        except ControlError as exc:
            return exc.code

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(reduce, [admin, other]))
    assert sum(isinstance(result, dict) for result in results) == 1
    assert results.count("LAST_TERMINAL_ADMIN") == 1
    with sqlite3.connect(store.path) as db:
        scopes = [json.loads(row[0]) for row in db.execute("SELECT terminals FROM users WHERE is_active=1 AND role='admin'")]
    assert sum("ben_thuy" in scope for scope in scopes) == 1


def test_plan_revisions_approval_immutability_and_decimal_precision(state):
    store, admin, _, _ = state
    first = store.create_plan(admin, **plan())
    assert first["status"] == "draft" and first["version"] == 1
    approved = store.approve_plan(admin, first["id"])
    assert approved["approved_by"] == admin["id"] and approved["is_current"]
    assert approved["amount_decimal"] == "123.456789"
    second = store.create_plan(admin, **plan(amount="200"))
    assert second["version"] == 2
    assert store.effective_plans(admin, "cua_lo", month="2026-09")[0]["id"] == first["id"]
    store.approve_plan(admin, second["id"])
    assert [row["id"] for row in store.effective_plans(admin, "cua_lo", month="2026-09")] == [second["id"]]
    with pytest.raises(ControlError):
        store.approve_plan(admin, first["id"])
    with sqlite3.connect(store.path) as db:
        with pytest.raises(sqlite3.IntegrityError):
            db.execute("UPDATE plans SET amount='999' WHERE id=?", (first["id"],))
        with pytest.raises(sqlite3.IntegrityError):
            db.execute("DELETE FROM plans WHERE id=?", (first["id"],))


def test_plan_import_is_atomic_and_filters_month_vs_voyage(state):
    store, admin, _, _ = state
    manager = account(store, admin, "manager")["user"]
    with pytest.raises(ControlError):
        store.create_plans(manager, [plan(), plan("ben_thuy")])
    assert store.list_plans(admin)["total"] == 0
    with pytest.raises(ControlError):
        store.create_plans(admin, [plan(), plan(amount="NaN")])
    assert store.list_plans(admin)["total"] == 0
    store.create_plans(admin, [plan(), plan(period_type="voyage", month=None, voyage_id=101)])
    assert store.list_plans(admin, month="2026-09")["total"] == 1
    assert store.list_plans(admin, period_type="voyage")["total"] == 1
    assert store.list_plans(admin, voyage_id=101)["items"][0]["month"] is None


def test_effective_plan_does_not_disappear_when_revision_is_approved_after_read(state, monkeypatch):
    from contextlib import contextmanager
    store, admin, _, _ = state
    first = store.create_plan(admin, **plan(amount="100"))
    store.approve_plan(admin, first["id"])
    second = store.create_plan(admin, **plan(amount="200"))
    original_db = store._db
    pending = [True]

    class InterleavedCursor:
        def __init__(self, cursor):
            self.cursor = cursor

        def fetchall(self):
            rows = self.cursor.fetchall()
            if pending[0]:
                pending[0] = False
                store.approve_plan(admin, second["id"])
            return rows

    class ReadConnection:
        def __init__(self, db):
            self.db = db

        def execute(self, sql, parameters=()):
            cursor = self.db.execute(sql, parameters)
            if "FROM plans" in sql and "SELECT MAX(" not in sql:
                return InterleavedCursor(cursor)
            return cursor

    @contextmanager
    def interleaved_db(write=False):
        with original_db(write=write) as db:
            yield db if write else ReadConnection(db)

    monkeypatch.setattr(store, "_db", interleaved_db)
    observed = store.effective_plans(admin, "cua_lo", month="2026-09")
    assert len(observed) == 1 and observed[0]["id"] == first["id"] and observed[0]["is_current"]
    assert store.effective_plans(admin, "cua_lo", month="2026-09")[0]["id"] == second["id"]


@pytest.mark.parametrize("change", [{"amount": "-1"}, {"amount": "Infinity"}, {"amount": "0.0000001"}, {"month": "2026-13"}, {"voyage_id": 101}, {"metric": "money"}])
def test_invalid_plan_inputs_rejected_before_writes(change):
    with pytest.raises(ControlError):
        ControlStore.validate_plan(plan(**change))


def test_plan_approval_requires_document_reference(state):
    store, admin, _, _ = state
    draft = store.create_plan(admin, **plan(reference=""))
    with pytest.raises(ControlError) as failure:
        store.approve_plan(admin, draft["id"])
    assert failure.value.code == "PLAN_REFERENCE_REQUIRED"


def test_closed_reports_are_frozen_versioned_scoped_and_compare_source_changes(state):
    store, admin, _, _ = state
    report = {"overview": {"total_tonnage": 10, "total_teu": 2, "vessel_calls": 1, "record_count": 2}}
    facts = [{"id": 1, "tonnage": 6}, {"id": 2, "tonnage": 4}]
    first = store.close_report(admin, "all", "2026-09-01", "2026-09-09", report, facts, title="Internal close")
    second = store.close_report(admin, "all", "2026-09-01", "2026-09-09", report, facts)
    assert (first["version"], second["version"]) == (1, 2)
    same = store.compare_closed_report(admin, first["id"], report, list(reversed(facts)))
    assert not same["changed"]
    report["overview"]["total_tonnage"] = 11
    facts[0]["tonnage"] = 7
    comparison = store.compare_closed_report(admin, first["id"], report, facts)
    assert comparison["changed"] and comparison["source_changed"]
    assert comparison["changes"] == [{"metric": "total_tonnage", "closed": 10, "current": 11, "delta": 1.0}]
    assert store.get_closed_report(admin, first["id"])["report"]["overview"]["total_tonnage"] == 10
    with sqlite3.connect(store.path) as db:
        with pytest.raises(sqlite3.IntegrityError):
            db.execute("UPDATE closed_reports SET report_json='{}' WHERE id=?", (first["id"],))
        with pytest.raises(sqlite3.IntegrityError):
            db.execute("DELETE FROM closed_reports WHERE id=?", (first["id"],))
    viewer = account(store, admin)["user"]
    assert store.list_closed_reports(viewer)["items"] == []
    with pytest.raises(ControlError):
        store.get_closed_report(viewer, first["id"])
    with pytest.raises(ControlError):
        store.close_report(viewer, "cua_lo", "2026-09-01", "2026-09-09", report, facts)


@pytest.mark.parametrize("metric", ["trend_tonnage", "trend_teu", "trend_vessels"])
def test_closed_comparison_detects_previous_period_corrections(state, metric):
    store, admin, _, _ = state
    report = {"overview": {"total_tonnage": 100, "total_teu": 2, "vessel_calls": 1, "record_count": 1, metric: 10}}
    facts = [{"id": "101", "weight": 100}]
    closed = store.close_report(admin, "cua_lo", "2026-09-01", "2026-09-09", report, facts)
    current = {"overview": {**report["overview"], metric: 20}}
    compared = store.compare_closed_report(admin, closed["id"], current, facts)
    assert compared["changed"] and not compared["source_changed"]
    assert compared["changes"] == [{"metric": metric, "closed": 10, "current": 20, "delta": 10.0}]


def test_issue_resolution_keeps_history_and_terminal_identity(state):
    store, admin, _, _ = state
    values = {"terminal": "cua_lo", "namespace": "tallyshift", "source_id": "101", "issue": "missing_weight"}
    first = store.set_issue(admin, **values, status="open", note="Needs review")
    second = store.set_issue(admin, **values, status="resolved", note="Reviewed against source document")
    store.set_issue(admin, **{**values, "terminal": "ben_thuy"}, status="open", note="Different source")
    assert store.list_issues(admin, "cua_lo")["items"] == [second]
    assert store.list_issues(admin, "cua_lo", history=True)["total"] == 2
    assert first["id"] != second["id"]
    viewer = account(store, admin)["user"]
    assert store.list_issues(viewer)["total"] == 1
    with pytest.raises(ControlError):
        store.set_issue(viewer, **values, status="ignored", note="Not permitted")

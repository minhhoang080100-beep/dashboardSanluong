"""Machine auth uses only isolated SQLite files and synthetic credentials."""
import hashlib
import json
import sqlite3

import pytest

from backend.corporate_api import auth, manage_clients
from backend.corporate_api.auth import MachineAuthError, MachineStore, require_access


PASSWORD = "synthetic-machine-secret-2026"
ROTATED_PASSWORD = "synthetic-rotated-secret-2026"


@pytest.fixture
def state(tmp_path, monkeypatch):
    monkeypatch.setenv("DASHBOARD_STATE_PATH", str(tmp_path / "control.sqlite3"))
    monkeypatch.delenv("CORPORATE_STATE_PATH", raising=False)
    now = [2_000_000_000.0]
    store = MachineStore(tmp_path / "corporate.sqlite3", clock=lambda: now[0])
    client = store.create_client("corporate-reader", PASSWORD, company_ids=["CNT"], resources=["shipDetails", "customers"])
    return store, client, now


def login(store, password=PASSWORD, username="corporate-reader", client_key="synthetic-client"):
    return store.login(username, password, client_key)


def test_opaque_token_contract_hashed_storage_and_persistence(state):
    store, client, now = state
    result = login(store)
    assert set(result) == {"code", "message", "accessToken", "expiresIn"}
    assert result["code"] == "1" and result["expiresIn"] == 28_800
    token = result["accessToken"]
    assert len(token) == 43 and "." not in token
    assert store.authenticate(token) == client
    assert MachineStore(store.path, clock=lambda: now[0]).authenticate(token) == client
    with sqlite3.connect(store.path) as db:
        stored_hash = db.execute("SELECT token_hash FROM machine_tokens").fetchone()[0]
        password_hash = db.execute("SELECT password_hash FROM machine_clients").fetchone()[0]
    assert stored_hash == hashlib.sha256(token.encode()).hexdigest()
    assert token not in stored_hash and PASSWORD not in password_hash
    assert password_hash.startswith("scrypt$32768$8$3$")
    assert "password_hash" not in client and "accessToken" not in client


def test_expiry_is_enforced_at_boundary_and_cleanup_removes_expired_token(state):
    store, _, now = state
    token = login(store)["accessToken"]
    now[0] += 28_799
    store.authenticate(token)
    now[0] += 1
    with pytest.raises(MachineAuthError) as failure:
        store.authenticate(token)
    assert failure.value.status_code == 401
    replacement = login(store)["accessToken"]
    assert replacement != token
    with sqlite3.connect(store.path) as db:
        assert db.execute("SELECT COUNT(*) FROM machine_tokens").fetchone()[0] == 1


@pytest.mark.parametrize("ttl", [0, -1, 28_801, True, 1.5])
def test_invalid_lifetime_rejected_before_creating_file(tmp_path, ttl):
    path = tmp_path / "invalid.sqlite3"
    with pytest.raises(ValueError):
        MachineStore(path, token_ttl_seconds=ttl)
    assert not path.exists()


def test_password_rotation_disabling_and_scope_update_revoke_tokens(state):
    store, _, _ = state
    original = login(store)["accessToken"]
    store.update_client("corporate-reader", password=ROTATED_PASSWORD)
    with pytest.raises(MachineAuthError):
        store.authenticate(original)
    with pytest.raises(MachineAuthError):
        login(store)
    current = login(store, ROTATED_PASSWORD)["accessToken"]
    store.update_client("corporate-reader", resources=["customers"])
    with pytest.raises(MachineAuthError):
        store.authenticate(current)
    restricted = login(store, ROTATED_PASSWORD)["accessToken"]
    assert store.authenticate(restricted)["resources"] == ["customers"]
    store.disable_client("corporate-reader")
    for attempt in (lambda: store.authenticate(restricted), lambda: login(store, ROTATED_PASSWORD)):
        with pytest.raises(MachineAuthError) as failure:
            attempt()
        assert failure.value.status_code == 401
    store.update_client("corporate-reader", enabled=True)
    with pytest.raises(MachineAuthError):
        store.authenticate(restricted)
    store.authenticate(login(store, ROTATED_PASSWORD)["accessToken"])


def test_company_and_resource_authorization_are_both_enforced(state):
    store, _, _ = state
    principal = store.authenticate(login(store)["accessToken"])
    assert require_access(principal, "CNT", "shipDetails") is principal
    for company, resource in [("OTHER", "shipDetails"), ("cnt", "shipDetails"), ("CNT", "containerSize"), ("CNT", "RORO"), (None, "customers")]:
        with pytest.raises(MachineAuthError) as failure:
            require_access(principal, company, resource)
        assert failure.value.status_code == 403
    with pytest.raises(MachineAuthError):
        require_access({**principal, "enabled": False}, "CNT", "customers")
    from backend.corporate_api.registry import MODELS
    assert auth.RESOURCE_KEYS == frozenset(MODELS)
    assert len(auth.RESOURCE_KEYS) == 32


@pytest.mark.parametrize("companies,resources", [
    ([], ["customers"]), (["OTHER"], ["customers"]), (["CNT", "CNT"], ["customers"]),
    (["CNT"], []), (["CNT"], ["*"]), (["CNT"], ["customers", "customers"]),
])
def test_grants_must_be_explicit_known_and_unique(state, companies, resources):
    store, _, _ = state
    with pytest.raises(MachineAuthError) as failure:
        store.create_client("second-client", PASSWORD, company_ids=companies, resources=resources)
    assert failure.value.status_code == 422
    with sqlite3.connect(store.path) as db:
        assert db.execute("SELECT COUNT(*) FROM machine_clients").fetchone()[0] == 1


def test_malformed_stored_grants_fail_closed(state):
    store, _, _ = state
    token = login(store)["accessToken"]
    with sqlite3.connect(store.path) as db:
        db.execute("UPDATE machine_clients SET company_ids=?", (json.dumps(["OTHER"]),))
    with pytest.raises(MachineAuthError) as failure:
        store.authenticate(token)
    assert failure.value.status_code == 503


def test_machine_and_human_sessions_are_isolated(state, tmp_path):
    from backend.control_store import ControlError, ControlStore
    store, _, _ = state
    humans = ControlStore(tmp_path / "control.sqlite3")
    created = humans.bootstrap_admin("human-admin")
    human_token = humans.login("human-admin", created["temporary_password"], "synthetic-human-client")["token"]
    machine_token = login(store)["accessToken"]
    with pytest.raises(MachineAuthError):
        store.authenticate(human_token)
    with pytest.raises(ControlError):
        humans.authenticate(machine_token)
    store.disable_client("corporate-reader")
    assert humans.authenticate(human_token)["username"] == "human-admin"
    with sqlite3.connect(store.path) as db:
        assert db.execute("SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name='users'").fetchone()[0] == 0


def test_default_storage_follows_dashboard_volume_and_override_is_separate(tmp_path, monkeypatch):
    control = tmp_path / "volume" / "control.sqlite3"
    monkeypatch.setenv("DASHBOARD_STATE_PATH", str(control))
    monkeypatch.delenv("CORPORATE_STATE_PATH", raising=False)
    store = MachineStore()
    assert store.path == control.with_name("corporate.sqlite3")
    assert not control.exists()
    override = tmp_path / "override" / "machine.sqlite3"
    monkeypatch.setenv("CORPORATE_STATE_PATH", str(override))
    assert MachineStore().path == override
    with pytest.raises(MachineAuthError) as failure:
        MachineStore(control)
    assert failure.value.code == "MACHINE_STORAGE_CONFLICT" and not control.exists()


def test_failed_login_limit_is_shared_across_ips_and_instances(state):
    store, _, now = state
    for index in range(5):
        with pytest.raises(MachineAuthError) as failure:
            login(store, "incorrect-password", username=" CORPORATE-READER ", client_key=f"client-{index}")
        assert failure.value.status_code == 401
    other = MachineStore(store.path, clock=lambda: now[0])
    with pytest.raises(MachineAuthError) as failure:
        login(other, client_key="different-client")
    assert failure.value.status_code == 429 and failure.value.retry_after == 900
    now[0] += 900
    assert login(other)["code"] == "1"
    with sqlite3.connect(store.path) as db:
        assert db.execute("SELECT COUNT(*) FROM machine_login_attempts").fetchone()[0] == 0


def test_concurrent_attempts_cannot_bypass_account_limit(state, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier
    store, _, _ = state
    monkeypatch.setattr(auth, "verify_password", lambda password, encoded: False)
    for index in range(4):
        with pytest.raises(MachineAuthError):
            login(store, client_key=f"initial-{index}")
    ready = Barrier(2)

    def attempt(index):
        ready.wait(timeout=5)
        try:
            login(store, client_key=f"concurrent-{index}")
        except MachineAuthError as exc:
            return exc.status_code
        return 200

    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(attempt, [1, 2])) == [401, 429]
    with sqlite3.connect(store.path) as db:
        assert db.execute("SELECT COUNT(*) FROM machine_login_attempts").fetchone()[0] == 5


def test_client_limit_covers_many_account_names_and_keys_are_hashed(state, monkeypatch):
    store, _, _ = state
    # Exercise the limiter independently of repeated expensive scrypt work.
    monkeypatch.setattr(auth, "verify_password", lambda password, encoded: False)
    for index in range(30):
        with pytest.raises(MachineAuthError) as failure:
            login(store, username=f"unknown-{index}", client_key="synthetic-network-address")
        assert failure.value.status_code == 401
    with pytest.raises(MachineAuthError) as failure:
        login(store, username="unknown-next", client_key="synthetic-network-address")
    assert failure.value.status_code == 429
    with sqlite3.connect(store.path) as db:
        rows = db.execute("SELECT account_key,client_key FROM machine_login_attempts").fetchall()
    assert len(rows) == 30
    assert all(len(account) == len(client) == 64 for account, client in rows)
    assert all("unknown" not in account and "synthetic" not in client for account, client in rows)


def test_attempt_storage_is_bounded_then_recovers_after_window(state, monkeypatch):
    store, _, now = state
    monkeypatch.setattr(auth, "MAX_LOGIN_ATTEMPTS", 3)
    monkeypatch.setattr(auth, "verify_password", lambda password, encoded: False)
    for index in range(3):
        with pytest.raises(MachineAuthError):
            login(store, username=f"unknown-{index}", client_key=f"client-{index}")
    with pytest.raises(MachineAuthError) as failure:
        login(store, username="new-unknown", client_key="new-client")
    assert failure.value.status_code == 429
    now[0] += 901
    with pytest.raises(MachineAuthError) as failure:
        login(store, username="new-unknown", client_key="new-client")
    assert failure.value.status_code == 401
    with sqlite3.connect(store.path) as db:
        assert db.execute("SELECT COUNT(*) FROM machine_login_attempts").fetchone()[0] == 1


def test_unknown_and_disabled_accounts_still_verify_and_errors_reveal_no_credentials(state, monkeypatch, caplog):
    store, _, _ = state
    store.disable_client("corporate-reader")
    seen = []
    monkeypatch.setattr(auth, "verify_password", lambda password, encoded: seen.append(encoded) or False)
    failures = []
    for username in ("unknown-user", "corporate-reader"):
        with pytest.raises(MachineAuthError) as failure:
            login(store, "never-log-this-secret", username=username)
        failures.append((failure.value.status_code, failure.value.code, str(failure.value)))
    assert len(seen) == 2 and all(value.startswith("scrypt$") for value in seen)
    assert failures[0] == failures[1]
    assert "never-log-this-secret" not in caplog.text + str(failures)
    assert "unknown-user" not in str(failures) and "corporate-reader" not in str(failures)


@pytest.mark.parametrize("password", ["short", "x" * 1025, "ổ" * 400, "\ud800" * 12])
def test_password_length_and_encoding_are_bounded(state, password):
    store, _, _ = state
    with pytest.raises(MachineAuthError) as failure:
        store.update_client("corporate-reader", password=password)
    assert failure.value.status_code == 422


def test_oversized_login_secret_uses_bounded_dummy_value(state, monkeypatch):
    store, _, _ = state
    seen = []
    monkeypatch.setattr(auth, "verify_password", lambda password, encoded: seen.append(password) or False)
    with pytest.raises(MachineAuthError) as failure:
        login(store, "x" * 100_000)
    assert failure.value.status_code == 401 and len(seen[0]) < 100


def test_storage_failure_is_redacted(state, monkeypatch, caplog):
    store, _, _ = state
    private_error = "server=private-endpoint;password=never-log-db-secret"

    def unavailable(*args, **kwargs):
        raise sqlite3.OperationalError(private_error)

    monkeypatch.setattr(auth.sqlite3, "connect", unavailable)
    with pytest.raises(MachineAuthError) as failure:
        login(store)
    assert failure.value.status_code == 503
    assert private_error not in str(failure.value) + caplog.text
    assert failure.value.__suppress_context__


def test_connections_close_after_success_auth_failure_and_setup_error(state, monkeypatch):
    store, _, _ = state
    original_connect = sqlite3.connect
    opened, closed = [], []
    fail_setup = [False]

    class TrackedConnection(sqlite3.Connection):
        def execute(self, statement, parameters=()):
            if fail_setup[0] and statement == "PRAGMA foreign_keys=ON":
                raise sqlite3.DatabaseError("synthetic setup failure")
            return super().execute(statement, parameters)

        def close(self):
            closed.append(id(self))
            return super().close()

    def connect(*args, **kwargs):
        connection = original_connect(*args, **kwargs, factory=TrackedConnection)
        opened.append(id(connection))
        return connection

    monkeypatch.setattr(auth.sqlite3, "connect", connect)
    token = login(store)["accessToken"]
    store.authenticate(token)
    with pytest.raises(MachineAuthError):
        login(store, "wrong-synthetic-password")
    with pytest.raises(MachineAuthError):
        store.update_client("unknown-client", enabled=False)
    fail_setup[0] = True
    with pytest.raises(MachineAuthError) as failure:
        store.authenticate(token)
    assert failure.value.status_code == 503
    assert len(opened) == 5 and opened == closed


def test_cli_create_update_disable_keeps_secrets_out_of_output(tmp_path, monkeypatch, capsys):
    path = tmp_path / "machine.sqlite3"
    passwords = iter([PASSWORD, PASSWORD, ROTATED_PASSWORD, ROTATED_PASSWORD])
    monkeypatch.setattr(manage_clients.getpass, "getpass", lambda prompt: next(passwords))
    common = ["--username", "corporate-reader", "--state-path", str(path)]
    assert manage_clients.main(["create", *common, "--company", "CNT", "--resource", "customers"]) == 0
    store = MachineStore(path)
    old_token = login(store)["accessToken"]
    assert manage_clients.main(["update", *common]) == 0
    with pytest.raises(MachineAuthError):
        store.authenticate(old_token)
    assert login(store, ROTATED_PASSWORD)["code"] == "1"
    assert manage_clients.main(["disable", *common]) == 0
    with pytest.raises(MachineAuthError):
        login(store, ROTATED_PASSWORD)
    captured = capsys.readouterr()
    for secret in (PASSWORD, ROTATED_PASSWORD, old_token):
        assert secret not in captured.out + captured.err


def test_cli_rejects_secret_arguments_without_echoing_them(capsys):
    with pytest.raises(SystemExit) as failure:
        manage_clients.main(["create", "--username", "corporate-reader", "--password", "never-echo-this"])
    assert failure.value.code == 2
    captured = capsys.readouterr()
    assert "never-echo-this" not in captured.out + captured.err


def test_cli_refuses_echoing_password_fallback_before_creating_store(tmp_path, monkeypatch, capsys):
    path = tmp_path / "machine.sqlite3"

    def no_terminal(prompt):
        raise manage_clients.getpass.GetPassWarning("echoing input")

    monkeypatch.setattr(manage_clients.getpass, "getpass", no_terminal)
    assert manage_clients.main(["create", "--username", "corporate-reader", "--state-path", str(path),
                               "--company", "CNT", "--resource", "customers"]) == 1
    assert not path.exists()
    assert "private password prompt" in capsys.readouterr().err

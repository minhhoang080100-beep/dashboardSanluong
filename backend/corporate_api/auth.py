"""Independent machine credentials for the corporate API; no source DB access."""
from contextlib import contextmanager
import hashlib
import json
import math
import os
from pathlib import Path
import re
import secrets
import sqlite3
import time

try:
    from ..control_store import hash_password, verify_password
except ImportError:  # ``corporate_api`` imported from the backend working directory.
    from control_store import hash_password, verify_password


ALLOWED_COMPANIES = frozenset({"CNT"})
from .registry import MODELS

RESOURCE_KEYS = frozenset(MODELS)
MAX_TOKEN_TTL_SECONDS = 28_800
LOGIN_WINDOW_SECONDS = 900
ACCOUNT_FAILURE_LIMIT = 5
CLIENT_FAILURE_LIMIT = 30
MAX_LOGIN_ATTEMPTS = 10_000


class MachineAuthError(Exception):
    """Safe fields for the corporate router's error envelope."""

    def __init__(self, status_code, code, message, retry_after=None):
        self.status_code = status_code
        self.code = code
        self.message = message
        self.retry_after = retry_after
        super().__init__(message)


def _digest(value):
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _username(value):
    if not isinstance(value, str):
        return None
    value = value.strip().lower()
    return value if re.fullmatch(r"[a-z0-9][a-z0-9._-]{2,79}", value) else None


def _password_valid(value, *, new=False):
    if not isinstance(value, str) or len(value) < (12 if new else 1) or len(value) > 1024:
        return False
    try:
        return len(value.encode("utf-8")) <= 1024
    except UnicodeError:
        return False


def _permissions(values, allowed, code):
    if (not isinstance(values, (list, tuple)) or not values
            or any(not isinstance(value, str) for value in values)
            or not set(values) <= allowed or len(values) != len(set(values))):
        raise MachineAuthError(422, code, "Danh sách quyền truy cập không hợp lệ.")
    return sorted(values)


def require_access(principal, company_id, resource):
    """Check both the deployment's fixed allowlist and this machine's grants."""
    if (not isinstance(principal, dict) or principal.get("enabled") is not True
            or not isinstance(company_id, str) or company_id not in ALLOWED_COMPANIES
            or not isinstance(resource, str) or resource not in RESOURCE_KEYS
            or company_id not in principal.get("company_ids", [])
            or resource not in principal.get("resources", [])):
        raise MachineAuthError(403, "MACHINE_SCOPE_FORBIDDEN", "Tài khoản máy không có quyền truy cập dữ liệu này.")
    return principal


class MachineStore:
    def __init__(self, path=None, *, clock=time.time, token_ttl_seconds=MAX_TOKEN_TTL_SECONDS):
        if (isinstance(token_ttl_seconds, bool) or not isinstance(token_ttl_seconds, int)
                or not 1 <= token_ttl_seconds <= MAX_TOKEN_TTL_SECONDS):
            raise ValueError("Machine token lifetime must be between 1 and 28800 seconds.")
        backend_path = Path(__file__).resolve().parents[1]
        control_path = Path(os.environ.get("DASHBOARD_STATE_PATH") or backend_path / ".data" / "control.sqlite3").resolve()
        configured = path or os.environ.get("CORPORATE_STATE_PATH") or control_path.with_name("corporate.sqlite3")
        try:
            target = Path(configured)
            if target.is_symlink():
                raise MachineAuthError(503, "MACHINE_STORAGE_UNAVAILABLE", "Kho xác thực API công ty mẹ không khả dụng.")
            self.path = target.resolve()
            if self.path == control_path or (self.path.exists() and control_path.exists() and self.path.samefile(control_path)):
                raise MachineAuthError(503, "MACHINE_STORAGE_CONFLICT", "Kho xác thực tài khoản máy phải tách khỏi kho dashboard.")
            self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        except OSError:
            raise MachineAuthError(503, "MACHINE_STORAGE_UNAVAILABLE", "Kho xác thực API công ty mẹ không khả dụng.") from None
        self.clock = clock
        self.token_ttl_seconds = token_ttl_seconds
        self._dummy_hash = None
        with self._db() as db:
            db.execute("PRAGMA journal_mode=WAL")
            db.executescript("""
                CREATE TABLE IF NOT EXISTS machine_clients (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    username TEXT NOT NULL UNIQUE COLLATE NOCASE,
                    password_hash TEXT NOT NULL, company_ids TEXT NOT NULL,
                    resources TEXT NOT NULL, enabled INTEGER NOT NULL CHECK(enabled IN (0,1)),
                    created_at REAL NOT NULL, updated_at REAL NOT NULL
                );
                CREATE TABLE IF NOT EXISTS machine_tokens (
                    token_hash TEXT PRIMARY KEY, client_id INTEGER NOT NULL REFERENCES machine_clients(id),
                    created_at REAL NOT NULL, expires_at REAL NOT NULL, revoked_at REAL
                );
                CREATE INDEX IF NOT EXISTS machine_tokens_client ON machine_tokens(client_id);
                CREATE TABLE IF NOT EXISTS machine_login_attempts (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, account_key TEXT NOT NULL,
                    client_key TEXT NOT NULL, created_at REAL NOT NULL
                );
                CREATE INDEX IF NOT EXISTS machine_attempts_account ON machine_login_attempts(account_key,created_at);
                CREATE INDEX IF NOT EXISTS machine_attempts_client ON machine_login_attempts(client_key,created_at);
            """)
        try:
            self.path.chmod(0o600)
        except OSError:
            if os.name != "nt":
                raise MachineAuthError(503, "MACHINE_STORAGE_UNAVAILABLE", "Kho xác thực API công ty mẹ không khả dụng.") from None

    @contextmanager
    def _db(self, *, write=False):
        db = None
        try:
            db = sqlite3.connect(self.path, timeout=5)
            db.row_factory = sqlite3.Row
            db.execute("PRAGMA foreign_keys=ON")
            if write:
                db.execute("BEGIN IMMEDIATE")
            yield db
            if write:
                db.commit()
        except (sqlite3.Error, OSError):
            if db is not None:
                db.rollback()
            raise MachineAuthError(503, "MACHINE_STORAGE_UNAVAILABLE", "Kho xác thực API công ty mẹ không khả dụng.") from None
        except BaseException:
            if db is not None:
                db.rollback()
            raise
        finally:
            if db is not None:
                db.close()

    @staticmethod
    def _public(row):
        try:
            companies = _permissions(json.loads(row["company_ids"]), ALLOWED_COMPANIES, "INVALID_MACHINE_COMPANIES")
            resources = _permissions(json.loads(row["resources"]), RESOURCE_KEYS, "INVALID_MACHINE_RESOURCES")
        except (ValueError, TypeError, MachineAuthError):
            raise MachineAuthError(503, "MACHINE_STORAGE_UNAVAILABLE", "Kho xác thực API công ty mẹ không khả dụng.") from None
        return {"id": row["id"], "username": row["username"], "company_ids": companies,
                "resources": resources, "enabled": bool(row["enabled"])}

    def create_client(self, username, password, *, company_ids, resources, enabled=True):
        name = _username(username)
        if name is None:
            raise MachineAuthError(422, "INVALID_MACHINE_USERNAME", "Tên tài khoản máy phải có 3–80 ký tự chữ, số, dấu chấm, gạch dưới hoặc gạch ngang.")
        if not _password_valid(password, new=True):
            raise MachineAuthError(422, "INVALID_MACHINE_PASSWORD", "Mật khẩu phải có ít nhất 12 ký tự và không quá 1024 byte.")
        companies = _permissions(company_ids, ALLOWED_COMPANIES, "INVALID_MACHINE_COMPANIES")
        resources = _permissions(resources, RESOURCE_KEYS, "INVALID_MACHINE_RESOURCES")
        if not isinstance(enabled, bool):
            raise MachineAuthError(422, "INVALID_MACHINE_STATUS", "Trạng thái tài khoản máy không hợp lệ.")
        encoded = hash_password(password)
        with self._db(write=True) as db:
            if db.execute("SELECT 1 FROM machine_clients WHERE username=?", (name,)).fetchone():
                raise MachineAuthError(409, "MACHINE_CLIENT_EXISTS", "Tài khoản máy đã tồn tại.")
            now = self.clock()
            cursor = db.execute("INSERT INTO machine_clients(username,password_hash,company_ids,resources,enabled,created_at,updated_at) VALUES(?,?,?,?,?,?,?)",
                                (name, encoded, json.dumps(companies), json.dumps(resources), int(enabled), now, now))
            return self._public(db.execute("SELECT * FROM machine_clients WHERE id=?", (cursor.lastrowid,)).fetchone())

    def update_client(self, username, *, password=None, company_ids=None, resources=None, enabled=None):
        name = _username(username)
        if name is None:
            raise MachineAuthError(404, "MACHINE_CLIENT_NOT_FOUND", "Không tìm thấy tài khoản máy.")
        if password is not None and not _password_valid(password, new=True):
            raise MachineAuthError(422, "INVALID_MACHINE_PASSWORD", "Mật khẩu phải có ít nhất 12 ký tự và không quá 1024 byte.")
        companies = _permissions(company_ids, ALLOWED_COMPANIES, "INVALID_MACHINE_COMPANIES") if company_ids is not None else None
        grants = _permissions(resources, RESOURCE_KEYS, "INVALID_MACHINE_RESOURCES") if resources is not None else None
        if enabled is not None and not isinstance(enabled, bool):
            raise MachineAuthError(422, "INVALID_MACHINE_STATUS", "Trạng thái tài khoản máy không hợp lệ.")
        encoded = hash_password(password) if password is not None else None
        with self._db(write=True) as db:
            row = db.execute("SELECT * FROM machine_clients WHERE username=?", (name,)).fetchone()
            if row is None:
                raise MachineAuthError(404, "MACHINE_CLIENT_NOT_FOUND", "Không tìm thấy tài khoản máy.")
            now = self.clock()
            db.execute("UPDATE machine_clients SET password_hash=?,company_ids=?,resources=?,enabled=?,updated_at=? WHERE id=?",
                       (encoded if encoded is not None else row["password_hash"],
                        json.dumps(companies) if companies is not None else row["company_ids"],
                        json.dumps(grants) if grants is not None else row["resources"],
                        int(enabled) if enabled is not None else row["enabled"], now, row["id"]))
            db.execute("UPDATE machine_tokens SET revoked_at=? WHERE client_id=? AND revoked_at IS NULL", (now, row["id"]))
            if encoded is not None:
                db.execute("DELETE FROM machine_login_attempts WHERE account_key=?", (_digest(name),))
            return self._public(db.execute("SELECT * FROM machine_clients WHERE id=?", (row["id"],)).fetchone())

    def disable_client(self, username):
        return self.update_client(username, enabled=False)

    def _rate_limit(self, db, account_key, client_key):
        now = self.clock()
        db.execute("DELETE FROM machine_login_attempts WHERE created_at<=?", (now - LOGIN_WINDOW_SECONDS,))
        for column, key, limit in (("account_key", account_key, ACCOUNT_FAILURE_LIMIT), ("client_key", client_key, CLIENT_FAILURE_LIMIT)):
            count, oldest = db.execute(f"SELECT COUNT(*),MIN(created_at) FROM machine_login_attempts WHERE {column}=?", (key,)).fetchone()
            if count >= limit:
                wait = max(1, math.ceil(oldest + LOGIN_WINDOW_SECONDS - now))
                raise MachineAuthError(429, "MACHINE_LOGIN_THROTTLED", "Có quá nhiều lần đăng nhập thất bại. Vui lòng thử lại sau.", wait)
        if db.execute("SELECT COUNT(*) FROM machine_login_attempts").fetchone()[0] >= MAX_LOGIN_ATTEMPTS:
            raise MachineAuthError(429, "MACHINE_LOGIN_THROTTLED", "Có quá nhiều lần đăng nhập thất bại. Vui lòng thử lại sau.", LOGIN_WINDOW_SECONDS)

    def login(self, username, password, client_key="local"):
        name = _username(username)
        account_key = _digest(name or "invalid-username")
        client_key = _digest(str(client_key)[:256])
        failure = False
        with self._db(write=True) as db:
            self._rate_limit(db, account_key, client_key)
            row = db.execute("SELECT * FROM machine_clients WHERE username=?", (name,)).fetchone() if name else None
            if self._dummy_hash is None:
                self._dummy_hash = hash_password(secrets.token_urlsafe(32))
            bounded = _password_valid(password)
            # Invalid/unknown credentials still take one password verification.
            valid = verify_password(password if bounded else "invalid-machine-password", row["password_hash"] if row else self._dummy_hash)
            if row is None or not row["enabled"] or not bounded or not valid:
                db.execute("INSERT INTO machine_login_attempts(account_key,client_key,created_at) VALUES(?,?,?)", (account_key, client_key, self.clock()))
                failure = True
            else:
                self._public(row)  # Fail closed if a stored grant is invalid.
                now = self.clock()
                db.execute("DELETE FROM machine_login_attempts WHERE account_key=?", (account_key,))
                db.execute("DELETE FROM machine_tokens WHERE expires_at<=? OR revoked_at IS NOT NULL", (now,))
                token = secrets.token_urlsafe(32)
                db.execute("INSERT INTO machine_tokens(token_hash,client_id,created_at,expires_at) VALUES(?,?,?,?)",
                           (_digest(token), row["id"], now, now + self.token_ttl_seconds))
                result = {"code": "1", "message": "Đăng nhập thành công.", "accessToken": token, "expiresIn": self.token_ttl_seconds}
        if failure:
            raise MachineAuthError(401, "MACHINE_CREDENTIALS_INVALID", "Thông tin đăng nhập không hợp lệ.")
        return result

    def authenticate(self, token):
        if not isinstance(token, str) or not re.fullmatch(r"[A-Za-z0-9_-]{43}", token):
            raise MachineAuthError(401, "MACHINE_TOKEN_INVALID", "Token không hợp lệ hoặc đã hết hạn.")
        with self._db() as db:
            row = db.execute("""SELECT c.* FROM machine_tokens t JOIN machine_clients c ON c.id=t.client_id
                WHERE t.token_hash=? AND t.revoked_at IS NULL AND t.expires_at>? AND c.enabled=1""",
                             (_digest(token), self.clock())).fetchone()
            if row is None:
                raise MachineAuthError(401, "MACHINE_TOKEN_INVALID", "Token không hợp lệ hoặc đã hết hạn.")
            return self._public(row)

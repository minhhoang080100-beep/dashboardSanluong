"""Internal dashboard state. This module never connects to or writes to TOS.

Passwords use scrypt N=2**15,r=8,p=3 (OWASP's 32 MiB configuration).
Only SHA-256 digests of independently random bearer tokens are persisted.
"""
from contextlib import contextmanager
from calendar import monthrange
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation
import hashlib
import hmac
import json
import os
from pathlib import Path
import re
import secrets
import sqlite3
import time

from fastapi import HTTPException

TERMINALS = frozenset({"cua_lo", "ben_thuy"})
ROLES = frozenset({"admin", "manager", "viewer"})
PRODUCTION_SCOPE_LABELS = {'nghe_tinh': 'Cảng Nghệ Tĩnh', 'vietsun': 'Cầu 5', 'unclassified': 'Chưa xác định cầu'}
PLAN_PERIOD_TYPES = frozenset({'week', 'month', 'quarter', 'year', 'custom', 'voyage'})
PLAN_PERIOD_FIELDS = ('week', 'month', 'quarter', 'year', 'start_date', 'end_date', 'voyage_id')


def plan_period(value):
    """Canonical identity and full target bounds; no source or state access."""
    kind = value.get('period_type', 'month')
    accepted = {'week': {'week'}, 'month': {'month'}, 'quarter': {'quarter'}, 'year': {'year'},
                'custom': {'start_date', 'end_date'}, 'voyage': {'voyage_id'}}
    invalid = lambda: ControlError(422, 'INVALID_PLAN_PERIOD', 'Kỳ kế hoạch không hợp lệ; chỉ điền các trường của loại kỳ đã chọn.')
    if kind not in accepted or any(value.get(field) is not None for field in set(PLAN_PERIOD_FIELDS) - accepted[kind]):
        raise invalid()
    try:
        if kind == 'voyage':
            voyage = value.get('voyage_id')
            if isinstance(voyage, bool) or not isinstance(voyage, int) or not 1 <= voyage <= 2147483647:
                raise ValueError()
            return str(voyage), None, None
        if kind == 'week':
            key = value.get('week')
            if not isinstance(key, str) or not re.fullmatch(r'20\d{2}-W(0[1-9]|[1-4]\d|5[0-3])', key):
                raise ValueError()
            # ISO week years differ from calendar years at New Year. The
            # standard constructor also rejects W53 in a 52-week year.
            year, week = int(key[:4]), int(key[-2:])
            start, end = date.fromisocalendar(year, week, 1), date.fromisocalendar(year, week, 7)
        elif kind == 'month':
            key = value.get('month')
            if not isinstance(key, str) or not re.fullmatch(r'20\d{2}-(0[1-9]|1[0-2])', key):
                raise ValueError()
            start = date.fromisoformat(key + '-01')
            end = start.replace(day=monthrange(start.year, start.month)[1])
        elif kind == 'quarter':
            key = value.get('quarter')
            if not isinstance(key, str) or not re.fullmatch(r'20\d{2}-Q[1-4]', key):
                raise ValueError()
            year, quarter = int(key[:4]), int(key[-1])
            start = date(year, quarter * 3 - 2, 1)
            end = date(year, quarter * 3, monthrange(year, quarter * 3)[1])
        elif kind == 'year':
            year = value.get('year')
            if isinstance(year, bool) or not isinstance(year, int) or not 2000 <= year <= 2099:
                raise ValueError()
            key, start, end = str(year), date(year, 1, 1), date(year, 12, 31)
        else:
            first, last = value.get('start_date'), value.get('end_date')
            # ISO dates only: timestamps and locale-dependent dates are ambiguous.
            if any(not isinstance(day, (str, date)) or isinstance(day, datetime) for day in (first, last)):
                raise ValueError()
            start, end = date.fromisoformat(str(first)), date.fromisoformat(str(last))
            if not 2000 <= start.year <= end.year <= 2099 or not 0 <= (end - start).days < 366:
                raise ValueError()
            key = start.isoformat() + '/' + end.isoformat()
        return key, start, end
    except (ValueError, TypeError):
        raise invalid() from None


def saved_plan_period(kind, key):
    values = {field: None for field in PLAN_PERIOD_FIELDS}
    if kind == 'custom':
        values['start_date'], values['end_date'] = key.split('/')
    elif kind == 'voyage':
        values['voyage_id'] = int(key)
    elif kind == 'year':
        values['year'] = int(key)
    else:
        values[kind] = key
    _, start, end = plan_period({'period_type': kind, **values})
    return {**values, 'period_key': key, 'period_start': start.isoformat() if start else None,
            'period_end': end.isoformat() if end else None}


def production_scope_context(report):
    """Describe saved scope without assigning today's rule to historical data."""
    meta = report.get('meta', {})
    scope = meta.get('filters', {}).get('production_scope')
    rule_version = meta.get('berth_rule_version')
    legacy = not isinstance(scope, str) or scope not in PRODUCTION_SCOPE_LABELS or not isinstance(rule_version, str) or not rule_version.strip()
    label = 'Phạm vi cũ — chưa lưu quy tắc cầu cập đầu tiên' if legacy else PRODUCTION_SCOPE_LABELS[scope]
    return {'production_scope': scope, 'production_scope_label': label,
            'berth_rule_version': rule_version, 'legacy_scope': legacy}


class ControlError(HTTPException):
    def __init__(self, status_code: int, code: str, message: str):
        self.code, self.message = code, message
        super().__init__(status_code, detail={"code": code, "message": message})


def require_scope(user: dict, terminal: str):
    requested = TERMINALS if terminal == "all" else {terminal}
    if not requested <= TERMINALS:
        raise ControlError(422, "INVALID_TERMINAL", "Cảng không hợp lệ.")
    if not requested <= set(user.get("terminals", [])):
        raise ControlError(403, "TERMINAL_FORBIDDEN", "Tài khoản không có quyền xem cảng này.")
    return user


def require_editor(user: dict):
    if user.get("role") not in {"admin", "manager"}:
        raise ControlError(403, "EDITOR_REQUIRED", "Cần quyền quản lý để thực hiện thao tác này.")
    return user


def require_admin(user: dict):
    if user.get("role") != "admin":
        raise ControlError(403, "ADMIN_REQUIRED", "Cần quyền quản trị.")
    return user


def _json(value):
    def encode(item):
        if isinstance(item, Decimal):
            return str(item)
        if isinstance(item, (date, datetime)):
            return item.isoformat()
        raise TypeError("Unsupported snapshot value")
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False, default=encode)


def _iso(timestamp):
    return datetime.fromtimestamp(timestamp, timezone.utc).isoformat()


def _digest(value: str):
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def plan_progress_rows(plans, actuals):
    by_key = {(row['terminal'], row['metric']): row for row in plans}
    result = []
    for terminal, values in actuals.items():
        for metric in ['tonnage', 'teu']:
            plan = by_key.get((terminal, metric))
            actual, status = values.get(metric), values.get(metric + '_status', 'unavailable')
            target = float(Decimal(str(plan['amount']))) if plan else None
            complete = status in {'ready', 'empty'} and actual is not None
            result.append({'terminal': terminal, 'terminal_name': {'cua_lo': 'Cửa Lò', 'ben_thuy': 'Bến Thủy'}[terminal], 'metric': metric,
                'plan_id': plan['id'] if plan else None, 'plan_version': plan['version'] if plan else None,
                'reference': plan.get('reference') if plan else None, 'target': target,
                'approved_by': plan.get('approved_by') if plan else None, 'approved_at': plan.get('approved_at') if plan else None,
                'actual': actual, 'actual_status': status,
                'remaining': max(0, round(target - actual, 3)) if target is not None and complete else None,
                'completion_percent': round(actual / target * 100, 1) if target and complete else None,
                'status': 'missing_plan' if plan is None else 'incomplete_actual' if not complete else 'zero_target' if target == 0 else 'ready'})
    return result


def throughput_progress_item(period, plans, actual, actual_status, target_source, *, complete_target=True):
    """One period, one target. Partial source values never become confirmed progress."""
    target = sum((Decimal(row['amount_decimal']) for row in plans), Decimal(0)) if complete_target else None
    actual_value = Decimal(str(actual)) if actual is not None else None
    if actual_value is not None and not actual_value.is_finite():
        actual_value = None
    status = ('missing_plan' if target is None else 'unavailable' if actual_value is None or actual_status not in {'ready', 'empty', 'partial'}
              else 'negative_actual' if actual_value < 0 else 'zero_target' if target == 0
              else 'partial' if actual_status == 'partial' else 'ready')
    percentage = float(actual_value / target * 100) if status in {'ready', 'partial'} else None
    band = None if percentage is None else ('red' if percentage < 20 else 'orange' if percentage < 40 else
           'yellow' if percentage < 60 else 'light_green' if percentage < 80 else 'dark_green')
    reasons = {'missing_plan': 'Chưa đủ kế hoạch đã duyệt của cả hai cảng cho cùng kỳ.',
               'unavailable': 'Chưa có sản lượng đủ điều kiện tính tỷ lệ.', 'negative_actual': 'Sản lượng âm cần được đối soát trước khi tính tỷ lệ.',
               'zero_target': 'Chỉ tiêu bằng 0; không tính tỷ lệ hoàn thành.', 'partial': 'Tạm tính — số liệu chưa đầy đủ.'}
    return {'key': f"{period['period_type']}:{period['period_key']}", 'period_type': period['period_type'],
            'period_key': period['period_key'], 'start_date': period['period_start'], 'end_date': period['period_end'],
            'target': float(target) if target is not None else None, 'actual': float(actual_value) if actual_value is not None else None,
            'actual_status': actual_status, 'status': status, 'reason': reasons.get(status),
            'completion_percent': percentage if status == 'ready' else None,
            'provisional_completion_percent': percentage if status == 'partial' else None,
            'remaining': float(max(Decimal(0), target - actual_value)) if status == 'ready' else None,
            'band': band, 'achieved': status == 'ready' and percentage >= 100, 'provisional': status == 'partial',
            'target_source': target_source, 'plans': plans}


def _facts_json(rows):
    # A SELECT may return the same records in a different order. Preserve
    # duplicates, while making the digest independent of retrieval order.
    return "[" + ",".join(sorted(_json(row) for row in rows)) + "]"


def _password(value: str):
    if not isinstance(value, str) or len(value) < 12 or len(value.encode("utf-8")) > 1024:
        raise ControlError(422, "INVALID_PASSWORD", "Mật khẩu phải có ít nhất 12 ký tự và không quá 1024 byte.")
    return value


def hash_password(password: str):
    _password(password)
    salt = secrets.token_bytes(16)
    derived = hashlib.scrypt(password.encode("utf-8"), salt=salt, n=32768, r=8, p=3, maxmem=64 * 1024 * 1024, dklen=32)
    return f"scrypt$32768$8$3${salt.hex()}${derived.hex()}"


def verify_password(password: str, stored: str):
    if not isinstance(password, str) or len(password.encode("utf-8")) > 1024:
        return False
    try:
        algorithm, n, r, p, salt, expected = stored.split("$")
        if (algorithm, n, r, p) != ("scrypt", "32768", "8", "3"):
            return False
        derived = hashlib.scrypt(password.encode("utf-8"), salt=bytes.fromhex(salt), n=32768, r=8, p=3, maxmem=64 * 1024 * 1024, dklen=32)
        return hmac.compare_digest(derived.hex(), expected)
    except (ValueError, TypeError):
        return False


class ControlStore:
    def __init__(self, path=None, *, clock=time.time, session_ttl_seconds=28800):
        self.path = Path(path or os.environ.get("DASHBOARD_STATE_PATH") or Path(__file__).parent / ".data" / "control.sqlite3").resolve()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.clock = clock
        if not 1 <= session_ttl_seconds <= 86400:
            raise ValueError("Session lifetime must be between 1 and 86400 seconds")
        self.session_ttl_seconds = session_ttl_seconds
        self._dummy_hash = None
        with self._db() as db:
            db.execute("PRAGMA journal_mode=WAL")
            db.executescript("""
                CREATE TABLE IF NOT EXISTS users (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, username TEXT NOT NULL UNIQUE COLLATE NOCASE,
                    display_name TEXT NOT NULL, role TEXT NOT NULL, terminals TEXT NOT NULL,
                    password_hash TEXT NOT NULL, is_active INTEGER NOT NULL DEFAULT 1,
                    must_change_password INTEGER NOT NULL DEFAULT 1, created_at REAL NOT NULL, updated_at REAL NOT NULL
                );
                CREATE TABLE IF NOT EXISTS sessions (
                    token_hash TEXT PRIMARY KEY, user_id INTEGER NOT NULL REFERENCES users(id),
                    created_at REAL NOT NULL, expires_at REAL NOT NULL, revoked_at REAL
                );
                CREATE INDEX IF NOT EXISTS sessions_user ON sessions(user_id);
                CREATE TABLE IF NOT EXISTS login_attempts (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, username TEXT NOT NULL, client_key TEXT NOT NULL,
                    created_at REAL NOT NULL
                );
                CREATE INDEX IF NOT EXISTS attempts_username ON login_attempts(username, created_at);
                CREATE INDEX IF NOT EXISTS attempts_client ON login_attempts(client_key, created_at);
                CREATE TABLE IF NOT EXISTS plans (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, terminal TEXT NOT NULL, period_type TEXT NOT NULL,
                    period_key TEXT NOT NULL, metric TEXT NOT NULL, amount TEXT NOT NULL,
                    reference TEXT NOT NULL, note TEXT NOT NULL, version INTEGER NOT NULL,
                    status TEXT NOT NULL DEFAULT 'draft', created_by INTEGER NOT NULL REFERENCES users(id),
                    created_at REAL NOT NULL, approved_by INTEGER REFERENCES users(id), approved_at REAL,
                    UNIQUE(terminal, period_type, period_key, metric, version)
                );
                CREATE TRIGGER IF NOT EXISTS approved_plans_no_update BEFORE UPDATE ON plans
                    WHEN OLD.status='approved' BEGIN SELECT RAISE(ABORT, 'Approved plans are immutable'); END;
                CREATE TRIGGER IF NOT EXISTS approved_plans_no_delete BEFORE DELETE ON plans
                    WHEN OLD.status='approved' BEGIN SELECT RAISE(ABORT, 'Approved plans are immutable'); END;
                CREATE TABLE IF NOT EXISTS plan_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, plan_id INTEGER NOT NULL REFERENCES plans(id),
                    action TEXT NOT NULL, snapshot_json TEXT NOT NULL, note TEXT NOT NULL,
                    actor_id INTEGER NOT NULL REFERENCES users(id), created_at REAL NOT NULL
                );
                CREATE INDEX IF NOT EXISTS plan_events_plan ON plan_events(plan_id,id);
                CREATE TRIGGER IF NOT EXISTS plan_events_no_update BEFORE UPDATE ON plan_events
                    BEGIN SELECT RAISE(ABORT, 'Plan history is append only'); END;
                CREATE TRIGGER IF NOT EXISTS plan_events_no_delete BEFORE DELETE ON plan_events
                    BEGIN SELECT RAISE(ABORT, 'Plan history is append only'); END;
                CREATE TRIGGER IF NOT EXISTS cancelled_plans_no_update BEFORE UPDATE ON plans
                    WHEN OLD.status='cancelled' BEGIN SELECT RAISE(ABORT, 'Cancelled plans are immutable'); END;
                CREATE TRIGGER IF NOT EXISTS cancelled_plans_no_delete BEFORE DELETE ON plans
                    WHEN OLD.status='cancelled' BEGIN SELECT RAISE(ABORT, 'Cancelled plans are immutable'); END;
                CREATE TABLE IF NOT EXISTS closed_reports (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, terminal TEXT NOT NULL, start_date TEXT NOT NULL,
                    end_date TEXT NOT NULL, version INTEGER NOT NULL, title TEXT NOT NULL, note TEXT NOT NULL,
                    report_json TEXT NOT NULL, source_facts_json TEXT NOT NULL,
                    digest TEXT NOT NULL, source_digest TEXT NOT NULL, source_fact_count INTEGER NOT NULL,
                    created_by INTEGER NOT NULL REFERENCES users(id), created_at REAL NOT NULL,
                    UNIQUE(terminal, start_date, end_date, version)
                );
                CREATE TRIGGER IF NOT EXISTS closed_reports_no_update BEFORE UPDATE ON closed_reports
                    BEGIN SELECT RAISE(ABORT, 'Closed reports are immutable'); END;
                CREATE TRIGGER IF NOT EXISTS closed_reports_no_delete BEFORE DELETE ON closed_reports
                    BEGIN SELECT RAISE(ABORT, 'Closed reports are immutable'); END;
                CREATE TABLE IF NOT EXISTS issue_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, terminal TEXT NOT NULL, namespace TEXT NOT NULL,
                    source_id TEXT NOT NULL, issue TEXT NOT NULL, status TEXT NOT NULL, note TEXT NOT NULL,
                    actor_id INTEGER NOT NULL REFERENCES users(id), updated_at REAL NOT NULL
                );
                CREATE INDEX IF NOT EXISTS issue_identity ON issue_events(terminal,namespace,source_id,issue,id);
                CREATE TRIGGER IF NOT EXISTS issue_events_no_update BEFORE UPDATE ON issue_events
                    BEGIN SELECT RAISE(ABORT, 'Issue history is append only'); END;
                CREATE TRIGGER IF NOT EXISTS issue_events_no_delete BEFORE DELETE ON issue_events
                    BEGIN SELECT RAISE(ABORT, 'Issue history is append only'); END;
            """)
            # Additive, idempotent migration: never rewrite approved rows.
            # Serialize schema inspection/additions across concurrent workers.
            db.execute('BEGIN IMMEDIATE')
            existing_columns = {row['name'] for row in db.execute('PRAGMA table_info(plans)')}
            for name, declaration in {'revision': 'INTEGER NOT NULL DEFAULT 1', 'updated_by': 'INTEGER REFERENCES users(id)',
                                      'updated_at': 'REAL', 'cancelled_by': 'INTEGER REFERENCES users(id)', 'cancelled_at': 'REAL',
                                      'deleted_by': 'INTEGER REFERENCES users(id)', 'deleted_at': 'REAL'}.items():
                if name not in existing_columns:
                    db.execute(f'ALTER TABLE plans ADD COLUMN {name} {declaration}')
            self._plan_deletion_triggers(db)
        try:
            self.path.chmod(0o600)
        except OSError:
            pass  # Windows access is controlled by the containing directory ACL.

    @staticmethod
    def _plan_deletion_triggers(db):
        """Permit only a tombstone transition for immutable plan versions.

        Run inside the additive migration transaction. All content columns,
        including any future additions, stay immutable during soft deletion.
        """
        mutable = {'deleted_at', 'deleted_by', 'revision', 'updated_at', 'updated_by'}
        columns = [row['name'] for row in db.execute('PRAGMA table_info(plans)') if row['name'] not in mutable]
        unchanged = ' AND '.join('NEW."{0}" IS OLD."{0}"'.format(name.replace('"', '""')) for name in columns)
        tombstone = f"""OLD.deleted_at IS NULL AND NEW.deleted_at IS NOT NULL
            AND NEW.deleted_by IS NOT NULL AND NEW.revision=OLD.revision+1
            AND NEW.updated_by IS NEW.deleted_by AND NEW.updated_at IS NEW.deleted_at
            AND {unchanged}"""
        for status in ('approved', 'cancelled'):
            db.execute(f'DROP TRIGGER IF EXISTS {status}_plans_no_update')
            db.execute(f"""CREATE TRIGGER {status}_plans_no_update BEFORE UPDATE ON plans
                WHEN OLD.status='{status}' AND NOT ({tombstone})
                BEGIN SELECT RAISE(ABORT, 'Approved or cancelled plan content is immutable'); END""")
        db.execute("""CREATE TRIGGER IF NOT EXISTS deleted_plans_no_update BEFORE UPDATE ON plans
            WHEN OLD.deleted_at IS NOT NULL
            BEGIN SELECT RAISE(ABORT, 'Deleted plans are immutable'); END""")
        db.execute("""CREATE TRIGGER IF NOT EXISTS deleted_plans_no_delete BEFORE DELETE ON plans
            WHEN OLD.deleted_at IS NOT NULL
            BEGIN SELECT RAISE(ABORT, 'Deleted plans must retain history'); END""")

    @contextmanager
    def _db(self, write=False):
        db = sqlite3.connect(self.path, timeout=10)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys=ON")
        try:
            if write:
                db.execute("BEGIN IMMEDIATE")
            yield db
            db.commit()
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    @staticmethod
    def _public(row):
        return {"id": row["id"], "username": row["username"], "display_name": row["display_name"],
                "role": row["role"], "terminals": json.loads(row["terminals"]),
                "is_active": bool(row["is_active"]), "must_change_password": bool(row["must_change_password"])}

    def _actor(self, db, actor, *, editor=False, admin=False):
        row = db.execute("SELECT * FROM users WHERE id=?", (actor.get("id"),)).fetchone()
        if row is None or not row["is_active"]:
            raise ControlError(401, "SESSION_INVALID", "Phiên đăng nhập không còn hiệu lực.")
        user = self._public(row)
        if user["must_change_password"]:
            raise ControlError(403, "PASSWORD_CHANGE_REQUIRED", "Vui lòng đổi mật khẩu tạm thời trước khi tiếp tục.")
        if editor:
            require_editor(user)
        if admin:
            require_admin(user)
        return user

    @staticmethod
    def _user_values(username, display_name, role, terminals):
        username = username.strip().lower()
        if not re.fullmatch(r"[a-z0-9][a-z0-9_.-]{2,79}", username):
            raise ControlError(422, "INVALID_USERNAME", "Tên đăng nhập gồm 3–80 chữ thường, số, dấu chấm, gạch ngang hoặc gạch dưới.")
        if role not in ROLES or not isinstance(terminals, (list, tuple)) or not terminals or not set(terminals) <= TERMINALS:
            raise ControlError(422, "INVALID_PERMISSIONS", "Vai trò hoặc phạm vi cảng không hợp lệ.")
        if not isinstance(display_name, str) or not 1 <= len(display_name.strip()) <= 160:
            raise ControlError(422, "INVALID_DISPLAY_NAME", "Tên hiển thị phải có 1–160 ký tự.")
        return username, display_name.strip(), role, sorted(set(terminals))

    def bootstrap_admin(self, username="admin", display_name="Quản trị hệ thống"):
        values = self._user_values(username, display_name, "admin", sorted(TERMINALS))
        temporary = secrets.token_urlsafe(24)
        encoded = hash_password(temporary)
        with self._db(write=True) as db:
            if db.execute("SELECT COUNT(*) FROM users").fetchone()[0]:
                raise ControlError(409, "ALREADY_INITIALIZED", "Đã có tài khoản; không tạo lại quản trị mặc định.")
            user = self._insert_user(db, *values, encoded)
        return {"user": user, "temporary_password": temporary}

    def _insert_user(self, db, username, display_name, role, terminals, encoded):
        now = self.clock()
        try:
            cursor = db.execute("INSERT INTO users(username,display_name,role,terminals,password_hash,created_at,updated_at) VALUES(?,?,?,?,?,?,?)",
                                (username, display_name, role, _json(terminals), encoded, now, now))
        except sqlite3.IntegrityError:
            raise ControlError(409, "USERNAME_EXISTS", "Tên đăng nhập đã tồn tại.") from None
        return self._public(db.execute("SELECT * FROM users WHERE id=?", (cursor.lastrowid,)).fetchone())

    def create_user(self, actor, *, username, display_name, role, terminals, password=None):
        require_admin(actor)
        values = self._user_values(username, display_name, role, terminals)
        temporary = password if password is not None else secrets.token_urlsafe(24)
        encoded = hash_password(temporary)
        with self._db(write=True) as db:
            actor = self._actor(db, actor, admin=True)
            if not set(values[3]) <= set(actor["terminals"]):
                raise ControlError(403, "TERMINAL_FORBIDDEN", "Không được cấp phạm vi cảng ngoài quyền của mình.")
            user = self._insert_user(db, *values, encoded)
        return {"user": user, "temporary_password": temporary}

    def list_users(self, actor):
        with self._db() as db:
            self._actor(db, actor, admin=True)
            return {"items": [self._public(row) for row in db.execute("SELECT * FROM users ORDER BY username")]}

    def update_user(self, actor, user_id, **changes):
        if not changes or not set(changes) <= {"display_name", "role", "terminals", "is_active"}:
            raise ControlError(422, "INVALID_USER_UPDATE", "Thông tin cập nhật không hợp lệ.")
        with self._db(write=True) as db:
            actor = self._actor(db, actor, admin=True)
            old = db.execute("SELECT * FROM users WHERE id=?", (user_id,)).fetchone()
            if old is None:
                raise ControlError(404, "USER_NOT_FOUND", "Không tìm thấy tài khoản.")
            old_user = self._public(old)
            if not set(old_user["terminals"]) <= set(actor["terminals"]):
                raise ControlError(403, "TERMINAL_FORBIDDEN", "Tài khoản nằm ngoài phạm vi quản trị.")
            new = {**old_user, **changes}
            _, name, role, terminals = self._user_values(old["username"], new["display_name"], new["role"], new["terminals"])
            if not set(terminals) <= set(actor["terminals"]):
                raise ControlError(403, "TERMINAL_FORBIDDEN", "Không được cấp phạm vi cảng ngoài quyền của mình.")
            if old["role"] == "admin" and old["is_active"] and (role != "admin" or not new["is_active"]):
                if db.execute("SELECT COUNT(*) FROM users WHERE role='admin' AND is_active=1 AND id<>?", (user_id,)).fetchone()[0] == 0:
                    raise ControlError(409, "LAST_ADMIN", "Phải giữ ít nhất một quản trị viên đang hoạt động.")
            old_admin_scope = set(old_user["terminals"]) if old["role"] == "admin" and old["is_active"] else set()
            new_admin_scope = set(terminals) if role == "admin" and new["is_active"] else set()
            removed_scope = old_admin_scope - new_admin_scope
            if removed_scope:
                remaining_scope = set()
                for other in db.execute("SELECT terminals FROM users WHERE role='admin' AND is_active=1 AND id<>?", (user_id,)):
                    remaining_scope.update(json.loads(other["terminals"]))
                if not removed_scope <= remaining_scope:
                    raise ControlError(409, "LAST_TERMINAL_ADMIN", "Mỗi cảng phải còn ít nhất một quản trị viên đang hoạt động có quyền quản lý cảng đó.")
            db.execute("UPDATE users SET display_name=?,role=?,terminals=?,is_active=?,updated_at=? WHERE id=?",
                       (name, role, _json(terminals), int(bool(new["is_active"])), self.clock(), user_id))
            if any(key in changes for key in ("role", "terminals", "is_active")):
                db.execute("UPDATE sessions SET revoked_at=? WHERE user_id=? AND revoked_at IS NULL", (self.clock(), user_id))
            return self._public(db.execute("SELECT * FROM users WHERE id=?", (user_id,)).fetchone())

    def reset_password(self, actor, user_id):
        require_admin(actor)
        temporary = secrets.token_urlsafe(24)
        encoded = hash_password(temporary)
        with self._db(write=True) as db:
            actor = self._actor(db, actor, admin=True)
            row = db.execute("SELECT * FROM users WHERE id=?", (user_id,)).fetchone()
            if row is None:
                raise ControlError(404, "USER_NOT_FOUND", "Không tìm thấy tài khoản.")
            if not set(json.loads(row["terminals"])) <= set(actor["terminals"]):
                raise ControlError(403, "TERMINAL_FORBIDDEN", "Tài khoản nằm ngoài phạm vi quản trị.")
            db.execute("UPDATE users SET password_hash=?,must_change_password=1,updated_at=? WHERE id=?", (encoded, self.clock(), user_id))
            db.execute("UPDATE sessions SET revoked_at=? WHERE user_id=? AND revoked_at IS NULL", (self.clock(), user_id))
            db.execute("DELETE FROM login_attempts WHERE username=?", (row["username"],))
            user = self._public(db.execute("SELECT * FROM users WHERE id=?", (user_id,)).fetchone())
        return {"user": user, "temporary_password": temporary}

    def _rate_limit(self, db, username, client):
        cutoff = self.clock() - 900
        db.execute("DELETE FROM login_attempts WHERE created_at<?", (cutoff,))
        user_count = db.execute("SELECT COUNT(*) FROM login_attempts WHERE username=?", (username,)).fetchone()[0]
        client_count = db.execute("SELECT COUNT(*) FROM login_attempts WHERE client_key=?", (client,)).fetchone()[0]
        if user_count >= 5 or client_count >= 30:
            raise ControlError(429, "LOGIN_THROTTLED", "Có quá nhiều lần thử. Vui lòng thử lại sau 15 phút.")

    def _session(self, db, row):
        token = secrets.token_urlsafe(32)
        now, expires = self.clock(), self.clock() + self.session_ttl_seconds
        db.execute("DELETE FROM sessions WHERE expires_at<? OR revoked_at IS NOT NULL", (now - 86400,))
        db.execute("INSERT INTO sessions(token_hash,user_id,created_at,expires_at) VALUES(?,?,?,?)", (_digest(token), row["id"], now, expires))
        return {"token": token, "token_type": "bearer", "expires_at": _iso(expires), "user": self._public(row)}

    def login(self, username, password, client_key="local"):
        username = str(username).strip().lower()[:80]
        client = _digest(client_key)
        failure = False
        with self._db(write=True) as db:
            self._rate_limit(db, username, client)
            row = db.execute("SELECT * FROM users WHERE username=?", (username,)).fetchone()
            if row is None and self._dummy_hash is None:
                self._dummy_hash = hash_password(secrets.token_urlsafe(24))
            valid = verify_password(password, row["password_hash"] if row else self._dummy_hash)
            if not row or not row["is_active"] or not valid:
                db.execute("INSERT INTO login_attempts(username,client_key,created_at) VALUES(?,?,?)", (username, client, self.clock()))
                failure = True
            else:
                db.execute("DELETE FROM login_attempts WHERE username=?", (username,))
                result = self._session(db, row)
        if failure:
            raise ControlError(401, "INVALID_CREDENTIALS", "Tên đăng nhập hoặc mật khẩu không đúng.")
        return result

    def authenticate(self, token):
        if not isinstance(token, str) or not re.fullmatch(r"[A-Za-z0-9_-]{32,200}", token):
            raise ControlError(401, "SESSION_INVALID", "Vui lòng đăng nhập.")
        with self._db() as db:
            row = db.execute("SELECT u.* FROM sessions s JOIN users u ON u.id=s.user_id WHERE s.token_hash=? AND s.revoked_at IS NULL AND s.expires_at>? AND u.is_active=1",
                             (_digest(token), self.clock())).fetchone()
        if row is None:
            raise ControlError(401, "SESSION_INVALID", "Phiên đăng nhập đã hết hạn hoặc bị thu hồi.")
        return self._public(row)

    def logout(self, token):
        with self._db(write=True) as db:
            db.execute("UPDATE sessions SET revoked_at=? WHERE token_hash=? AND revoked_at IS NULL", (self.clock(), _digest(token)))

    def change_password(self, token, current_password, new_password):
        actor = self.authenticate(token)
        _password(new_password)
        failure = False
        with self._db(write=True) as db:
            still_valid = db.execute("SELECT 1 FROM sessions s JOIN users u ON u.id=s.user_id WHERE s.token_hash=? AND s.revoked_at IS NULL AND s.expires_at>? AND u.is_active=1",
                                     (_digest(token), self.clock())).fetchone()
            if not still_valid:
                raise ControlError(401, "SESSION_INVALID", "Phiên đăng nhập không còn hiệu lực.")
            key = f"password-change:{actor['id']}"
            self._rate_limit(db, key, key)
            row = db.execute("SELECT * FROM users WHERE id=?", (actor["id"],)).fetchone()
            if not verify_password(current_password, row["password_hash"]):
                db.execute("INSERT INTO login_attempts(username,client_key,created_at) VALUES(?,?,?)", (key, key, self.clock()))
                failure = True
            else:
                if hmac.compare_digest(current_password.encode("utf-8"), new_password.encode("utf-8")):
                    raise ControlError(422, "PASSWORD_UNCHANGED", "Mật khẩu mới phải khác mật khẩu hiện tại.")
                db.execute("UPDATE users SET password_hash=?,must_change_password=0,updated_at=? WHERE id=?", (hash_password(new_password), self.clock(), actor["id"]))
                db.execute("UPDATE sessions SET revoked_at=? WHERE user_id=?", (self.clock(), actor["id"]))
                db.execute("DELETE FROM login_attempts WHERE username IN (?,?)", (key, row["username"]))
                result = self._session(db, db.execute("SELECT * FROM users WHERE id=?", (actor["id"],)).fetchone())
        if failure:
            raise ControlError(401, "INVALID_CREDENTIALS", "Mật khẩu hiện tại không đúng.")
        return result

    @staticmethod
    def validate_plan(value):
        value = dict(value)
        if set(value) - {"terminal", "period_type", *PLAN_PERIOD_FIELDS, "metric", "amount", "reference", "note"}:
            raise ControlError(422, "INVALID_PLAN", "Kế hoạch chứa trường không được hỗ trợ.")
        terminal, period_type = value.get("terminal"), value.get("period_type", "month")
        if terminal not in TERMINALS | {'all'} or period_type not in PLAN_PERIOD_TYPES or value.get("metric") not in {"tonnage", "teu"}:
            raise ControlError(422, "INVALID_PLAN", "Cảng, loại kỳ hoặc chỉ tiêu không hợp lệ.")
        if period_type == 'voyage' and terminal == 'all':
            raise ControlError(422, 'INVALID_PLAN_PERIOD', 'Kế hoạch chuyến phải thuộc một cảng cụ thể.')
        key, _, _ = plan_period(value)
        try:
            amount = Decimal(str(value.get("amount")))
            if not amount.is_finite() or amount < 0 or amount > Decimal("1000000000000"):
                raise InvalidOperation()
            # Decimal precision is about value, not redundant trailing zeroes.
            # Match Pydantic's six-place contract without rounding user input.
            sign, digits, exponent = amount.as_tuple()
            end = len(digits)
            while end > 1 and digits[end - 1] == 0:
                end -= 1
            amount = Decimal((sign, digits[:end], exponent + len(digits) - end)) if amount else Decimal(0)
            if amount.as_tuple().exponent < -6:
                raise InvalidOperation()
        except (InvalidOperation, ValueError):
            raise ControlError(422, "INVALID_PLAN_AMOUNT", "Chỉ tiêu phải không âm, tối đa 10¹² và 6 chữ số thập phân.") from None
        reference, note = value.get("reference", ""), value.get("note", "")
        if not isinstance(reference, str) or len(reference) > 500 or not isinstance(note, str) or len(note) > 4000:
            raise ControlError(422, "INVALID_PLAN_REFERENCE", "Tham chiếu hoặc ghi chú quá dài.")
        return {"terminal": terminal, "period_type": period_type, "period_key": key, "metric": value["metric"],
                "amount": format(amount, 'f'), "reference": reference.strip(), "note": note.strip()}

    def _plan(self, db, row):
        highest = db.execute("SELECT MAX(version) FROM plans WHERE terminal=? AND period_type=? AND period_key=? AND metric=? AND status='approved'",
                             (row["terminal"], row["period_type"], row["period_key"], row["metric"])).fetchone()[0]
        return self._plan_view(row, highest)

    @staticmethod
    def _plan_view(row, highest):
        return {"id": row["id"], "terminal": row["terminal"], "period_type": row["period_type"],
                **saved_plan_period(row['period_type'], row['period_key']),
                "metric": row["metric"], "amount": float(Decimal(row["amount"])), "amount_decimal": row["amount"],
                "reference": row["reference"], "note": row["note"], "version": row["version"], "status": row["status"],
                "created_by": row["created_by"], "created_at": _iso(row["created_at"]),
                "approved_by": row["approved_by"], "approved_at": _iso(row["approved_at"]) if row["approved_at"] is not None else None,
                "revision": row["revision"], "updated_by": row["updated_by"] or row["created_by"],
                "updated_at": _iso(row["updated_at"] if row["updated_at"] is not None else row["created_at"]),
                "cancelled_by": row["cancelled_by"], "cancelled_at": _iso(row["cancelled_at"]) if row["cancelled_at"] is not None else None,
                "is_deleted": row["deleted_at"] is not None, "deleted_by": row["deleted_by"],
                "deleted_at": _iso(row["deleted_at"]) if row["deleted_at"] is not None else None,
                "is_current": row["deleted_at"] is None and row["status"] == "approved" and row["version"] == highest}

    def _plan_event(self, db, actor, row, action, note=""):
        db.execute('INSERT INTO plan_events(plan_id,action,snapshot_json,note,actor_id,created_at) VALUES(?,?,?,?,?,?)',
                   (row['id'], action, _json(self._plan(db, row)), note, actor['id'], self.clock()))

    def get_plan(self, actor, plan_id):
        with self._db() as db:
            actor = self._actor(db, actor)
            row = db.execute('SELECT * FROM plans WHERE id=?', (plan_id,)).fetchone()
            if row is None:
                raise ControlError(404, 'PLAN_NOT_FOUND', 'Không tìm thấy kế hoạch.')
            require_scope(actor, row['terminal'])
            events = db.execute('SELECT * FROM plan_events WHERE plan_id=? ORDER BY id', (plan_id,)).fetchall()
            return {**self._plan(db, row), 'history': [
                {'id': event['id'], 'action': event['action'], 'snapshot': json.loads(event['snapshot_json']),
                 'note': event['note'], 'actor_id': event['actor_id'], 'created_at': _iso(event['created_at'])}
                for event in events]}

    @staticmethod
    def _draft(row, expected_revision=None):
        if row is None:
            raise ControlError(404, 'PLAN_NOT_FOUND', 'Không tìm thấy kế hoạch.')
        if row['deleted_at'] is not None:
            raise ControlError(409, 'PLAN_DELETED', 'Kế hoạch đã được xóa. Lịch sử vẫn được lưu để tra cứu.')
        if row['status'] != 'draft':
            raise ControlError(409, 'PLAN_IMMUTABLE', 'Chỉ được sửa, hủy hoặc duyệt bản nháp. Hãy tạo phiên bản mới khi cần điều chỉnh bản đã duyệt.')
        if expected_revision is not None and row['revision'] != expected_revision:
            raise ControlError(409, 'PLAN_CONFLICT', 'Bản nháp đã thay đổi. Hãy tải lại để kiểm tra trước khi tiếp tục.')

    def update_plan(self, actor, plan_id, expected_revision, **changes):
        if isinstance(expected_revision, bool) or not isinstance(expected_revision, int) or expected_revision < 1:
            raise ControlError(422, 'INVALID_PLAN_REVISION', 'Cần phiên bản chỉnh sửa của bản nháp.')
        with self._db(write=True) as db:
            actor = self._actor(db, actor, editor=True)
            row = db.execute('SELECT * FROM plans WHERE id=?', (plan_id,)).fetchone()
            if row is not None:
                require_scope(actor, row['terminal'])
            self._draft(row, expected_revision)
            current = self._plan(db, row)
            fields = ('terminal', 'period_type', *PLAN_PERIOD_FIELDS, 'metric', 'amount', 'reference', 'note')
            value = self.validate_plan({**{key: current[key] for key in fields}, 'amount': row['amount'], **changes})
            require_scope(actor, value['terminal'])
            identity = ('terminal', 'period_type', 'period_key', 'metric')
            version = row['version']
            if any(value[key] != row[key] for key in identity):
                version = db.execute('SELECT COALESCE(MAX(version),0)+1 FROM plans WHERE terminal=? AND period_type=? AND period_key=? AND metric=?',
                                     tuple(value[key] for key in identity)).fetchone()[0]
            if not db.execute('SELECT 1 FROM plan_events WHERE plan_id=? LIMIT 1', (plan_id,)).fetchone():
                self._plan_event(db, actor, row, 'legacy_baseline')
            db.execute('UPDATE plans SET terminal=?,period_type=?,period_key=?,metric=?,amount=?,reference=?,note=?,version=?,revision=revision+1,updated_by=?,updated_at=? WHERE id=?',
                       (*[value[key] for key in (*identity, 'amount', 'reference', 'note')], version, actor['id'], self.clock(), plan_id))
            updated = db.execute('SELECT * FROM plans WHERE id=?', (plan_id,)).fetchone()
            self._plan_event(db, actor, updated, 'updated')
            return self._plan(db, updated)

    def cancel_plan(self, actor, plan_id, expected_revision, note):
        if isinstance(expected_revision, bool) or not isinstance(expected_revision, int) or expected_revision < 1:
            raise ControlError(422, 'INVALID_PLAN_REVISION', 'Cần phiên bản chỉnh sửa của bản nháp.')
        if not isinstance(note, str) or not 1 <= len(note.strip()) <= 4000:
            raise ControlError(422, 'PLAN_CANCEL_REASON_REQUIRED', 'Cần ghi lý do hủy bản nháp.')
        with self._db(write=True) as db:
            actor = self._actor(db, actor, editor=True)
            row = db.execute('SELECT * FROM plans WHERE id=?', (plan_id,)).fetchone()
            if row is not None:
                require_scope(actor, row['terminal'])
            self._draft(row, expected_revision)
            if not db.execute('SELECT 1 FROM plan_events WHERE plan_id=? LIMIT 1', (plan_id,)).fetchone():
                self._plan_event(db, actor, row, 'legacy_baseline')
            timestamp = self.clock()
            db.execute("UPDATE plans SET status='cancelled',revision=revision+1,updated_by=?,updated_at=?,cancelled_by=?,cancelled_at=? WHERE id=?",
                       (actor['id'], timestamp, actor['id'], timestamp, plan_id))
            cancelled = db.execute('SELECT * FROM plans WHERE id=?', (plan_id,)).fetchone()
            self._plan_event(db, actor, cancelled, 'cancelled', note.strip())
            return self._plan(db, cancelled)

    def create_plans(self, actor, rows):
        if not isinstance(rows, list) or not 1 <= len(rows) <= 500:
            raise ControlError(422, "INVALID_PLAN_BATCH", "Mỗi lần nhập từ 1 đến 500 chỉ tiêu.")
        validated = [self.validate_plan(row) for row in rows]
        result = []
        with self._db(write=True) as db:
            actor = self._actor(db, actor, editor=True)
            for value in validated:
                require_scope(actor, value["terminal"])
            for value in validated:
                version = db.execute("SELECT COALESCE(MAX(version),0)+1 FROM plans WHERE terminal=? AND period_type=? AND period_key=? AND metric=?",
                                     (value["terminal"], value["period_type"], value["period_key"], value["metric"])).fetchone()[0]
                cursor = db.execute("INSERT INTO plans(terminal,period_type,period_key,metric,amount,reference,note,version,created_by,created_at) VALUES(?,?,?,?,?,?,?,?,?,?)",
                                    (*[value[key] for key in ("terminal", "period_type", "period_key", "metric", "amount", "reference", "note")], version, actor["id"], self.clock()))
                created = db.execute("SELECT * FROM plans WHERE id=?", (cursor.lastrowid,)).fetchone()
                self._plan_event(db, actor, created, 'created')
                result.append(self._plan(db, created))
        return result

    def delete_plan(self, actor, plan_id, revision):
        if isinstance(revision, bool) or not isinstance(revision, int) or revision < 1:
            raise ControlError(422, 'INVALID_PLAN_REVISION', 'Cần phiên bản hiện tại của kế hoạch để xóa.')
        with self._db(write=True) as db:
            actor = self._actor(db, actor, editor=True)
            row = db.execute('SELECT * FROM plans WHERE id=?', (plan_id,)).fetchone()
            if row is None:
                raise ControlError(404, 'PLAN_NOT_FOUND', 'Không tìm thấy kế hoạch.')
            require_scope(actor, row['terminal'])
            if row['revision'] != revision:
                raise ControlError(409, 'PLAN_CONFLICT', 'Kế hoạch đã thay đổi. Hãy tải lại để kiểm tra trước khi xóa.')
            if row['deleted_at'] is not None:
                raise ControlError(409, 'PLAN_DELETED', 'Kế hoạch đã được xóa. Lịch sử vẫn được lưu để tra cứu.')
            if not db.execute('SELECT 1 FROM plan_events WHERE plan_id=? LIMIT 1', (plan_id,)).fetchone():
                self._plan_event(db, actor, row, 'legacy_baseline')
            timestamp = self.clock()
            changed = db.execute('''UPDATE plans SET deleted_at=?,deleted_by=?,revision=revision+1,
                updated_at=?,updated_by=? WHERE id=? AND revision=? AND deleted_at IS NULL''',
                (timestamp, actor['id'], timestamp, actor['id'], plan_id, revision))
            if changed.rowcount != 1:
                raise ControlError(409, 'PLAN_CONFLICT', 'Kế hoạch đã thay đổi. Hãy tải lại để kiểm tra trước khi xóa.')
            deleted = db.execute('SELECT * FROM plans WHERE id=?', (plan_id,)).fetchone()
            self._plan_event(db, actor, deleted, 'deleted')
            return self._plan(db, deleted)

    def create_plan(self, actor, **value):
        return self.create_plans(actor, [value])[0]

    def list_plans(self, actor, terminal=None, month=None, voyage_id=None, status=None, page=1, page_size=50, *, period_type=None,
                   week=None, quarter=None, year=None, start_date=None, end_date=None, include_deleted=False):
        self._pagination(page, page_size)
        if not isinstance(include_deleted, bool):
            raise ControlError(422, 'INVALID_PLAN_FILTER', 'Bộ lọc kế hoạch đã xóa không hợp lệ.')
        with self._db() as db:
            actor = self._actor(db, actor)
            terms, params = self._scope_filter(actor, terminal)
            if not include_deleted:
                terms += ' AND deleted_at IS NULL'
            if period_type is not None:
                if period_type not in PLAN_PERIOD_TYPES:
                    raise ControlError(422, "INVALID_PLAN_PERIOD", "Loại kỳ kế hoạch không hợp lệ.")
                terms += " AND period_type=?"
                params.append(period_type)
            if month is not None:
                terms += " AND period_type='month' AND period_key=?"
                params.append(month)
            if voyage_id is not None:
                terms += " AND period_type='voyage' AND period_key=?"
                params.append(str(voyage_id))
            for kind, value in [('week', week), ('quarter', quarter), ('year', year)]:
                if value is not None:
                    key, _, _ = plan_period({'period_type': kind, kind: value})
                    terms += ' AND period_type=? AND period_key=?'
                    params.extend((kind, key))
            if start_date is not None or end_date is not None:
                key, _, _ = plan_period({'period_type': 'custom', 'start_date': start_date, 'end_date': end_date})
                terms += " AND period_type='custom' AND period_key=?"
                params.append(key)
            if status is not None:
                if status not in {"draft", "approved", "cancelled"}:
                    raise ControlError(422, "INVALID_PLAN_STATUS", "Trạng thái kế hoạch không hợp lệ.")
                terms += " AND status=?"
                params.append(status)
            total = db.execute(f"SELECT COUNT(*) FROM plans WHERE {terms}", params).fetchone()[0]
            rows = db.execute(f"SELECT * FROM plans WHERE {terms} ORDER BY created_at DESC,id DESC LIMIT ? OFFSET ?", (*params, page_size, (page - 1) * page_size)).fetchall()
            return {"items": [self._plan(db, row) for row in rows], "total": total, "page": page, "page_size": page_size}

    def effective_plans(self, actor, terminal, month=None, voyage_id=None):
        with self._db() as db:
            actor = self._actor(db, actor)
            terms, params = self._scope_filter(actor, terminal)
            terms += " AND status='approved' AND selected.deleted_at IS NULL"
            if month is not None:
                terms += " AND period_type='month' AND period_key=?"
                params.append(month)
            if voyage_id is not None:
                terms += " AND period_type='voyage' AND period_key=?"
                params.append(str(voyage_id))
            # Choose the effective revision in one SQLite statement. A later
            # approval must not make previously fetched rows all disappear.
            # Newer tombstones still supersede older approved versions.
            rows = db.execute(f"""SELECT selected.* FROM plans selected WHERE {terms}
                AND NOT EXISTS (
                    SELECT 1 FROM plans newer WHERE newer.terminal=selected.terminal
                    AND newer.period_type=selected.period_type AND newer.period_key=selected.period_key
                    AND newer.metric=selected.metric AND newer.status='approved'
                    AND newer.version>selected.version
                ) ORDER BY terminal,period_type,period_key,metric""", params).fetchall()
            return [self._plan_view(row, row["version"]) for row in rows]

    def approve_plan(self, actor, plan_id, expected_revision=None):
        with self._db(write=True) as db:
            actor = self._actor(db, actor, editor=True)
            row = db.execute("SELECT * FROM plans WHERE id=?", (plan_id,)).fetchone()
            if row is None:
                raise ControlError(404, "PLAN_NOT_FOUND", "Không tìm thấy kế hoạch.")
            require_scope(actor, row["terminal"])
            self._draft(row, expected_revision)
            if not row["reference"]:
                raise ControlError(422, "PLAN_REFERENCE_REQUIRED", "Cần ghi rõ tệp hoặc văn bản làm căn cứ trước khi duyệt.")
            newest = db.execute("SELECT MAX(version) FROM plans WHERE terminal=? AND period_type=? AND period_key=? AND metric=? AND status='approved'",
                                (row["terminal"], row["period_type"], row["period_key"], row["metric"])).fetchone()[0]
            if newest is not None and row["version"] <= newest:
                raise ControlError(409, "PLAN_SUPERSEDED", "Đã có phiên bản mới hơn được duyệt.")
            if not db.execute('SELECT 1 FROM plan_events WHERE plan_id=? LIMIT 1', (plan_id,)).fetchone():
                self._plan_event(db, actor, row, 'legacy_baseline')
            timestamp = self.clock()
            db.execute("UPDATE plans SET status='approved',approved_by=?,approved_at=?,revision=revision+1,updated_by=?,updated_at=? WHERE id=?",
                       (actor["id"], timestamp, actor['id'], timestamp, plan_id))
            approved = db.execute("SELECT * FROM plans WHERE id=?", (plan_id,)).fetchone()
            self._plan_event(db, actor, approved, 'approved')
            return self._plan(db, approved)

    def _throughput_progress(self, db, actor, report):
        filters = report.get('meta', {}).get('filters', {})
        terminal, start, end = filters.get('terminal'), filters.get('start_date'), filters.get('end_date')
        if terminal is not None:
            require_scope(actor, terminal)
        scope = production_scope_context(report)
        eligible = scope['production_scope'] == 'nghe_tinh' and not scope['legacy_scope']
        result = {'report_id': report.get('meta', {}).get('report_id'), 'production_scope': scope['production_scope'],
                  'berth_rule_version': scope['berth_rule_version'], 'period': dict(filters), 'eligible': eligible,
                  'reason': None, 'items': [], 'available_periods': []}
        if not eligible:
            result['reason'] = 'Chỉ đối chiếu kế hoạch Cảng Nghệ Tĩnh với dữ liệu đã phân loại theo quy tắc cầu cập đầu tiên.'
            return result
        terms, params = self._scope_filter(actor, terminal)
        rows = db.execute(f"""SELECT selected.* FROM plans selected WHERE {terms}
            AND metric='tonnage' AND period_type<>'voyage' AND status='approved'
            AND NOT EXISTS (SELECT 1 FROM plans newer WHERE newer.terminal=selected.terminal
                AND newer.period_type=selected.period_type AND newer.period_key=selected.period_key
                AND newer.metric=selected.metric AND newer.status='approved' AND newer.version>selected.version)
            ORDER BY period_type,period_key,terminal""", params).fetchall()
        groups = {}
        for row in rows:
            plan = self._plan_view(row, row['version'])
            groups.setdefault((plan['period_type'], plan['period_key']), {})[plan['terminal']] = plan
        overview = report['overview']
        for _, by_terminal in sorted(groups.items()):
            if terminal != 'all':
                if by_terminal[terminal]['is_deleted']:
                    continue
                plans, source, complete = [by_terminal[terminal]], 'terminal', True
            elif 'all' in by_terminal:
                # Removing the company target must not silently substitute
                # terminal targets. A newer company approval can replace it.
                if by_terminal['all']['is_deleted']:
                    continue
                plans, source, complete = [by_terminal['all']], 'company', True
            else:
                by_terminal = {key: value for key, value in by_terminal.items() if not value['is_deleted']}
                if not by_terminal:
                    continue
                plans, source = [by_terminal[t] for t in sorted(TERMINALS) if t in by_terminal], 'terminals'
                complete = set(by_terminal) == TERMINALS
            period = plans[0]
            target = sum((Decimal(row['amount_decimal']) for row in plans), Decimal(0)) if complete else None
            # Navigation metadata only. Never attach this report's actuals to
            # a target with a different start or an already-ended target period.
            result['available_periods'].append({
                'key': f"{period['period_type']}:{period['period_key']}", 'period_type': period['period_type'],
                'terminal': terminal,
                'period_key': period['period_key'], 'start_date': period['period_start'], 'end_date': period['period_end'],
                'target': float(target) if target is not None else None, 'target_source': source, 'plans': plans,
                'status': 'ready' if complete else 'missing_plan',
                'reason': None if complete else 'Chưa đủ kế hoạch đã duyệt của cả hai cảng cho cùng kỳ.'})
            if period['period_start'] == start and end <= period['period_end']:
                result['items'].append(throughput_progress_item(period, plans, overview.get('total_tonnage'),
                                      overview.get('tonnage_status', 'unavailable'), source, complete_target=complete))
        if not result['items']:
            result['reason'] = 'Chưa có kế hoạch tấn thông qua đã duyệt bắt đầu đúng ngày đầu kỳ và bao phủ kỳ báo cáo.'
        return result

    def throughput_progress(self, actor, report):
        with self._db() as db:
            actor = self._actor(db, actor)
            return self._throughput_progress(db, actor, report)

    @staticmethod
    def _pagination(page, page_size):
        if isinstance(page, bool) or isinstance(page_size, bool) or not isinstance(page, int) or not isinstance(page_size, int) or page < 1 or not 1 <= page_size <= 100:
            raise ControlError(422, "INVALID_PAGINATION", "Phân trang không hợp lệ.")

    @staticmethod
    def _scope_filter(actor, terminal):
        if terminal not in (None, "all"):
            require_scope(actor, terminal)
            return "terminal=?", [terminal]
        allowed = sorted(set(actor["terminals"]) & TERMINALS)
        if not allowed:
            raise ControlError(403, "TERMINAL_FORBIDDEN", "Không có phạm vi cảng được cấp.")
        # Closed reports for 'all' are readable only with permission for both sources.
        if set(allowed) == TERMINALS:
            allowed.append("all")
        return f"terminal IN ({','.join('?' for _ in allowed)})", allowed

    @staticmethod
    def _closed_header(row, report=None):
        return {key: row[key] for key in ("id", "terminal", "start_date", "end_date", "version", "title", "note", "digest", "source_digest", "source_fact_count", "created_by")} | {
            "created_at": _iso(row["created_at"]), **production_scope_context(report if report is not None else json.loads(row['report_json']))}

    def close_report(self, actor, terminal, start_date, end_date, report, source_facts, title="", note="", *, planning_actuals=None):
        require_scope(actor, terminal)
        try:
            start, end = date.fromisoformat(str(start_date)), date.fromisoformat(str(end_date))
        except ValueError:
            raise ControlError(422, "INVALID_REPORT_PERIOD", "Ngày báo cáo không hợp lệ.") from None
        if end < start or (end - start).days >= 366:
            raise ControlError(422, "INVALID_REPORT_PERIOD", "Kỳ báo cáo tối đa 366 ngày, ngày cuối không trước ngày đầu.")
        if not isinstance(report, dict) or not isinstance(source_facts, list) or len(title) > 200 or len(note) > 4000:
            raise ControlError(422, "INVALID_REPORT", "Báo cáo hoặc ghi chú không hợp lệ.")
        # Root integration supplies this server-side, from the exact same report fact set.
        report_json, facts_json = _json(report), _json(source_facts)
        if len(report_json.encode("utf-8")) + len(facts_json.encode("utf-8")) > 64 * 1024 * 1024:
            raise ControlError(413, "REPORT_TOO_LARGE", "Báo cáo vượt giới hạn lưu trữ; hãy thu hẹp kỳ.")
        with self._db(write=True) as db:
            actor = self._actor(db, actor, editor=True)
            require_scope(actor, terminal)
            if planning_actuals is not None:
                eligible = start.day == 1 and start.strftime('%Y-%m') == end.strftime('%Y-%m')
                month = start.strftime('%Y-%m')
                terminals = sorted(TERMINALS) if terminal == 'all' else [terminal]
                if not isinstance(planning_actuals, dict) or (eligible and set(planning_actuals) != set(terminals)):
                    raise ControlError(422, 'INVALID_PLAN_ACTUALS', 'Thiếu sản lượng cùng kỳ để lưu đối chiếu kế hoạch.')
                approved = []
                if eligible:
                    # Approval and closure both hold BEGIN IMMEDIATE. Select all
                    # effective plans in this transaction so approval cannot
                    # interleave between capturing the plan and saving closure.
                    for source in terminals:
                        rows = db.execute("""SELECT selected.* FROM plans selected
                            WHERE terminal=? AND period_type='month' AND period_key=? AND status='approved'
                            AND selected.deleted_at IS NULL
                            AND NOT EXISTS (SELECT 1 FROM plans newer WHERE newer.terminal=selected.terminal
                                AND newer.period_type=selected.period_type AND newer.period_key=selected.period_key
                                AND newer.metric=selected.metric AND newer.status='approved' AND newer.version>selected.version)
                            ORDER BY metric""", (source, month)).fetchall()
                        approved.extend(self._plan_view(row, row['version']) for row in rows)
                planning = {'captured': True, 'captured_at': _iso(self.clock()), 'eligible': eligible,
                            'reason': None if eligible else 'Kỳ chốt không bắt đầu từ đầu tháng hoặc trải qua nhiều tháng; không đối chiếu kế hoạch tháng.',
                            'period': {'terminal': terminal, 'start_date': start.isoformat(), 'end_date': end.isoformat(), 'month': month},
                            'plans': approved, 'rows': plan_progress_rows(approved, planning_actuals) if eligible else []}
                # The approved period targets share the closure transaction;
                # a concurrent approval cannot change the captured versions.
                throughput = {**self._throughput_progress(db, actor, report), 'captured': True, 'captured_at': _iso(self.clock())}
                report_json = _json({**report, 'planning': planning, 'throughput_progress': throughput})
                if len(report_json.encode('utf-8')) + len(facts_json.encode('utf-8')) > 64 * 1024 * 1024:
                    raise ControlError(413, 'REPORT_TOO_LARGE', 'Báo cáo vượt giới hạn lưu trữ; hãy thu hẹp kỳ.')
            version = db.execute("SELECT COALESCE(MAX(version),0)+1 FROM closed_reports WHERE terminal=? AND start_date=? AND end_date=?", (terminal, start.isoformat(), end.isoformat())).fetchone()[0]
            cursor = db.execute("INSERT INTO closed_reports(terminal,start_date,end_date,version,title,note,report_json,source_facts_json,digest,source_digest,source_fact_count,created_by,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
                                (terminal, start.isoformat(), end.isoformat(), version, title.strip(), note.strip(), report_json, facts_json, _digest(report_json), _digest(_facts_json(source_facts)), len(source_facts), actor["id"], self.clock()))
            return self._closed_header(db.execute("SELECT * FROM closed_reports WHERE id=?", (cursor.lastrowid,)).fetchone())

    def list_closed_reports(self, actor, terminal=None, page=1, page_size=50):
        self._pagination(page, page_size)
        with self._db() as db:
            actor = self._actor(db, actor)
            terms, params = self._scope_filter(actor, terminal)
            total = db.execute(f"SELECT COUNT(*) FROM closed_reports WHERE {terms}", params).fetchone()[0]
            rows = db.execute(f"SELECT * FROM closed_reports WHERE {terms} ORDER BY id DESC LIMIT ? OFFSET ?", (*params, page_size, (page - 1) * page_size)).fetchall()
            return {"items": [self._closed_header(row) for row in rows], "total": total, "page": page, "page_size": page_size}

    def get_closed_report(self, actor, report_id):
        with self._db() as db:
            actor = self._actor(db, actor)
            row = db.execute("SELECT * FROM closed_reports WHERE id=?", (report_id,)).fetchone()
            if row is None:
                raise ControlError(404, "REPORT_NOT_FOUND", "Không tìm thấy báo cáo đã chốt.")
            require_scope(actor, row["terminal"])
            report = json.loads(row['report_json'])
            planning = report.get('planning') or {'captured': False, 'eligible': False, 'rows': [], 'plans': [],
                'reason': 'Bản chốt này chưa lưu kế hoạch tại thời điểm chốt. Không sử dụng kế hoạch hiện tại để thay thế.'}
            return {**self._closed_header(row, report), "report": report, "source_facts": json.loads(row["source_facts_json"]), 'planning': planning}

    def compare_closed_report(self, actor, report_id, current_report, current_source_facts):
        old = self.get_closed_report(actor, report_id)
        changes = []
        for metric in ("total_tonnage", "total_teu", "vessel_calls", "record_count",
                       "trend_tonnage", "trend_teu", "trend_vessels"):
            previous = old["report"].get("overview", {}).get(metric)
            current = current_report.get("overview", {}).get(metric)
            if previous != current:
                changes.append({"metric": metric, "closed": previous, "current": current,
                                "delta": float(Decimal(str(current)) - Decimal(str(previous))) if current is not None and previous is not None else None})
        current_digest = _digest(_facts_json(current_source_facts))
        return {"report_id": old["id"], "version": old["version"], "changed": bool(changes) or old["source_digest"] != current_digest,
                "source_changed": old["source_digest"] != current_digest, "changes": changes,
                "closed_source_digest": old["source_digest"], "current_source_digest": current_digest,
                "compared_at": _iso(self.clock())}

    def set_issue(self, actor, *, terminal, namespace, source_id, issue, status, note=""):
        if terminal not in TERMINALS or not re.fullmatch(r"[a-z][a-z0-9_.-]{0,79}", namespace) or not re.fullmatch(r"[A-Za-z0-9_.:-]{1,120}", str(source_id)):
            raise ControlError(422, "INVALID_ISSUE_KEY", "Định danh đối soát không hợp lệ.")
        if not isinstance(issue, str) or not 1 <= len(issue) <= 100 or status not in {"open", "resolved", "ignored"} or not isinstance(note, str) or not 1 <= len(note.strip()) <= 4000:
            raise ControlError(422, "INVALID_ISSUE", "Cần loại vấn đề, trạng thái hợp lệ và ghi chú xử lý.")
        with self._db(write=True) as db:
            actor = self._actor(db, actor, editor=True)
            require_scope(actor, terminal)
            cursor = db.execute("INSERT INTO issue_events(terminal,namespace,source_id,issue,status,note,actor_id,updated_at) VALUES(?,?,?,?,?,?,?,?)",
                                (terminal, namespace, str(source_id), issue, status, note.strip(), actor["id"], self.clock()))
            return self._issue(db.execute("SELECT * FROM issue_events WHERE id=?", (cursor.lastrowid,)).fetchone())

    @staticmethod
    def _issue(row):
        return {key: row[key] for key in ("id", "terminal", "namespace", "source_id", "issue", "status", "note", "actor_id")} | {"updated_at": _iso(row["updated_at"])}

    def list_issues(self, actor, terminal=None, namespace=None, source_id=None, *, history=False, page=1, page_size=50):
        self._pagination(page, page_size)
        with self._db() as db:
            actor = self._actor(db, actor)
            terms, params = self._scope_filter(actor, terminal)
            if namespace is not None:
                terms += " AND namespace=?"
                params.append(namespace)
            if source_id is not None:
                terms += " AND source_id=?"
                params.append(str(source_id))
            if not history:
                terms += " AND id IN (SELECT MAX(id) FROM issue_events GROUP BY terminal,namespace,source_id,issue)"
            total = db.execute(f"SELECT COUNT(*) FROM issue_events WHERE {terms}", params).fetchone()[0]
            rows = db.execute(f"SELECT * FROM issue_events WHERE {terms} ORDER BY id DESC LIMIT ? OFFSET ?", (*params, page_size, (page - 1) * page_size)).fetchall()
            return {"items": [self._issue(row) for row in rows], "total": total, "page": page, "page_size": page_size}

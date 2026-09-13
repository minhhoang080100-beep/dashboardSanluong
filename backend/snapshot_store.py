"""Bounded, short-lived report storage; never connects to the source databases."""
from contextlib import closing, contextmanager
from datetime import date, datetime
from decimal import Decimal, DecimalException
import json
from pathlib import Path
import sqlite3
from threading import Lock
import time
import zlib


class SnapshotStorageError(RuntimeError):
    pass


class SnapshotStorageCapacity(SnapshotStorageError):
    pass


def _encode(value):
    if isinstance(value, datetime):
        return {"__snapshot_type__": "datetime", "value": value.isoformat()}
    if isinstance(value, date):
        return {"__snapshot_type__": "date", "value": value.isoformat()}
    if isinstance(value, Decimal):
        return {"__snapshot_type__": "decimal", "value": str(value)}
    raise TypeError("Unsupported report value")


def _decode(value):
    if set(value) == {"__snapshot_type__", "value"}:
        converter = {"datetime": datetime.fromisoformat, "date": date.fromisoformat, "decimal": Decimal}.get(value["__snapshot_type__"])
        if converter:
            return converter(value["value"])
    return value


class SnapshotStore:
    def __init__(self, path, *, max_snapshots=128, max_rows=1_000_000,
                 max_bytes=128 * 1024 * 1024, max_decoded_bytes=128 * 1024 * 1024,
                 clock=time.time):
        if any(isinstance(value, bool) or not isinstance(value, int) or value < 1
               for value in (max_snapshots, max_rows, max_bytes, max_decoded_bytes)):
            raise ValueError("Snapshot limits must be positive integers.")
        self.path = Path(path)
        self.max_snapshots, self.max_rows = max_snapshots, max_rows
        self.max_bytes, self.max_decoded_bytes = max_bytes, max_decoded_bytes
        self.clock = clock
        self._ready = False
        self._init_lock = Lock()

    @contextmanager
    def _db(self):
        db = None
        try:
            with self._init_lock:
                if not self._ready:
                    if self.path.is_symlink():
                        raise SnapshotStorageError("Snapshot path cannot be a symbolic link.")
                    self.path.parent.mkdir(parents=True, exist_ok=True)
                    with closing(sqlite3.connect(self.path, timeout=5)) as setup, setup:
                        setup.execute("PRAGMA journal_mode=WAL")
                        setup.execute("""CREATE TABLE IF NOT EXISTS report_snapshots (
                            report_id TEXT PRIMARY KEY, cache_key TEXT NOT NULL,
                            created REAL NOT NULL, expires REAL NOT NULL, fresh_until REAL NOT NULL,
                            row_count INTEGER NOT NULL, payload BLOB NOT NULL)""")
                        setup.execute("CREATE INDEX IF NOT EXISTS report_snapshots_cache ON report_snapshots(cache_key, created DESC)")
                    self._ready = True
            db = sqlite3.connect(self.path, timeout=5)
            db.row_factory = sqlite3.Row
            yield db
        except (sqlite3.Error, OSError) as exc:
            raise SnapshotStorageError("Report snapshot storage is unavailable.") from exc
        finally:
            if db is not None:
                db.close()

    def put(self, report_id, cache_key, report, rows, *, ttl, fresh_ttl):
        content = json.dumps({"format": 1, "report": report, "rows": rows}, default=_encode,
                             ensure_ascii=False, allow_nan=False, separators=(",", ":")).encode("utf-8")
        if len(content) > self.max_decoded_bytes or len(rows) > self.max_rows:
            raise SnapshotStorageCapacity("Report snapshot is too large.")
        compressed = zlib.compress(content, level=3)
        if len(compressed) > self.max_bytes:
            raise SnapshotStorageCapacity("Report snapshot is too large.")
        now = self.clock()
        with self._db() as db:
            with db:
                db.execute("BEGIN IMMEDIATE")
                db.execute("DELETE FROM report_snapshots WHERE expires<=?", (now,))
                db.execute("INSERT INTO report_snapshots VALUES(?,?,?,?,?,?,?)",
                           (report_id, cache_key, now, now + ttl, now + min(fresh_ttl, ttl), len(rows), compressed))
                while True:
                    count, count_rows, count_bytes = db.execute("SELECT COUNT(*),COALESCE(SUM(row_count),0),COALESCE(SUM(LENGTH(payload)),0) FROM report_snapshots").fetchone()
                    if count <= self.max_snapshots and count_rows <= self.max_rows and count_bytes <= self.max_bytes:
                        break
                    db.execute("DELETE FROM report_snapshots WHERE report_id=(SELECT report_id FROM report_snapshots WHERE report_id<>? ORDER BY created,report_id LIMIT 1)", (report_id,))

    def _read(self, row):
        if row is None:
            return None
        try:
            decoder = zlib.decompressobj()
            content = decoder.decompress(row["payload"], self.max_decoded_bytes + 1)
            if len(content) > self.max_decoded_bytes or not decoder.eof or decoder.unused_data:
                raise ValueError("Invalid snapshot size")
            data = json.loads(content, object_hook=_decode)
            if not isinstance(data, dict) or data.get("format") != 1 or not isinstance(data.get("report"), dict) or not isinstance(data.get("rows"), list):
                raise ValueError("Invalid snapshot format")
            if not isinstance(data["report"].get("meta"), dict) or not all(isinstance(item, dict) for item in data["rows"]):
                raise ValueError("Invalid snapshot structure")
            if len(data["rows"]) != row["row_count"] or data["report"]["meta"].get("report_id") != row["report_id"]:
                raise ValueError("Invalid snapshot identity")
            return {**data, "created": row["created"], "expires": row["expires"], "fresh_until": row["fresh_until"]}
        except (ValueError, TypeError, KeyError, DecimalException, zlib.error) as exc:
            raise SnapshotStorageError("Stored report snapshot is invalid.") from exc

    def get(self, report_id):
        with self._db() as db:
            return self._read(db.execute("SELECT * FROM report_snapshots WHERE report_id=? AND expires>?", (report_id, self.clock())).fetchone())

    def get_fresh(self, cache_key):
        now = self.clock()
        with self._db() as db:
            return self._read(db.execute("SELECT * FROM report_snapshots WHERE cache_key=? AND fresh_until>? AND expires>? ORDER BY created DESC,rowid DESC LIMIT 1", (cache_key, now, now)).fetchone())

    def stats(self):
        with self._db() as db:
            with db:
                db.execute("DELETE FROM report_snapshots WHERE expires<=?", (self.clock(),))
            count, rows, size = db.execute("SELECT COUNT(*),COALESCE(SUM(row_count),0),COALESCE(SUM(LENGTH(payload)),0) FROM report_snapshots").fetchone()
            return {"snapshots": count, "stored_rows": rows, "compressed_bytes": size,
                    "max_snapshots": self.max_snapshots, "max_rows": self.max_rows, "max_bytes": self.max_bytes}

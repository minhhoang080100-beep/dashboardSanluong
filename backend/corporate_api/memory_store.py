"""Request-scoped validation/filtering workspace; never retained between requests."""
from contextlib import contextmanager
import sqlite3
import threading

from .store import ExportStore


class MemoryExportStore(ExportStore):
    """Reuse contract validation/filtering while keeping a single in-memory DB.

    One request owns one workspace and always closes it before returning.
    No result cache, file, cross-request snapshot or background preload exists.
    """

    def __init__(self):
        self._local = threading.local()
        self._lock = threading.RLock()
        self._closed = False
        self._connection = sqlite3.connect(':memory:', check_same_thread=False)
        self._connection.row_factory = sqlite3.Row
        self._connection.execute('PRAGMA foreign_keys=ON')
        self._connection.execute('PRAGMA temp_store=MEMORY')
        try:
            self._initialize_schema()
        except BaseException:
            self.close()
            raise

    @contextmanager
    def db(self):
        existing = getattr(self._local, 'connection', None)
        if existing is not None:
            yield existing
            return
        with self._lock:
            if self._closed:
                raise sqlite3.ProgrammingError('Transient result already closed.')
            with self._connection:
                yield self._connection

    def size_bytes(self):
        with self.db() as db:
            return db.execute('PRAGMA page_count').fetchone()[0] * db.execute('PRAGMA page_size').fetchone()[0]

    def close(self):
        with self._lock:
            if not self._closed:
                self._connection.close()
                self._closed = True

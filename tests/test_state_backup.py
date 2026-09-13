"""Recovery verification preserves users/plans and never overwrites live state."""
from contextlib import closing
import sqlite3

import pytest

from backend.control_store import ControlStore
from backend.state_backup import create_backup, verify_restore


def test_backup_includes_committed_wal_and_restore_preserves_state(tmp_path):
    source = tmp_path / 'control.sqlite3'
    ControlStore(source)
    with closing(sqlite3.connect(source)) as db:
        db.execute('PRAGMA journal_mode=WAL')
        db.execute('CREATE TABLE recovery_probe(value TEXT)')
        db.execute("INSERT INTO recovery_probe VALUES('synthetic committed value')")
        db.commit()
        destination = tmp_path / 'backups' / 'verified.sqlite3'
        result = create_backup(source, destination)
        assert result['tables']['recovery_probe'] == 1
        restored = verify_restore(destination)
        assert restored['restore_verified']
        assert restored['content_sha256'] == result['content_sha256']
        assert db.execute('SELECT COUNT(*) FROM recovery_probe').fetchone()[0] == 1


def test_backup_refuses_overwrite_and_same_live_path(tmp_path):
    source = tmp_path / 'control.sqlite3'
    ControlStore(source)
    before = source.read_bytes()
    with pytest.raises(ValueError):
        create_backup(source, source)
    destination = tmp_path / 'existing.sqlite3'
    destination.write_bytes(b'keep existing backup')
    with pytest.raises(FileExistsError):
        create_backup(source, destination)
    assert destination.read_bytes() == b'keep existing backup'
    assert source.read_bytes() == before


def test_restore_rejects_unrelated_or_corrupt_file(tmp_path):
    unrelated = tmp_path / 'other.sqlite3'
    with sqlite3.connect(unrelated) as db:
        db.execute('CREATE TABLE other(value TEXT)')
    with pytest.raises(ValueError):
        verify_restore(unrelated)
    corrupt = tmp_path / 'corrupt.sqlite3'
    corrupt.write_bytes(b'not a database')
    with pytest.raises(sqlite3.DatabaseError):
        verify_restore(corrupt)

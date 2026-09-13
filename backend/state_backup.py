"""Back up the control database online and verify a restore without replacing live state."""
import argparse
from contextlib import closing
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import tempfile


def _open_readonly(path):
    path = Path(path).resolve(strict=True)
    return sqlite3.connect(path.as_uri() + '?mode=ro', uri=True, timeout=10)


def _inspect(db):
    if db.execute('PRAGMA integrity_check').fetchone()[0] != 'ok' or db.execute('PRAGMA foreign_key_check').fetchone():
        raise ValueError('Database integrity check failed.')
    tables = [row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name")]
    if not {'users', 'plans', 'closed_reports'} <= set(tables):
        raise ValueError('This is not a dashboard control database.')
    digest, counts = hashlib.sha256(), {}
    for table in tables:
        quoted = '"' + table.replace('"', '""') + '"'
        primary = sorted((row[5], row[1]) for row in db.execute(f'PRAGMA table_info({quoted})') if row[5])
        ordering = ','.join('"' + name.replace('"', '""') + '"' for _, name in primary) or 'rowid'
        digest.update(json.dumps(table, ensure_ascii=False).encode('utf-8') + b'\n')
        count = 0
        for row in db.execute(f'SELECT * FROM {quoted} ORDER BY {ordering}'):
            digest.update(json.dumps(row, ensure_ascii=False, separators=(',', ':')).encode('utf-8') + b'\n')
            count += 1
        counts[table] = count
    return {'tables': counts, 'content_sha256': digest.hexdigest()}


def create_backup(source, destination):
    source = Path(source).resolve(strict=True)
    destination = Path(destination).absolute()
    if destination.resolve() == source:
        raise ValueError('Backup destination must differ from the live database.')
    destination.parent.mkdir(parents=True, exist_ok=True)
    # Exclusive creation refuses overwrites, including a symlink to live state.
    fd = os.open(destination, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    os.close(fd)
    try:
        with closing(_open_readonly(source)) as live, closing(sqlite3.connect(destination)) as copy:
            live.backup(copy, pages=256, sleep=0.05)
            result = _inspect(copy)
        return {'backup': str(destination), **result}
    except Exception:
        destination.unlink(missing_ok=True)
        raise


def verify_restore(source):
    """Restore to a private temporary database, compare contents, then discard it."""
    with closing(_open_readonly(source)) as backup:
        original = _inspect(backup)
        with tempfile.TemporaryDirectory(prefix='dashboard-restore-check-') as temporary:
            with closing(sqlite3.connect(Path(temporary) / 'control.sqlite3')) as restored:
                backup.backup(restored)
                result = _inspect(restored)
                if result != original:
                    raise ValueError('Restored data differs from the backup.')
    return {'restore_verified': True, **result}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    backup = commands.add_parser('backup', help='Create an online backup without overwriting files.')
    backup.add_argument('--source', default=os.environ.get('DASHBOARD_STATE_PATH', str(Path(__file__).resolve().parent / '.data' / 'control.sqlite3')))
    backup.add_argument('--output', required=True)
    verify = commands.add_parser('verify', help='Verify a restore in a separate temporary database.')
    verify.add_argument('--backup', required=True)
    args = parser.parse_args(argv)
    try:
        result = create_backup(args.source, args.output) if args.command == 'backup' else verify_restore(args.backup)
    except (ValueError, OSError, sqlite3.Error):
        parser.exit(1, 'Backup/restore verification failed. Check file paths, access and database integrity. Existing live state was not replaced.\n')
    print(json.dumps(result, ensure_ascii=True))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())

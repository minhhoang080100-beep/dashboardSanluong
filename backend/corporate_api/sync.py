"""Bounded scheduled extraction with atomic publication and a private status report."""
import argparse
from contextlib import contextmanager
from datetime import datetime, timedelta
import json
import os
from pathlib import Path
import tempfile
import time

from .registry import MODELS, S_MODELS, resources_for_domain
from .manage_exports import extract, publish_preview, check_publication, json_default
from .reconciliation import reconciliation_report
from .store import ExportStore, TZ


class SyncBusy(Exception):
    pass


@contextmanager
def sync_lock(path):
    """An OS lock releases on crash; do not delete its inode after unlocking."""
    with Path(path).open('a+b') as handle:
        handle.seek(0, 2)
        if handle.tell() == 0:
            handle.write(b'0')
            handle.flush()
        handle.seek(0)
        try:
            if os.name == 'nt':
                import msvcrt
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            raise SyncBusy() from None
        try:
            yield
        finally:
            handle.seek(0)
            if os.name == 'nt':
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(handle, fcntl.LOCK_UN)


def write_report(path, report):
    path = Path(path)
    fd, temporary = tempfile.mkstemp(prefix='.corporate-sync-', dir=path.parent)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as stream:
            json.dump(report, stream, ensure_ascii=False, indent=2, default=json_default, allow_nan=False)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def failure_report(*, check_only=False):
    # Never retain exceptions, profile fields or source row values in status.
    return {'status': 'failed', 'failureCode': 'SYNC_FAILED',
            'checkedAt': datetime.now(TZ).isoformat(), 'checkOnly': check_only,
            'reportVersion': 1, 'actualPublicationValidated': False, 'resources': {}}


def record_failure_if_idle(store, *, check_only=False):
    """A CLI/configuration failure must not overwrite another active job's report."""
    try:
        with sync_lock(store.path.with_suffix('.sync.lock')):
            write_report(store.path.with_suffix('.sync-status.json'),
                         failure_report(check_only=check_only))
    except (OSError, SyncBusy):
        pass


def run_once(profile, store, *, days=7, resources=None, query_fn=None, now=None, check_only=False):
    if type(days) is not int or not 1 <= days <= 31:
        raise ValueError('Sync window must be between 1 and 31 days')
    if resources is None:
        resources = sorted(S_MODELS)
    elif (not isinstance(resources, (list, tuple, set, frozenset)) or not resources
          or any(not isinstance(resource, str) or resource not in MODELS for resource in resources)):
        raise ValueError('Unknown or empty sync resource selection')
    else:
        resources = sorted(set(resources))
    today = (now or datetime.now(TZ)).astimezone(TZ).date()
    start = today - timedelta(days=days - 1)
    report_path = store.path.with_suffix('.sync-status.json')
    try:
        with sync_lock(store.path.with_suffix('.sync.lock')):
            try:
                kwargs = {'query_fn': query_fn} if query_fn else {}
                preview = extract(profile, start, today, resources, **kwargs)
                report = reconciliation_report(preview, profile)
                if set(report['resources']) != set(resources):
                    raise ValueError('Incomplete reconciliation resource coverage')
                report.update(checkedAt=datetime.now(TZ).isoformat(), checkOnly=check_only,
                              status='blocked', reportVersion=1, actualPublicationValidated=False)
                # Preview diagnostics cannot prove dependencies already present
                # in the store. The publication validator can, so resource-only
                # refreshes must reach that validator when extraction succeeded.
                if all(preview['datasets'][name].get('ready') is True
                       and not preview['datasets'][name].get('blockers') for name in resources):
                    try:
                        if check_only:
                            check_publication(preview, profile, store, resources)
                            report['status'] = 'checked'
                        else:
                            report['publication'] = publish_preview(preview, profile, store, resources)
                            report['status'] = 'published'
                        report['actualPublicationValidated'] = True
                    except Exception:
                        # Atomic publication retains every previous snapshot.
                        report['failureCode'] = 'VALIDATION_FAILED' if check_only else 'PUBLICATION_FAILED'
            except Exception:
                # Write failures while still holding the job lock: a previous
                # success must not remain the reported result of a failed run.
                report = failure_report(check_only=check_only)
            write_report(report_path, report)
            return report
    except SyncBusy:
        return {'status': 'busy', 'failureCode': 'SYNC_ALREADY_RUNNING'}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--profile', required=True)
    parser.add_argument('--days', type=int, default=7, choices=range(1, 32))
    parser.add_argument('--resource', action='append', choices=sorted(MODELS))
    parser.add_argument('--domain', choices=('production', 'operations', 'all'), default='production')
    parser.add_argument('--interval', type=int, default=0, help='0 runs once; otherwise at least 60 seconds.')
    parser.add_argument('--check-only', action='store_true')
    args = parser.parse_args(argv)
    if args.interval and args.interval < 60:
        parser.error('Interval must be zero or at least 60 seconds')
    while True:
        store = None
        try:
            # Re-read profile each cycle so an approved correction takes effect.
            store = ExportStore()
            profile = json.loads(Path(args.profile).read_text(encoding='utf-8-sig'))
            report = run_once(profile, store, days=args.days,
                              resources=args.resource or resources_for_domain(args.domain), check_only=args.check_only)
            print(json.dumps({'status': report['status'], 'failureCode': report.get('failureCode'),
                'actualPublicationValidated': report.get('actualPublicationValidated', False),
                'resources': {name: {'rows': item['rows'], 'deliveryReady': item['deliveryReady']}
                              for name, item in report.get('resources', {}).items()}}), flush=True)
            code = (0 if report['status'] in {'published', 'checked'}
                    else 1 if report['status'] == 'failed' else 2)
        except Exception:
            # Never print profile, credentials, connection or SQL exception text.
            print('{"status":"failed","failureCode":"SYNC_FAILED"}', flush=True)
            if store is not None:
                record_failure_if_idle(store, check_only=args.check_only)
            code = 1
        if not args.interval:
            return code
        time.sleep(args.interval)


if __name__ == '__main__':
    raise SystemExit(main())

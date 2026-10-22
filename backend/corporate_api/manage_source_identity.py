"""Guarded, local-only S identity activation; default action is a dry-run plan.

Run inside the intended Railway deployment with ``python -m
corporate_api.manage_source_identity``. Supply --expected-profile-sha256,
--service-id and --deployment-id for every action. ``apply`` and ``rollback``
also require --backup, an unused filename beside the configured profile.
No SQL, API request, account change or receiver migration is performed.
Rollback is deliberately tied to the same service AND deployment as apply;
restore the profile before moving to another deployment. An operator changing
the profile outside this command must honor the adjacent .identity.lock file.
"""
import argparse
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import sys
import tempfile
from uuid import UUID

from .source_identity import MODE, NATIVE_MODE, mode

MAX_BYTES = 2 * 1024 * 1024
RECEIPT_VERSION = 1


class ActivationError(ValueError):
    """Fixed, non-secret error code for operator output."""


def _require(condition, code):
    if not condition:
        raise ActivationError(code)


def _sha(raw):
    return hashlib.sha256(raw).hexdigest()


def _safe_path(path):
    _require(path.is_absolute(), 'ABSOLUTE_PATH_REQUIRED')
    _require('..' not in path.parts, 'UNSAFE_PATH')
    for item in [path, *path.parents]:
        try:
            info = item.lstat()
        except FileNotFoundError:
            continue
        _require(not stat.S_ISLNK(info.st_mode)
                 and not getattr(info, 'st_file_attributes', 0) & 0x400,
                 'SYMLINK_OR_REPARSE_POINT')
    return path


def _signature(info):
    return (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns,
            stat.S_IMODE(info.st_mode), info.st_uid, info.st_gid)


def _read(path):
    _safe_path(path)
    flags = os.O_RDONLY | getattr(os, 'O_BINARY', 0) | getattr(os, 'O_NOFOLLOW', 0)
    fd = os.open(path, flags)
    with os.fdopen(fd, 'rb') as handle:
        before = os.fstat(handle.fileno())
        _require(stat.S_ISREG(before.st_mode), 'REGULAR_FILE_REQUIRED')
        raw = handle.read(MAX_BYTES + 1)
        after = os.fstat(handle.fileno())
    _require(len(raw) <= MAX_BYTES, 'FILE_TOO_LARGE')
    _require(_signature(before) == _signature(after) == _signature(path.lstat()),
             'FILE_CHANGED_DURING_READ')
    _safe_path(path)
    return raw, after


def _object(pairs):
    result = {}
    for key, value in pairs:
        _require(key not in result, 'DUPLICATE_JSON_KEY')
        result[key] = value
    return result


def _json(raw):
    try:
        result = json.loads(raw.decode('utf-8-sig'), object_pairs_hook=_object,
                            parse_constant=lambda _: (_ for _ in ()).throw(ActivationError('INVALID_JSON')))
    except (UnicodeError, ValueError, RecursionError):
        raise ActivationError('INVALID_JSON') from None
    _require(isinstance(result, dict), 'INVALID_JSON_OBJECT')
    return result


def _profile(raw):
    value = _json(raw)
    terminals = value.get('terminals')
    _require(value.get('approved') is True and value.get('company_id') == 'CNT'
             and isinstance(terminals, list) and len(terminals) == 2
             and all(isinstance(item, str) for item in terminals)
             and set(terminals) == {'cua_lo', 'ben_thuy'}
             and value.get('cargo_catalog_source') == 'native_groups'
             and isinstance(value.get('container_size_source'), str)
             and value['container_size_source'] in {'native_cargo', 'native_domestic'},
             'INCOMPATIBLE_PROFILE')
    try:
        mode(value)
    except ValueError:
        raise ActivationError('INVALID_IDENTITY_MODE') from None
    return value


def _candidate(profile):
    raw = (json.dumps({**profile, 'source_identity_mode': MODE}, ensure_ascii=False,
                      indent=2, allow_nan=False) + '\n').encode('utf-8')
    _require(len(raw) <= MAX_BYTES, 'FILE_TOO_LARGE')
    return raw


def _context(expected_sha, service_id, deployment_id):
    _require(isinstance(expected_sha, str) and re.fullmatch('[a-f0-9]{64}', expected_sha),
             'INVALID_EXPECTED_HASH')
    try:
        _require(str(UUID(service_id)) == service_id and str(UUID(deployment_id)) == deployment_id,
                 'INVALID_TARGET_ID')
    except (ValueError, TypeError, AttributeError):
        raise ActivationError('INVALID_TARGET_ID') from None
    _require(os.environ.get('RAILWAY_SERVICE_ID') == service_id
             and os.environ.get('RAILWAY_DEPLOYMENT_ID') == deployment_id, 'TARGET_MISMATCH')
    _require(os.environ.get('CORPORATE_READ_MODE', '').strip().lower() == 'live', 'LIVE_MODE_REQUIRED')
    value = os.environ.get('CORPORATE_SOURCE_PROFILE') or os.environ.get('CORPORATE_SYNC_PROFILE')
    _require(isinstance(value, str) and bool(value), 'PROFILE_PATH_REQUIRED')
    return _safe_path(Path(value))


def _metadata(info):
    return {'permissions': stat.S_IMODE(info.st_mode), 'uid': info.st_uid, 'gid': info.st_gid}


def _permissions(fd, metadata):
    if hasattr(os, 'fchown'):
        current = os.fstat(fd)
        if (current.st_uid, current.st_gid) != (metadata['uid'], metadata['gid']):
            os.fchown(fd, metadata['uid'], metadata['gid'])
    if hasattr(os, 'fchmod'):
        os.fchmod(fd, metadata['permissions'])


def _write_new(path, raw, metadata):
    _safe_path(path)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, 'O_BINARY', 0), 0o600)
    with os.fdopen(fd, 'wb') as handle:
        _permissions(handle.fileno(), metadata)
        handle.write(raw)
        handle.flush()
        os.fsync(handle.fileno())
    if os.name == 'nt':
        os.chmod(path, metadata['permissions'])


def _sync_parent(path):
    if os.name != 'nt':
        fd = os.open(path.parent, os.O_RDONLY | getattr(os, 'O_DIRECTORY', 0))
        try:
            os.fsync(fd)
        finally:
            os.close(fd)


@contextmanager
def _lock(path):
    lock = path.with_name(path.name + '.identity.lock')
    _safe_path(lock)
    try:
        fd = os.open(lock, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        raise ActivationError('ACTIVATION_LOCKED') from None
    owned = os.fstat(fd)
    os.close(fd)
    try:
        yield
    finally:
        try:
            current = lock.lstat()
            if (current.st_dev, current.st_ino) == (owned.st_dev, owned.st_ino):
                lock.unlink()
        except FileNotFoundError:
            pass


def _replace(path, raw, previous, previous_info):
    fd, name = tempfile.mkstemp(prefix='.' + path.name + '.identity-', dir=path.parent)
    staging = Path(name)
    try:
        with os.fdopen(fd, 'wb') as handle:
            _permissions(handle.fileno(), _metadata(previous_info))
            handle.write(raw)
            handle.flush()
            os.fsync(handle.fileno())
        if os.name == 'nt':
            os.chmod(staging, stat.S_IMODE(previous_info.st_mode))
        current, info = _read(path)
        _require(current == previous and _signature(info) == _signature(previous_info),
                 'CONCURRENT_PROFILE_CHANGE')
        # Cooperative invocations share the lock. Recheck immediately before
        # atomic replace; operators must not edit this file outside that lock.
        os.replace(staging, path)
        _sync_parent(path)
        installed, info = _read(path)
        _require(installed == raw and _metadata(info) == _metadata(previous_info), 'POSTWRITE_MISMATCH')
    finally:
        if staging.exists():
            staging.unlink()


def activate(*, expected_sha, service_id, deployment_id, action='plan', backup=None):
    _require(action in {'plan', 'apply', 'rollback'}, 'INVALID_ACTION')
    path = _context(expected_sha, service_id, deployment_id)
    if action == 'plan':
        previous, info = _read(path)
        _require(_sha(previous) == expected_sha, 'PROFILE_HASH_MISMATCH')
        profile = _profile(previous)
        _require(mode(profile) == NATIVE_MODE, 'ALREADY_ACTIVATED')
        candidate = _candidate(profile)
        return {'action': 'plan', 'changed': False, 'oldSha256': expected_sha,
                'newSha256': _sha(candidate), 'oldMode': NATIVE_MODE, 'newMode': MODE,
                'changedFields': ['source_identity_mode']}
    _require(backup is not None, 'BACKUP_REQUIRED')
    backup = _safe_path(Path(backup))
    _require(backup.parent == path.parent and backup != path
             and backup.name != path.name + '.identity.lock', 'INVALID_BACKUP_PATH')
    receipt_path = backup.with_name(backup.name + '.activation.json')
    _require(receipt_path != path, 'INVALID_BACKUP_PATH')
    _safe_path(receipt_path)
    with _lock(path):
        previous, info = _read(path)
        _require(_sha(previous) == expected_sha, 'PROFILE_HASH_MISMATCH')
        profile = _profile(previous)
        if action == 'apply':
            _require(mode(profile) == NATIVE_MODE, 'ALREADY_ACTIVATED')
            _require(not backup.exists() and not receipt_path.exists(), 'BACKUP_ALREADY_EXISTS')
            candidate = _candidate(profile)
            receipt = {'version': RECEIPT_VERSION, 'profilePath': str(path), 'backupPath': str(backup),
                       'serviceId': service_id, 'deploymentId': deployment_id,
                       'oldSha256': expected_sha, 'newSha256': _sha(candidate),
                       'oldMode': NATIVE_MODE, 'newMode': MODE, 'fileMetadata': _metadata(info)}
            _write_new(backup, previous, _metadata(info))
            saved, saved_info = _read(backup)
            _require(saved == previous and _metadata(saved_info) == _metadata(info), 'BACKUP_VERIFICATION_FAILED')
            _write_new(receipt_path, json.dumps(receipt, sort_keys=True).encode('utf-8'),
                       {**_metadata(info), 'permissions': 0o600})
            _sync_parent(path)
            _replace(path, candidate, previous, info)
            return {'action': action, 'changed': True, 'oldSha256': expected_sha,
                    'newSha256': _sha(candidate), 'oldMode': NATIVE_MODE, 'newMode': MODE,
                    'changedFields': ['source_identity_mode']}
        _require(mode(profile) == MODE, 'ROLLBACK_MODE_MISMATCH')
        original, backup_info = _read(backup)
        receipt = _json(_read(receipt_path)[0])
        required = {'version', 'profilePath', 'backupPath', 'serviceId', 'deploymentId',
                    'oldSha256', 'newSha256', 'oldMode', 'newMode', 'fileMetadata'}
        _require(set(receipt) == required and receipt['version'] == RECEIPT_VERSION
                 and receipt['profilePath'] == str(path) and receipt['backupPath'] == str(backup)
                 and receipt['serviceId'] == service_id and receipt['deploymentId'] == deployment_id
                 and receipt['newSha256'] == expected_sha and receipt['oldSha256'] == _sha(original)
                 and receipt['oldMode'] == NATIVE_MODE and receipt['newMode'] == MODE
                 and receipt['fileMetadata'] == _metadata(backup_info) == _metadata(info),
                 'ROLLBACK_RECEIPT_MISMATCH')
        old_profile = _profile(original)
        _require(mode(old_profile) == NATIVE_MODE
                 and profile == {**old_profile, 'source_identity_mode': MODE},
                 'ROLLBACK_PROFILE_MISMATCH')
        _replace(path, original, previous, info)
        return {'action': action, 'changed': True, 'oldSha256': expected_sha,
                'newSha256': _sha(original), 'oldMode': MODE, 'newMode': NATIVE_MODE,
                'changedFields': ['source_identity_mode']}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', nargs='?', choices=('plan', 'apply', 'rollback'), default='plan')
    parser.add_argument('--expected-profile-sha256', required=True)
    parser.add_argument('--service-id', required=True)
    parser.add_argument('--deployment-id', required=True)
    parser.add_argument('--backup', type=Path)
    args = parser.parse_args(argv)
    try:
        result = activate(expected_sha=args.expected_profile_sha256, service_id=args.service_id,
                          deployment_id=args.deployment_id, action=args.action, backup=args.backup)
    except ActivationError as exc:
        print(json.dumps({'error': str(exc)}), file=sys.stderr)
        return 1
    except (OSError, ValueError, TypeError, OverflowError, RecursionError):
        print(json.dumps({'error': 'LOCAL_FILE_OPERATION_FAILED'}), file=sys.stderr)
        return 1
    print(json.dumps(result))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())

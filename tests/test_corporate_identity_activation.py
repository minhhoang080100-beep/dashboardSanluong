"""Offline activation uses temporary files and synthetic Railway identifiers only."""
import hashlib
import json
import os
from pathlib import Path
import stat
from types import SimpleNamespace

import pytest

from backend.corporate_api import manage_source_identity as activation


SERVICE = '11111111-1111-4111-8111-111111111111'
DEPLOYMENT = '22222222-2222-4222-8222-222222222222'
PROFILE = {'approved': True, 'company_id': 'CNT', 'terminals': ['cua_lo', 'ben_thuy'],
           'cargo_catalog_source': 'native_groups', 'container_size_source': 'native_cargo',
           'production_scope': 'nghe_tinh', 'quay_selection': 'source_statistics',
           'gate_selection': 'vessel_type', 'accepted_null_fields': {'bulkQuayVolumesCB': ['bulkOriginId']},
           'bulk_unit_conversion_cargo_ids': {'cua_lo': ['123'], 'ben_thuy': []},
           'private_note': 'Synthetic private value never printed'}


def sha(value):
    return hashlib.sha256(value).hexdigest()


@pytest.fixture
def setup(tmp_path, monkeypatch):
    target = tmp_path / 'corporate-source.json'
    # BOM, CRLF, unusual indentation are preserved in backup and rollback.
    raw = b'\xef\xbb\xbf' + json.dumps(PROFILE, ensure_ascii=False, indent=3).replace('\n', '\r\n').encode('utf-8')
    target.write_bytes(raw)
    os.chmod(target, 0o600)
    monkeypatch.setenv('RAILWAY_SERVICE_ID', SERVICE)
    monkeypatch.setenv('RAILWAY_DEPLOYMENT_ID', DEPLOYMENT)
    monkeypatch.setenv('CORPORATE_READ_MODE', 'live')
    monkeypatch.setenv('CORPORATE_SOURCE_PROFILE', str(target))
    monkeypatch.delenv('CORPORATE_SYNC_PROFILE', raising=False)
    return SimpleNamespace(target=target, raw=raw, backup=tmp_path / 'identity-native.backup',
        args={'expected_sha': sha(raw), 'service_id': SERVICE, 'deployment_id': DEPLOYMENT})


def apply(setup):
    return activation.activate(**setup.args, action='apply', backup=setup.backup)


def rollback(setup, installed):
    return activation.activate(**{**setup.args, 'expected_sha': installed['newSha256']},
                               action='rollback', backup=setup.backup)


def test_default_plan_does_not_create_files_and_matches_apply(setup):
    before = list(setup.target.parent.iterdir())
    planned = activation.activate(**setup.args)
    assert planned['action'] == 'plan' and planned['changed'] is False
    assert planned['changedFields'] == ['source_identity_mode']
    assert list(setup.target.parent.iterdir()) == before
    assert setup.target.read_bytes() == setup.raw
    result = apply(setup)
    assert result['newSha256'] == planned['newSha256']


def test_apply_changes_only_mode_and_rollback_restores_exact_bytes_and_metadata(setup):
    metadata = activation._metadata(setup.target.stat())
    result = apply(setup)
    assert json.loads(setup.target.read_bytes()) == {**PROFILE, 'source_identity_mode': activation.MODE}
    assert setup.backup.read_bytes() == setup.raw
    assert activation._metadata(setup.target.stat()) == activation._metadata(setup.backup.stat()) == metadata
    assert result['changed'] and result['oldSha256'] == sha(setup.raw)
    restored = rollback(setup, result)
    assert restored['newMode'] == 'native' and restored['newSha256'] == sha(setup.raw)
    assert setup.target.read_bytes() == setup.raw
    assert activation._metadata(setup.target.stat()) == metadata
    assert setup.backup.read_bytes() == setup.raw
    assert not setup.target.with_name(setup.target.name + '.identity.lock').exists()


def test_explicit_native_mode_is_restored_not_removed(setup):
    raw = json.dumps({**PROFILE, 'source_identity_mode': 'native'}).encode()
    setup.target.write_bytes(raw)
    setup.args['expected_sha'] = sha(raw)
    result = apply(setup)
    rollback(setup, result)
    assert setup.target.read_bytes() == raw


@pytest.mark.parametrize('key,value', [
    ('approved', False), ('approved', 1), ('company_id', 'OTHER'),
    ('terminals', ['cua_lo']), ('terminals', ['cua_lo', 'cua_lo']),
    ('terminals', [True, 'ben_thuy']), ('cargo_catalog_source', 'configured'),
    ('container_size_source', 'configured'), ('container_size_source', []),
    ('source_identity_mode', None), ('source_identity_mode', 'unknown'),
])
def test_incompatible_profiles_never_write(setup, key, value):
    raw = json.dumps({**PROFILE, key: value}).encode()
    setup.target.write_bytes(raw)
    with pytest.raises(activation.ActivationError):
        activation.activate(**{**setup.args, 'expected_sha': sha(raw)}, action='apply', backup=setup.backup)
    assert setup.target.read_bytes() == raw and not setup.backup.exists()


@pytest.mark.parametrize('key,value,code', [
    ('RAILWAY_SERVICE_ID', DEPLOYMENT, 'TARGET_MISMATCH'),
    ('RAILWAY_DEPLOYMENT_ID', SERVICE, 'TARGET_MISMATCH'),
    ('CORPORATE_READ_MODE', 'published', 'LIVE_MODE_REQUIRED'),
    ('CORPORATE_READ_MODE', '', 'LIVE_MODE_REQUIRED'),
    ('CORPORATE_SOURCE_PROFILE', 'relative.json', 'ABSOLUTE_PATH_REQUIRED'),
])
def test_target_and_live_guards_fail_before_reading_or_writing(setup, monkeypatch, key, value, code):
    monkeypatch.setenv(key, value)
    monkeypatch.setattr(activation, '_read', lambda *args: pytest.fail('Must not read profile'))
    with pytest.raises(activation.ActivationError, match=code):
        apply(setup)
    assert setup.target.read_bytes() == setup.raw and not setup.backup.exists()


def test_profile_path_fallback_matches_live_reader(setup, monkeypatch):
    monkeypatch.delenv('CORPORATE_SOURCE_PROFILE')
    monkeypatch.setenv('CORPORATE_SYNC_PROFILE', str(setup.target))
    assert activation.activate(**setup.args)['oldSha256'] == sha(setup.raw)


@pytest.mark.parametrize('override', [{'expected_sha': '0' * 64}, {'expected_sha': 'bad'},
    {'service_id': 'bad'}, {'deployment_id': 'bad'}])
def test_explicit_expectations_are_mandatory_and_exact(setup, override):
    with pytest.raises(activation.ActivationError):
        activation.activate(**{**setup.args, **override}, action='apply', backup=setup.backup)
    assert setup.target.read_bytes() == setup.raw and not setup.backup.exists()


def test_cannot_overwrite_backup_or_repeat_activation(setup):
    setup.backup.write_bytes(b'existing backup')
    with pytest.raises(activation.ActivationError, match='BACKUP_ALREADY_EXISTS'):
        apply(setup)
    assert setup.backup.read_bytes() == b'existing backup'
    setup.backup.unlink()
    result = apply(setup)
    with pytest.raises(activation.ActivationError, match='ALREADY_ACTIVATED'):
        activation.activate(**{**setup.args, 'expected_sha': result['newSha256']})


def test_preexisting_receipt_never_gets_overwritten(setup):
    receipt = setup.backup.with_name(setup.backup.name + '.activation.json')
    receipt.write_text('existing')
    with pytest.raises(activation.ActivationError, match='BACKUP_ALREADY_EXISTS'):
        apply(setup)
    assert receipt.read_text() == 'existing' and not setup.backup.exists()


@pytest.mark.parametrize('kind', ['profile', 'parent', 'backup'])
def test_symlink_and_windows_reparse_paths_rejected(setup, monkeypatch, kind):
    blocked = {'profile': setup.target, 'parent': setup.target.parent, 'backup': setup.backup}[kind]
    original = Path.lstat
    def lstat(path, *args, **kwargs):
        if path == blocked:
            return SimpleNamespace(st_mode=stat.S_IFLNK, st_file_attributes=0x400)
        return original(path, *args, **kwargs)
    monkeypatch.setattr(Path, 'lstat', lstat)
    with pytest.raises(activation.ActivationError, match='SYMLINK_OR_REPARSE_POINT'):
        apply(setup)
    assert setup.target.read_bytes() == setup.raw


def test_concurrent_change_after_backup_does_not_overwrite_external_edit(setup, monkeypatch):
    writer = activation._write_new
    external = json.dumps({**PROFILE, 'production_scope': 'all_activity'}).encode()
    def write(path, raw, metadata):
        writer(path, raw, metadata)
        if path.name.endswith('.activation.json'):
            setup.target.write_bytes(external)
    monkeypatch.setattr(activation, '_write_new', write)
    with pytest.raises(activation.ActivationError, match='CONCURRENT_PROFILE_CHANGE'):
        apply(setup)
    assert setup.target.read_bytes() == external and setup.backup.read_bytes() == setup.raw
    assert not list(setup.target.parent.glob('.*.identity-*'))


def test_concurrent_activation_lock_preserved(setup):
    lock = setup.target.with_name(setup.target.name + '.identity.lock')
    lock.write_text('owned by another invocation')
    with pytest.raises(activation.ActivationError, match='ACTIVATION_LOCKED'):
        apply(setup)
    assert lock.read_text() == 'owned by another invocation'
    assert setup.target.read_bytes() == setup.raw and not setup.backup.exists()


@pytest.mark.parametrize('tamper', ['backup', 'receipt_hash', 'receipt_target', 'receipt_mode', 'current_profile'])
def test_rollback_refuses_any_unrelated_or_changed_state(setup, tamper):
    result = apply(setup)
    receipt_path = setup.backup.with_name(setup.backup.name + '.activation.json')
    receipt = json.loads(receipt_path.read_bytes())
    if tamper == 'backup':
        setup.backup.write_bytes(b'other backup')
    elif tamper == 'current_profile':
        current = json.loads(setup.target.read_bytes())
        current['gate_selection'] = 'methods'
        setup.target.write_text(json.dumps(current))
        result['newSha256'] = sha(setup.target.read_bytes())
    else:
        field = {'receipt_hash': 'oldSha256', 'receipt_target': 'profilePath', 'receipt_mode': 'newMode'}[tamper]
        receipt[field] = 'wrong'
        receipt_path.write_text(json.dumps(receipt))
    previous = setup.target.read_bytes()
    with pytest.raises(activation.ActivationError):
        rollback(setup, result)
    assert setup.target.read_bytes() == previous


def test_forged_receipt_cannot_restore_profile_with_other_business_rules(setup):
    result = apply(setup)
    receipt_path = setup.backup.with_name(setup.backup.name + '.activation.json')
    original = json.dumps({**PROFILE, 'production_scope': 'all_activity'}).encode()
    setup.backup.write_bytes(original)
    receipt = json.loads(receipt_path.read_bytes())
    receipt['oldSha256'] = sha(original)
    receipt_path.write_text(json.dumps(receipt))
    before = setup.target.read_bytes()
    with pytest.raises(activation.ActivationError, match='ROLLBACK_PROFILE_MISMATCH'):
        rollback(setup, result)
    assert setup.target.read_bytes() == before


@pytest.mark.parametrize('raw', [b'{"approved":true,"approved":false}', b'[]', b'{"x":NaN}', b'\xff'])
def test_noncanonical_json_rejected_without_backup(setup, raw):
    setup.target.write_bytes(raw)
    with pytest.raises(activation.ActivationError):
        activation.activate(**{**setup.args, 'expected_sha': sha(raw)}, action='apply', backup=setup.backup)
    assert setup.target.read_bytes() == raw and not setup.backup.exists()


def test_expanded_candidate_size_checked_before_backup(setup, monkeypatch):
    raw = json.dumps(PROFILE, separators=(',', ':')).encode()
    setup.target.write_bytes(raw)
    monkeypatch.setattr(activation, 'MAX_BYTES', len(raw) + 5)
    with pytest.raises(activation.ActivationError, match='FILE_TOO_LARGE'):
        activation.activate(**{**setup.args, 'expected_sha': sha(raw)}, action='apply', backup=setup.backup)
    assert setup.target.read_bytes() == raw and not setup.backup.exists()


def test_cli_default_dry_run_and_errors_never_print_profile_or_paths(setup, capsys):
    argv = ['--expected-profile-sha256', setup.args['expected_sha'], '--service-id', SERVICE,
            '--deployment-id', DEPLOYMENT]
    assert activation.main(argv) == 0
    success = capsys.readouterr()
    assert json.loads(success.out)['changed'] is False and not success.err
    assert PROFILE['private_note'] not in success.out and str(setup.target) not in success.out
    assert activation.main(['apply', *argv, '--backup', str(setup.target)]) == 1
    failed = capsys.readouterr()
    assert json.loads(failed.err) == {'error': 'INVALID_BACKUP_PATH'}
    assert not failed.out and PROFILE['private_note'] not in failed.err


def test_apply_refuses_missing_backup(setup):
    with pytest.raises(activation.ActivationError, match='BACKUP_REQUIRED'):
        activation.activate(**setup.args, action='apply')
    assert setup.target.read_bytes() == setup.raw


@pytest.mark.parametrize('failure', ['backup_write', 'staging_sync', 'atomic_replace'])
def test_write_failures_leave_original_intact_and_release_owned_lock(setup, monkeypatch, failure):
    def fail(*args, **kwargs):
        raise OSError('Synthetic private path should not be printed')
    if failure == 'backup_write':
        monkeypatch.setattr(activation, '_write_new', fail)
    elif failure == 'atomic_replace':
        monkeypatch.setattr(activation.os, 'replace', fail)
    else:
        original = activation.os.fsync
        count = 0
        def fsync(fd):
            nonlocal count
            count += 1
            # Backup and receipt are already durable. Fail the staging file.
            if count == 3:
                fail()
            return original(fd)
        monkeypatch.setattr(activation, '_sync_parent', lambda path: None)
        monkeypatch.setattr(activation.os, 'fsync', fsync)
    with pytest.raises(OSError):
        apply(setup)
    assert setup.target.read_bytes() == setup.raw
    assert not setup.target.with_name(setup.target.name + '.identity.lock').exists()
    assert not list(setup.target.parent.glob('.*.identity-*'))


def test_unknown_nested_profile_keys_preserved_semantically_then_byte_exact(setup):
    profile = {**PROFILE, 'future_setting': {'array': [1, None, False, {'x': 'Nghệ Tĩnh'}],
                                          'unaltered_mapping': {'42': {'factor': 0.001}}}}
    raw = json.dumps(profile, ensure_ascii=False, separators=(',', ':')).encode('utf-8')
    setup.target.write_bytes(raw)
    setup.args['expected_sha'] = sha(raw)
    installed = apply(setup)
    assert json.loads(setup.target.read_bytes()) == {**profile, 'source_identity_mode': activation.MODE}
    rollback(setup, installed)
    assert setup.target.read_bytes() == raw


def test_rollback_cannot_be_reused_after_changing_deployment(setup, monkeypatch):
    installed = apply(setup)
    replacement = '33333333-3333-4333-8333-333333333333'
    monkeypatch.setenv('RAILWAY_DEPLOYMENT_ID', replacement)
    setup.args['deployment_id'] = replacement
    previous = setup.target.read_bytes()
    with pytest.raises(activation.ActivationError, match='ROLLBACK_RECEIPT_MISMATCH'):
        rollback(setup, installed)
    assert setup.target.read_bytes() == previous


def test_cli_file_exception_does_not_disclose_exception_text(setup, monkeypatch, capsys):
    def fail(*args, **kwargs):
        raise OSError('secret file location or credential')
    monkeypatch.setattr(activation, '_read', fail)
    assert activation.main(['--expected-profile-sha256', setup.args['expected_sha'],
                            '--service-id', SERVICE, '--deployment-id', DEPLOYMENT]) == 1
    captured = capsys.readouterr()
    assert not captured.out
    assert json.loads(captured.err) == {'error': 'LOCAL_FILE_OPERATION_FAILED'}


def test_backup_is_read_back_before_replacing_profile(setup, monkeypatch):
    writer = activation._write_new
    def corrupt(path, raw, metadata):
        writer(path, b'corrupt' if path == setup.backup else raw, metadata)
    monkeypatch.setattr(activation, '_write_new', corrupt)
    with pytest.raises(activation.ActivationError, match='BACKUP_VERIFICATION_FAILED'):
        apply(setup)
    assert setup.target.read_bytes() == setup.raw
    assert not setup.backup.with_name(setup.backup.name + '.activation.json').exists()

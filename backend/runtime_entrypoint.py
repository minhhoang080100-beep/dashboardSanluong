"""Prepare the fixed state mount, drop privileges, then run the API or admin CLI."""
import os
from pathlib import Path
import stat
import sys


def main():
    if len(sys.argv) < 2:
        raise SystemExit('No runtime command was provided.')
    if os.name == 'posix':
        import pwd
        state = Path(os.environ.get('DASHBOARD_STATE_PATH', '/app/.data/control.sqlite3'))
        directory = state.parent
        if not state.is_absolute() or state.name != 'control.sqlite3' or str(directory) not in {'/data', '/app/.data'}:
            raise SystemExit('DASHBOARD_STATE_PATH must be /data/control.sqlite3 or /app/.data/control.sqlite3 in the container.')
        if os.environ.get('RAILWAY_PROJECT_ID') or os.environ.get('RAILWAY_ENVIRONMENT_ID'):
            if os.environ.get('RAILWAY_VOLUME_MOUNT_PATH') != str(directory):
                raise SystemExit('Attach a persistent Railway volume at the configured state directory before starting the dashboard.')
        if directory.is_symlink() or directory.resolve() != directory:
            raise SystemExit('The state directory must not be a symbolic link.')
        account = pwd.getpwnam('dashboard')
        if os.getuid() == 0:
            directory.mkdir(mode=0o700, exist_ok=True)
            os.chown(directory, account.pw_uid, account.pw_gid)
            os.chmod(directory, 0o700)
            for name in ['control.sqlite3', 'control.sqlite3-wal', 'control.sqlite3-shm',
                         'report-cache.sqlite3', 'report-cache.sqlite3-wal', 'report-cache.sqlite3-shm']:
                target = directory / name
                if target.is_symlink():
                    raise SystemExit('A state file must not be a symbolic link.')
                if target.exists():
                    info = target.stat()
                    if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
                        raise SystemExit('A state file must be an ordinary file with one link.')
                    os.chown(target, account.pw_uid, account.pw_gid)
                    os.chmod(target, 0o600)
            os.setgroups([])
            os.setgid(account.pw_gid)
            os.setuid(account.pw_uid)
        if not directory.is_dir() or not os.access(directory, os.W_OK):
            raise SystemExit('The dashboard user cannot write the state mount. Check volume ownership and RAILWAY_RUN_UID=0.')
        os.umask(0o077)
    os.execvp(sys.argv[1], sys.argv[1:])


if __name__ == '__main__':
    main()

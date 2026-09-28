"""Run the HTTP server and an optional isolated sync process on the same volume."""
import os
from pathlib import Path
import signal
import subprocess
import sys
import time


def worker_command(env):
    if env.get('CORPORATE_SYNC_ENABLED', '').lower() not in {'1', 'true'}:
        return None
    profile = env.get('CORPORATE_SYNC_PROFILE', '')
    if not profile or not Path(profile).is_file():
        raise ValueError('Missing sync profile')
    interval = int(env.get('CORPORATE_SYNC_INTERVAL_SECONDS', '900'))
    days = int(env.get('CORPORATE_SYNC_LOOKBACK_DAYS', '7'))
    if not 60 <= interval <= 86400 or not 1 <= days <= 31:
        raise ValueError('Invalid sync scheduling settings')
    domain = env.get('CORPORATE_SYNC_DOMAIN', 'production')
    if domain not in {'production', 'operations', 'all'}:
        raise ValueError('Invalid sync domain')
    command = [sys.executable, '-m', __package__ + '.sync', '--profile', profile,
            '--days', str(days), '--interval', str(interval)]
    if domain != 'production':
        command.extend(['--domain', domain])
    return command


def main():
    try:
        command = worker_command(os.environ)
    except (ValueError, OSError):
        # Dashboard remains available; the missing worker must be visible in logs.
        print('Corporate sync disabled: invalid configuration.', flush=True)
        command = None
    server = subprocess.Popen([sys.executable, '-m', 'uvicorn', 'main:app', '--host', '0.0.0.0',
                               '--port', os.environ.get('PORT', '8000')])
    worker = None
    stopping = False
    restart_at = 0.0
    def stop(*_):
        nonlocal stopping
        stopping = True
    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    try:
        while not stopping and server.poll() is None:
            if command and (worker is None or worker.poll() is not None) and time.monotonic() >= restart_at:
                if worker is not None:
                    print('Corporate sync process exited; restarting.', flush=True)
                try:
                    worker = subprocess.Popen(command)
                except OSError:
                    print('Corporate sync process could not start; retrying in 60 seconds.', flush=True)
                restart_at = time.monotonic() + 60
            time.sleep(1)
    finally:
        for process in (worker, server):
            if process is not None and process.poll() is None:
                process.terminate()
        for process in (worker, server):
            if process is not None:
                try:
                    process.wait(timeout=15)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()
    return server.returncode if server.returncode and not stopping else 0


if __name__ == '__main__':
    raise SystemExit(main())

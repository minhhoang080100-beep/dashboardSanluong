"""Verify packaged API, privileges and persistent state using an isolated Docker volume."""
import argparse
import json
import re
import secrets
import subprocess
import time
from urllib.error import URLError
from urllib.request import Request, urlopen
from uuid import uuid4


def docker(*args):
    result = subprocess.run(['docker', *args], capture_output=True, text=True, timeout=90)
    if result.returncode:
        # Never print captured credentials or arbitrary container log contents.
        raise RuntimeError(f'Docker command failed: {args[0]}')
    return result.stdout.strip()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--image', default='dashboard-sanluong:local-review')
    args = parser.parse_args()
    name = 'dashboard-check-' + uuid4().hex[:12]
    volume, running = name + '-data', False
    docker('volume', 'create', volume)
    try:
        docker('run', '-d', '--name', name, '--user', '0', '-p', '127.0.0.1::8000',
               '-v', volume + ':/data', '-e', 'DASHBOARD_STATE_PATH=/data/control.sqlite3',
               '-e', 'RAILWAY_PROJECT_ID=synthetic-verification', '-e', 'RAILWAY_VOLUME_MOUNT_PATH=/data', args.image)
        running = True
        address = docker('port', name, '8000/tcp').splitlines()[0]
        base = 'http://' + address + '/api'
        token = None

        def request(path, body=None, timeout=10):
            headers = {'Content-Type': 'application/json'}
            if token:
                headers['Authorization'] = 'Bearer ' + token
            payload = json.dumps(body).encode() if body is not None else None
            with urlopen(Request(base + path, data=payload, headers=headers), timeout=timeout) as response:
                return json.load(response)

        def ready():
            deadline = time.monotonic() + 25
            while time.monotonic() < deadline:
                try:
                    if request('/health/live', timeout=2)['status'] == 'ok':
                        return
                except (URLError, TimeoutError, ConnectionError):
                    pass
                time.sleep(0.25)
            raise RuntimeError('Packaged API did not become ready.')

        ready()
        identity = docker('exec', name, 'python', '-c', "from pathlib import Path; print(next(line.split()[2] for line in Path('/proc/1/status').read_text().splitlines() if line.startswith('Uid:')))")
        assert identity == '10001', 'API container did not drop root privileges'
        docker('exec', '--user', '10001', name, 'python', 'manage_users.py', 'bootstrap-admin', '--username', 'synthetic-admin', '--output', '/data/access.txt')
        access = docker('exec', '--user', '10001', name, 'cat', '/data/access.txt')
        temporary = re.search(r'^Temporary password:\s*(.+)$', access, re.M).group(1)
        session = request('/auth/login', {'username': 'synthetic-admin', 'password': temporary})
        token = session['token']
        password = secrets.token_urlsafe(24)
        request('/auth/password', {'current_password': temporary, 'new_password': password})
        token = request('/auth/login', {'username': 'synthetic-admin', 'password': password})['token']
        plan = request('/plans', {'terminal': 'cua_lo', 'period_type': 'month', 'month': '2026-09',
                                  'metric': 'tonnage', 'amount': '123', 'reference': 'SYNTHETIC CONTAINER TEST'})
        docker('restart', name)
        base = 'http://' + docker('port', name, '8000/tcp').splitlines()[0] + '/api'
        ready()
        assert request('/auth/me')['username'] == 'synthetic-admin'
        plans = request('/plans')['items']
        assert any(item['id'] == plan['id'] and item['amount'] == 123 for item in plans)
        docker('exec', '--user', '10001', name, 'python', 'state_backup.py', 'backup', '--output', '/data/backups/check.sqlite3')
        verification = json.loads(docker('exec', '--user', '10001', name, 'python', 'state_backup.py', 'verify', '--backup', '/data/backups/check.sqlite3'))
        assert verification['restore_verified']
        print(json.dumps({'status': 'passed', 'data': 'synthetic isolated Docker volume only',
                          'checks': ['image starts', 'non-root runtime', 'login/password flow',
                                     'session and monthly plan survive restart without SQL', 'online backup and restore verification']}))
    finally:
        if running:
            docker('rm', '-f', name)
        docker('volume', 'rm', volume)


if __name__ == '__main__':
    main()

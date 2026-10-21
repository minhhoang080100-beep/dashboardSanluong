from pathlib import Path

import pytest

from scripts.prepare_corporate_api_deploy import API_COMMAND, LEGACY_COMMAND, prepare


def test_context_excludes_private_and_unrelated_files(tmp_path):
    backend = tmp_path / 'backend'
    backend.mkdir()
    files = {
        'Dockerfile': 'COPY requirements.txt .\nCOPY config.py ./\nCOPY corporate_api ./corporate_api\n' + LEGACY_COMMAND + '\n',
        'requirements.txt': 'fastapi\n', '.dockerignore': '.env\n.data\n',
        'config.py': '',
        'corporate_api/app.py': 'app = None\n', 'corporate_api/auth.py': '',
        '.env': 'SECRET=private', '.data/corporate.sqlite3': 'private',
        'corporate_api/private.json': 'private', 'corporate_api/__pycache__/auth.pyc': 'private',
        'audit-output.json': 'private', 'local_diagnostic.py': 'private',
    }
    for name, content in files.items():
        path = backend / name
        path.parent.mkdir(exist_ok=True, parents=True)
        path.write_text(content)
    output = tmp_path / 'release'
    manifest = prepare(backend, output)
    assert set(manifest) == {'Dockerfile', '.dockerignore', 'requirements.txt', 'config.py',
                             'corporate_api/app.py', 'corporate_api/auth.py'}
    assert API_COMMAND in (output / 'Dockerfile').read_text()
    assert LEGACY_COMMAND not in (output / 'Dockerfile').read_text()
    assert LEGACY_COMMAND in (backend / 'Dockerfile').read_text()
    assert not any('private' in p.read_text() for p in output.rglob('*') if p.is_file())
    assert (output / 'deployment-manifest.json').is_file()
    with pytest.raises(FileExistsError):
        prepare(backend, output)


def test_context_cannot_be_nested_under_backend(tmp_path):
    backend = tmp_path / 'backend'
    backend.mkdir()
    with pytest.raises(ValueError, match='outside'):
        prepare(backend, backend / 'release')

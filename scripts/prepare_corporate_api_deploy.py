"""Prepare a data-free build context for the separate Railway API service.

Usage: python scripts/prepare_corporate_api_deploy.py --output outputs/api-release
The target directory must not exist. This command does not deploy anything.
"""
import argparse
import hashlib
import json
from pathlib import Path
import re
import shutil

LEGACY_COMMAND = 'CMD ["python", "-m", "corporate_api.service"]'
API_COMMAND = ('CMD ["python", "-m", "uvicorn", "corporate_api.app:app", '
               '"--host", "0.0.0.0", "--port", "8000", "--no-access-log"]')


def prepare(backend: Path, output: Path):
    backend = backend.resolve(strict=True)
    output = output.resolve()
    if output == backend or backend in output.parents:
        raise ValueError('Build context must be outside the backend source directory.')
    files = {'Dockerfile', '.dockerignore', 'requirements.txt'}
    dockerfile = (backend / 'Dockerfile').read_text(encoding='utf-8')
    if dockerfile.count(LEGACY_COMMAND) != 1:
        raise ValueError('Docker runtime command changed; review the API deployment command.')
    for line in dockerfile.splitlines():
        if line.startswith('COPY ') and line.endswith(' ./'):
            for name in line.split()[1:-1]:
                if not re.fullmatch(r'[A-Za-z0-9_.-]+', name) or name.startswith('.'):
                    raise ValueError('Unsupported Docker COPY input; review the deployment allowlist.')
                files.add(name)
    sources = [(backend / name, name) for name in sorted(files)]
    sources.extend((path, 'corporate_api/' + path.name)
                   for path in sorted((backend / 'corporate_api').glob('*.py')))
    for path, _ in sources:
        if path.is_symlink() or not path.is_file() or backend not in path.resolve().parents:
            raise ValueError('Build input must be an ordinary file within backend.')
    if not (backend / 'corporate_api' / 'app.py').is_file():
        raise ValueError('The dedicated corporate API app is missing.')
    # Deliberately fail on an existing destination, including populated releases.
    output.mkdir(parents=True, exist_ok=False)
    hashes = {}
    for source, relative in sources:
        target = output / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        if relative == 'Dockerfile':
            target.write_text(dockerfile.replace(LEGACY_COMMAND, API_COMMAND), encoding='utf-8')
        else:
            shutil.copyfile(source, target)
        hashes[relative] = hashlib.sha256(target.read_bytes()).hexdigest()
    (output / 'deployment-manifest.json').write_text(json.dumps(hashes, indent=2), encoding='utf-8')
    return hashes


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', required=True, type=Path)
    args = parser.parse_args()
    try:
        files = prepare(Path(__file__).resolve().parents[1] / 'backend', args.output)
    except (OSError, ValueError) as exc:
        parser.exit(1, f'Cannot prepare build context: {exc}\n')
    print(f'Prepared {len(files)} application files in {args.output}. No credentials or source data included.')


if __name__ == '__main__':
    main()

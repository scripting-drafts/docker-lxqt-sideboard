#!/usr/bin/env bash
# Run source/archive tests without touching a configured installation.
set -Eeuo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
export LXQT_PROJECT_ROOT="$ROOT"
source "$ROOT/scripts/cpu-affinity.sh"
lxqt_cpu_guard "$(lxqt_cpu_set)"
python3 - "$ROOT" "$@" <<'PY'
from pathlib import Path
import os
import shutil
import subprocess
import sys
import tempfile

root = Path(sys.argv[1])
temporary_root = '/tmp/opencode' if Path('/tmp/opencode').is_dir() else None
with tempfile.TemporaryDirectory(prefix='lxqt-tests-', dir=temporary_root) as directory:
    target = Path(directory)
    for name in ('Dockerfile', 'Dockerfile.overlay', 'install.sh', 'start-desktop.sh', 'README.md',
                 '.gitignore', '.dockerignore'):
        shutil.copy2(root / name, target / name)
    for folder, patterns in (
        ('scripts', ('*.sh', '*.py')),
        ('tests', ('*.py',)),
        ('docs', ('*.md',)),
        ('examples', ('*.yaml',)),
    ):
        (target / folder).mkdir()
        for pattern in patterns:
            for path in (root / folder).glob(pattern):
                if path.is_symlink():
                    raise SystemExit(f'Refusing symlink in test source: {path.relative_to(root)}')
                shutil.copy2(path, target / folder / path.name)
    (target / 'config').mkdir()
    shutil.copy2(root / 'config/settings.example.json', target / 'config/settings.example.json')
    (target / 'generated').mkdir()
    print('Running tests in an isolated copy; live config, secrets and backups excluded.', flush=True)
    result = subprocess.run(
        [sys.executable, '-m', 'unittest', 'discover', '-s', 'tests', '-v', *sys.argv[2:]],
        cwd=target, env=dict(os.environ), timeout=180,
    )
    sys.exit(result.returncode)
PY

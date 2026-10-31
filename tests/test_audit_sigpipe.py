"""Regression tests for pipefail/SIGPIPE in long APT candidate tables."""
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]

class AuditSigpipeTests(unittest.TestCase):
    def test_large_apt_version_table_does_not_abort_inventory(self):
        with tempfile.TemporaryDirectory() as temp:
            home = Path(temp)
            bin_dir = home / '.local/bin'
            bin_dir.mkdir(parents=True)
            fake = bin_dir / 'apt-cache'
            fake.write_text('''#!/bin/bash
printf 'Package: %s\\n  Installed: (none)\\n  Candidate: 5:29.0.0-1\\n  Version table:\\n' "$2"
if [[ "$2" == docker-ce ]]; then
  awk 'BEGIN { for (i=0;i<90000;i++) print "   5:29.0.0-1 500 https://example.invalid/ubuntu stable amd64 Packages" }'
fi
''')
            fake.chmod(0o755)
            proc = subprocess.run(['bash', str(ROOT / 'scripts/audit-host.sh')],
                                  env={**os.environ, 'HOME': str(home)},
                                  capture_output=True, text=True, timeout=20)
            self.assertEqual(proc.returncode, 0, proc.stderr)
            self.assertIn('docker-ce ', proc.stdout)
            self.assertIn('python3-yaml ', proc.stdout)
            self.assertIn('Read-only inventory', proc.stdout)

    def test_apt_query_failure_is_reported_and_inventory_continues(self):
        with tempfile.TemporaryDirectory() as temp:
            home = Path(temp)
            bin_dir = home / '.local/bin'
            bin_dir.mkdir(parents=True)
            fake = bin_dir / 'apt-cache'
            fake.write_text('#!/bin/bash\nprintf "APT metadata unavailable\\n" >&2\nexit 100\n')
            fake.chmod(0o755)
            proc = subprocess.run(['bash', str(ROOT / 'scripts/audit-host.sh')],
                                  env={**os.environ, 'HOME': str(home)},
                                  capture_output=True, text=True, timeout=20)
            self.assertEqual(proc.returncode, 0, proc.stderr)
            self.assertIn('CHECK_APT', proc.stdout)
            self.assertIn('Read-only inventory', proc.stdout)

    def test_docker_installer_avoids_early_awk_exit(self):
        body = (ROOT / 'scripts/install-docker.sh').read_text()
        self.assertIn("$1==\"Candidate:\" && !found++ {print $2}", body)
        self.assertNotIn("'/Candidate:/ {print $2; exit}'", body)
        self.assertIn('ERROR: Could not query APT candidate', body)

if __name__ == '__main__':
    unittest.main()

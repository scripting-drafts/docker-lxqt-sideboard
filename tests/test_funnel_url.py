"""Check the Funnel URL helper appends the noVNC path."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


class FunnelUrlTests(unittest.TestCase):
    def test_appends_vnc_html_and_accepts_a_path(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / 'scripts').mkdir()
            shutil.copy2(ROOT / 'scripts/funnel-url.sh', root / 'scripts/funnel-url.sh')
            (root / 'config').mkdir()
            (root / 'config/settings.json').write_text(json.dumps({'namespace': 'test'}))
            (root / 'config/kubeconfig').write_text('fixture')
            (root / 'bin').mkdir()
            kubectl = root / 'bin/kubectl'
            kubectl.write_text(
                '#!/bin/sh\n'
                'printf "%s\\n" "# Funnel on:" "# https://browser.example.ts.net"\n'
            )
            kubectl.chmod(0o755)
            environment = dict(os.environ, PATH=str(root / 'bin') + ':' + os.environ['PATH'])
            first = subprocess.run(
                ['bash', str(root / 'scripts/funnel-url.sh')],
                env=environment, capture_output=True, text=True, timeout=10,
            )
            self.assertEqual(first.returncode, 0, first.stderr)
            self.assertEqual(first.stdout.strip(), 'https://browser.example.ts.net/vnc.html')
            second = subprocess.run(
                ['bash', str(root / 'scripts/funnel-url.sh'), 'vnc_lite.html'],
                env=environment, capture_output=True, text=True, timeout=10,
            )
            self.assertEqual(second.returncode, 0, second.stderr)
            self.assertEqual(second.stdout.strip(), 'https://browser.example.ts.net/vnc_lite.html')


if __name__ == '__main__':
    unittest.main()

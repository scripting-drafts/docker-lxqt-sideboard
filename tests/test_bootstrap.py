"""Non-mutating checks for the host installer. Does not install packages."""
from pathlib import Path
import re
import subprocess
import unittest

ROOT = Path(__file__).resolve().parents[1]

class BootstrapStaticTests(unittest.TestCase):
    def test_scripts_parse_as_bash(self):
        for path in ('scripts/bootstrap-host.sh','scripts/install-docker.sh','scripts/install-all.sh','install.sh','scripts/install-python.sh','scripts/verify-tun.sh','scripts/audit-host.sh'):
            with self.subTest(path=path):
                subprocess.run(['bash','-n',str(ROOT/path)],check=True)

    def test_python_digest_is_pinned(self):
        script = (ROOT/'scripts/install-python.sh').read_text()
        self.assertIn('3.15.2', script)
        self.assertIn('8b5536d280142ecf6cdfe15d12a21ba1c65e300bbf7b28a8d0cdb849575797de',script)
        self.assertIn('sha256sum -c -',script)
        self.assertIn('LXQT_PYYAML_VERSION:-6.0.3',script)
        self.assertIn('PyYAML==$PYYAML_VERSION',script)
        self.assertIn('alias python=',script)
        self.assertNotIn('ln -sfn "$VENV/bin/python" /usr/bin/', script)

    def test_docker_safety(self):
        script=(ROOT/'scripts/bootstrap-host.sh').read_text()
        self.assertIn('--dry-run',script)
        self.assertIn('root-equivalent',script)
        docker_installer=(ROOT/'scripts/install-docker.sh').read_text()
        self.assertIn('Docker apt key fingerprint mismatch',docker_installer)
        self.assertIn('--repair-docker',docker_installer)
        self.assertIn('docker.socket',docker_installer)
        self.assertIn('docker --context default info',docker_installer)
        self.assertIn('scripts/install-docker.sh',script)
        self.assertIn('sha256sum -c',script)
        self.assertIn('python3-yaml',script)
        self.assertIn('--add-docker-group',script)

    def test_tun_test_is_isolated(self):
        script=(ROOT/'scripts/verify-tun.sh').read_text()
        self.assertIn('--network none',script)
        self.assertIn('--cap-add NET_ADMIN',script)
        self.assertIn('--device /dev/net/tun',script)
        self.assertIn('TUNSETIFF',script)
        self.assertIn('TUNGETFEATURES',script)

if __name__=='__main__':
    unittest.main()

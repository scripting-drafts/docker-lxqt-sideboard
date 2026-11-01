"""Synthetic Caddy patch/apply tests: never use Docker or Kubernetes credentials."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
CONFIG = '''{
    admin off
    auto_https off
}
127.0.0.1:8081 {
    basic_auth argon2id {
        viewer $argon2id$synthetic-test-fixture-not-a-real-hash
    }
    reverse_proxy http://127.0.0.1:6080
}
'''


class CaddyPatchTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        for folder in ('scripts', 'config/secrets', 'bin'):
            (self.root / folder).mkdir(parents=True)
        for name in ('fix-caddy.sh', 'cpu-affinity.sh', 'shared_space.py'):
            shutil.copy2(ROOT / 'scripts' / name, self.root / 'scripts' / name)
        settings = json.loads((ROOT / 'config/settings.example.json').read_text())
        (self.root / 'config/settings.json').write_text(json.dumps(settings))
        self.config = self.root / 'config/Caddyfile'
        self.config.write_text(CONFIG)
        self.log = self.root / 'commands.log'
        docker = self.root / 'bin/docker'
        docker.write_text('''#!/usr/bin/env python3
import os, sys
with open(os.environ['COMMAND_LOG'], 'a') as f: f.write('docker ' + ' '.join(sys.argv[1:]) + '\\n')
data=sys.stdin.read()
if 'fmt' in sys.argv: sys.stdout.write(data)
if 'adapt' in sys.argv and os.environ.get('FAIL_ADAPT'): sys.exit(9)
''')
        docker.chmod(0o755)
        kubectl = self.root / 'bin/kubectl'
        kubectl.write_text('''#!/usr/bin/env python3
import os, sys
with open(os.environ['COMMAND_LOG'], 'a') as f: f.write('kubectl ' + ' '.join(sys.argv[1:]) + '\\n')
if 'create' in sys.argv: print('kind: Secret')
if 'apply' in sys.argv: sys.stdin.read()
''')
        kubectl.chmod(0o755)
        self.environment = dict(os.environ, PATH=str(self.root / 'bin') + ':' + os.environ['PATH'],
                                COMMAND_LOG=str(self.log),
                                LXQT_ALLOWED_CPUS=str(min(os.sched_getaffinity(0) - {2, 8})))

    def run_patch(self, *arguments):
        return subprocess.run(['bash', str(self.root / 'scripts/fix-caddy.sh'), *arguments],
                              env=self.environment, capture_output=True, text=True, timeout=20)

    def test_patch_preserves_authentication_and_is_idempotent(self):
        first = self.run_patch()
        self.assertEqual(first.returncode, 0, first.stderr)
        patched = self.config.read_text()
        self.assertIn('http://:8081 {\n\tbind 127.0.0.1', patched)
        self.assertIn('protocols h1 h2', patched)
        self.assertIn('redir / /vnc.html 302', patched)
        self.assertIn('@shared path /shared /shared/*', patched)
        self.assertIn('reverse_proxy http://127.0.0.1:8082', patched)
        self.assertIn('$argon2id$synthetic-test-fixture-not-a-real-hash', patched)
        self.assertNotIn('kubectl', self.log.read_text())
        self.assertEqual(self.config.stat().st_mode & 0o777, 0o600)
        second = self.run_patch()
        self.assertEqual(second.returncode, 0, second.stderr)
        self.assertEqual(self.config.read_text(), patched)
        self.assertEqual(len(list((self.root / 'config/secrets').glob('Caddyfile.backup.*'))), 1)

    def test_validation_failure_preserves_original_file(self):
        self.environment['FAIL_ADAPT'] = '1'
        result = self.run_patch()
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.config.read_text(), CONFIG)
        self.assertFalse(list((self.root / 'config/secrets').glob('Caddyfile.backup.*')))
        self.assertNotIn('kubectl', self.log.read_text())

    def test_apply_keeps_all_required_secret_entries(self):
        for name in ('wireguard_private_key', 'tailscale_authkey', 'vnc_password'):
            (self.root / 'config/secrets' / name).write_text('synthetic fixture\n')
        (self.root / 'config/kubeconfig').write_text('synthetic fixture\n')
        result = self.run_patch('--apply')
        self.assertEqual(result.returncode, 0, result.stderr)
        log = self.log.read_text()
        for name in ('wireguard_private_key', 'tailscale_authkey', 'vnc_password', 'Caddyfile'):
            self.assertIn('--from-file=' + name + '=config/', log)
        self.assertIn('rollout restart deployment/lxqt-desktop', log)
        self.assertNotIn('synthetic fixture', log)

    def test_unexpected_listener_is_not_rewritten(self):
        original = CONFIG.replace('127.0.0.1:8081', 'https://custom.example:8443')
        self.config.write_text(original)
        result = self.run_patch()
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.config.read_text(), original)

    def test_future_configuration_uses_explicit_http_loopback(self):
        source = (ROOT / 'scripts/configure.py').read_text()
        self.assertIn('http://:8081 {\\n    bind 127.0.0.1', source)
        self.assertIn('patch-caddy', source)
        shared = (ROOT / 'scripts/shared_space.py').read_text()
        self.assertIn('@shared path /shared /shared/*', shared)


if __name__ == '__main__':
    unittest.main()

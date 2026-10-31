"""Idempotent reconciliation regression checks; no changes to the test host."""
from pathlib import Path
import subprocess
import unittest
ROOT = Path(__file__).resolve().parents[1]

class ReconcileTests(unittest.TestCase):
    def test_audit_is_read_only_and_executable(self):
        script = ROOT / 'scripts/audit-host.sh'
        body = script.read_text()
        for name in ('docker-ce','docker-compose-plugin','kind','kubectl','wireguard-tools',
                     'python3-yaml','/dev/net/tun','Project PyYAML'):
            self.assertIn(name, body)
        for forbidden in ('apt-get install', 'apt-get upgrade', 'sudo systemctl restart', 'docker build', 'docker pull'):
            self.assertNotIn(forbidden, body)
        result = subprocess.run([str(script)], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('INSTALLED', result.stdout)
        self.assertIn('Read-only inventory', result.stdout)

    def test_docker_reconciles_candidate_versions_and_reinstalls_broken_plugins(self):
        script = (ROOT / 'scripts/install-docker.sh').read_text()
        self.assertIn("dpkg --compare-versions", script)
        self.assertIn('updates+=("$p")', script)
        self.assertIn('repair_plugins+=(docker-compose-plugin)', script)
        self.assertIn('repair_plugins+=(docker-buildx-plugin)', script)
        self.assertIn('apt-get -s install', script)
        self.assertIn("grep -Eq '^Remv '", script)
        self.assertIn('Refusing automated package replacement', script)
        self.assertIn('--repair-docker', script)

    def test_version_changes_replaced_with_verified_kind_and_kubectl(self):
        script = (ROOT / 'scripts/bootstrap-host.sh').read_text()
        self.assertIn('"$kind_current" != "$KIND_VERSION"', script)
        self.assertIn('"$K_INSTALLED" != "$K_VERSION"', script)
        self.assertIn('sha256sum -c', script)
        self.assertIn('kind.lxqt-backup-', script)
        self.assertIn('kubectl.lxqt-backup-', script)
        self.assertIn('scripts/audit-host.sh', script)

    def test_python_repairs_invalid_project_env_but_not_system_python(self):
        script = (ROOT / 'scripts/install-python.sh').read_text()
        self.assertIn('"$installed_python_version" != "$PYTHON_VERSION"', script)
        self.assertIn('"$current_yaml" != "$PYYAML_VERSION"', script)
        self.assertIn('PyYAML==$PYYAML_VERSION', script)
        self.assertIn('lxqt-backup-', script)
        self.assertNotIn('sudo rm -rf', script)
        self.assertNotIn('update-alternatives', script)
        self.assertNotIn('sudo make install', script)

    def test_main_installer_audits_first(self):
        script = (ROOT / 'scripts/install-all.sh').read_text()
        self.assertLess(script.index('Audit and reconcile Docker'), script.index('"$ROOT/scripts/deploy.sh"'))
        self.assertIn('WIREGUARD_CONFIG=""', script)
        self.assertIn('"$ROOT/scripts/bootstrap-host.sh" --dry-run', script)

if __name__ == '__main__':
    unittest.main()

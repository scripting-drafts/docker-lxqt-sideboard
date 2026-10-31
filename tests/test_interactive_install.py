"""Safe behavioral tests for zero-argument installer. Never install host software."""
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


class InstallerPromptTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='lxqt-install-test-')
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name)
        self.project = self.base / 'project'
        self.scripts = self.project / 'scripts'
        self.scripts.mkdir(parents=True)
        self.home = self.base / 'home'
        binpath = self.home / '.local' / 'bin'
        binpath.mkdir(parents=True)
        shutil.copy2(ROOT / 'install.sh', self.project / 'install.sh')
        shutil.copy2(ROOT / 'scripts/install-all.sh', self.scripts / 'install-all.sh')
        self.write_exe(self.scripts / 'cpu-affinity.sh', '''#!/bin/bash
lxqt_cpu_set() { printf '0,1,3-7,9-11\n'; }
lxqt_cpu_guard() { :; }
lxqt_cpu_expand() { :; }
''')
        # Unconditionally stop at bootstrap and report arguments: no apt, sudo, Docker or network runs.
        self.write_exe(self.scripts / 'bootstrap-host.sh', '#!/bin/bash\nprintf "BOOTSTRAP-ARGS: %s\\n" "$*"\n[[ "$1" == --dry-run ]] && exit 0\nexit 73\n')
        self.write_exe(self.scripts / 'install-docker.sh', '#!/bin/bash\necho DOCKER-DRYRUN\n')
        self.write_exe(binpath / 'id', '''#!/bin/bash
case "$1" in
  -u) echo 1000 ;;
  -un) echo tester ;;
  -nG) echo "tester adm ${MOCK_GROUP:-}" ;;
  *) exec /usr/bin/id "$@" ;;
esac
''')
        self.write_exe(binpath / 'docker', '''#!/bin/bash
[ "${MOCK_DOCKER:-1}" = 1 ]
''')
        self.env = dict(os.environ, HOME=str(self.home),
                        PATH=str(binpath) + os.pathsep + os.environ['PATH'], MOCK_DOCKER='1')

    @staticmethod
    def write_exe(path, script):
        path.write_text(script)
        path.chmod(0o755)

    def run_installer(self, *args, stdin=''):
        return subprocess.run([str(self.project / 'install.sh'), *args], cwd=self.base,
                              env=self.env, input=stdin, capture_output=True, text=True, timeout=10)

    def test_default_file_no_prompts_or_parameters(self):
        path = self.home / 'Documents' / 'wireguard.conf'
        path.parent.mkdir()
        path.write_text('test fixture, not valid WG key\n')
        result = self.run_installer()
        self.assertEqual(result.returncode, 73, result.stderr)
        self.assertIn('BOOTSTRAP-ARGS:', result.stdout)
        self.assertNotIn('Enter the path', result.stdout)
        self.assertNotIn('Authorize Docker-group', result.stdout)

    def test_missing_default_prompts_and_accepts_absolute_filename_with_spaces(self):
        path = self.base / 'proton vpn US.conf'
        path.write_text('test fixture')
        result = self.run_installer(stdin=f'{path}\n')
        self.assertEqual(result.returncode, 73, result.stderr)
        self.assertIn('Enter the path', result.stdout)
        self.assertIn('BOOTSTRAP-ARGS:', result.stdout)

    def test_missing_default_retries_and_can_expand_tilde(self):
        path = self.home / 'other.conf'
        path.write_text('test fixture')
        result = self.run_installer(stdin='bad path.conf\n~/other.conf\n')
        self.assertEqual(result.returncode, 73, result.stderr)
        self.assertIn('File not found', result.stderr)

    def test_missing_default_no_input_exits_before_bootstrap(self):
        result = self.run_installer()
        self.assertEqual(result.returncode, 2)
        self.assertIn('Cancelled', result.stderr)
        self.assertNotIn('BOOTSTRAP-ARGS', result.stdout)

    def test_missing_explicit_argument_does_not_prompt(self):
        result = self.run_installer('--wireguard-config', 'missing.conf')
        self.assertEqual(result.returncode, 1)
        self.assertNotIn('Enter the path', result.stdout)
        self.assertNotIn('BOOTSTRAP-ARGS', result.stdout)

    def test_docker_group_requested_only_after_explicit_yes(self):
        path = self.home / 'Documents' / 'wireguard.conf'
        path.parent.mkdir()
        path.write_text('test fixture')
        self.env['MOCK_DOCKER'] = '0'
        result = self.run_installer(stdin='yes\n')
        self.assertEqual(result.returncode, 73, result.stderr)
        self.assertIn('ROOT-EQUIVALENT', result.stdout)
        self.assertIn('BOOTSTRAP-ARGS: --add-docker-group', result.stdout)

    def test_docker_group_refusal_leaves_host_untouched(self):
        path = self.home / 'Documents' / 'wireguard.conf'
        path.parent.mkdir()
        path.write_text('test fixture')
        self.env['MOCK_DOCKER'] = '0'
        result = self.run_installer(stdin='n\n')
        self.assertEqual(result.returncode, 1)
        self.assertIn('declined', result.stderr)
        self.assertNotIn('BOOTSTRAP-ARGS', result.stdout)

    def test_existing_group_membership_does_not_require_reapproval(self):
        path = self.home / 'Documents' / 'wireguard.conf'
        path.parent.mkdir()
        path.write_text('test fixture')
        self.env['MOCK_DOCKER'] = '0'
        self.env['MOCK_GROUP'] = 'docker'
        result = self.run_installer()
        self.assertEqual(result.returncode, 73, result.stderr)
        self.assertIn('BOOTSTRAP-ARGS: --add-docker-group', result.stdout)
        self.assertNotIn('Authorize Docker-group', result.stdout)

    def test_dryrun_missing_file_is_noninteractive(self):
        result = self.run_installer('--dry-run')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('BOOTSTRAP-ARGS: --dry-run', result.stdout)
        self.assertIn('DOCKER-DRYRUN', result.stdout)
        self.assertIn('installer will ask for a path', result.stdout)
        self.assertNotIn('Enter the path', result.stdout)


if __name__ == '__main__':
    unittest.main()

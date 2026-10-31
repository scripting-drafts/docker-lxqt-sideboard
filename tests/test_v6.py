"""Offline regression checks: host installer merge, orchestrator and Docker hash failure."""
import importlib.util
import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]


class V6Tests(unittest.TestCase):
    def test_all_install_entrypoints_validate_and_help(self):
        for path in ['install.sh', 'scripts/install-all.sh', 'scripts/install-docker.sh', 'scripts/bootstrap-host.sh']:
            subprocess.run(['bash', '-n', str(ROOT / path)], check=True)
        res = subprocess.run([str(ROOT / 'install.sh'), '--help'], capture_output=True, text=True, check=True)
        for flag in ('--wireguard-config', '--host-profile', '--add-docker-group', '--repair-docker',
                     '--bootstrap-cpus', '--skip-timers', '--no-funnel', '--dry-run'):
            self.assertIn(flag, res.stdout)

    def test_kind_is_on_path_and_checked_before_deploy(self):
        top = (ROOT / 'install.sh').read_text()
        installer = (ROOT / 'scripts/install-all.sh').read_text()
        bootstrap = (ROOT / 'scripts/bootstrap-host.sh').read_text()
        self.assertIn('export PATH="/usr/local/bin:$HOME/.local/bin:$PATH"', top)
        self.assertIn('export PATH="/usr/local/bin:$HOME/.local/bin:$PATH"', installer)
        self.assertIn('export PATH="/usr/local/bin:$HOME/.local/bin:$PATH"', bootstrap)
        self.assertIn('sudo install -m 0755 "$TMP/$ASSET" /usr/local/bin/kind', bootstrap)
        self.assertIn("kind version >/dev/null", installer)
        self.assertIn('# LXQt Kind Kubernetes CLI path', installer)
        self.assertIn('~/.bashrc', (ROOT / 'README.md').read_text())
        self.assertLess(installer.index('kind version >/dev/null'), installer.index('"$ROOT/scripts/deploy.sh"'))
        self.assertLess(installer.index('kind version >/dev/null'), installer.index('"$ROOT/scripts/enable-funnel.sh"'))

    def test_explicit_wireguard_filename_and_profile_independence(self):
        installer = (ROOT / 'scripts/install-all.sh').read_text()
        setup = (ROOT / 'scripts/setup-proton.sh').read_text()
        wizard = (ROOT / 'scripts/configure.py').read_text()
        self.assertIn('WIREGUARD_CONFIG=""', installer)
        self.assertIn('CONFIG_FILE=""', setup)
        self.assertNotIn('LXQT_WIREGUARD_CONFIG:-', installer)
        self.assertNotIn('LXQT_WIREGUARD_CONFIG:-', setup)
        self.assertNotIn("'Documents' / 'wireguard.conf'", wizard)
        self.assertIn('LXQT_HOST_PROFILE:-', installer)
        self.assertIn('LXQT_HOST_PROFILE:-', setup)
        self.assertIn('--wireguard-config FILE', installer)
        self.assertIn('WIREGUARD_CONFIG="$CALLER_DIR/$WIREGUARD_CONFIG"', installer)
        self.assertIn('restart=("$ROOT/scripts/install-all.sh" --resume-after-group --wireguard-config "$WIREGUARD_CONFIG")', installer)
        self.assertIn('WG_FROM_DEFAULT=1', installer)
        self.assertIn('Enter the path to your downloaded Proton WireGuard', installer)
        self.assertLess(installer.index('No Proton WireGuard configuration found at'), installer.index('Audit and reconcile Docker, Kind, kubectl, Python/PyYAML, WireGuard and TUN'))
        self.assertIn('~/Documents/wireguard.conf', (ROOT/'docs/PROTON-WIREGUARD.md').read_text())

    def test_missing_wireguard_argument_is_rejected_before_install(self):
        for args in [['--wireguard-config'], ['--wireguard-config', '--no-funnel']]:
            res = subprocess.run([str(ROOT / 'install.sh'), *args], capture_output=True, text=True)
            self.assertEqual(res.returncode, 2, (args, res.stderr))
            self.assertIn('wireguard-config', res.stderr)
        res = subprocess.run([str(ROOT / 'scripts/setup-proton.sh')], capture_output=True, text=True)
        self.assertEqual(res.returncode, 2)
        self.assertIn('--config FILE is required', res.stderr)

    def test_relative_wireguard_filename_is_resolved_from_invocation_directory(self):
        with tempfile.TemporaryDirectory() as td:
            # Simulate an ordinary nonroot shell even when CI itself runs as root.
            # The installer must stop on a missing file before any privileged action.
            fake_id = Path(td) / 'id'
            fake_id.write_text('#!/bin/sh\nif [ "$1" = -u ]; then echo 1000; else exec /usr/bin/id "$@"; fi\n')
            fake_id.chmod(0o755)
            import os
            env = {**os.environ, 'PATH': td + os.pathsep + os.environ['PATH']}
            name = 'proton profile.conf'
            res = subprocess.run([str(ROOT / 'install.sh'), '--wireguard-config', name],
                                 cwd=td, env=env, capture_output=True, text=True)
            self.assertEqual(res.returncode, 1)
            self.assertIn(str(Path(td) / name), res.stderr)
            self.assertNotIn('Install or repair Docker', res.stdout)
            same = subprocess.run([str(ROOT / 'install.sh'), name], cwd=td, env=env,
                                  capture_output=True, text=True)
            self.assertIn(str(Path(td) / name), same.stderr)
            self.assertEqual(same.returncode, 1)

    def test_uploaded_installer_replaces_old_weak_cli_check(self):
        installer = (ROOT / 'scripts/install-docker.sh').read_text()
        bootstrap = (ROOT / 'scripts/bootstrap-host.sh').read_text()
        self.assertIn('engine_present()', installer)
        self.assertIn('docker.socket', installer)
        self.assertIn('--repair-docker', installer)
        self.assertIn('Docker apt key fingerprint mismatch', installer)
        self.assertIn('"$ROOT/scripts/install-docker.sh"', bootstrap)
        self.assertNotIn('if ! command -v docker >/dev/null || ! docker compose version', bootstrap)

    def test_caddy_hash_failure_is_actionable_and_doesnt_traceback(self):
        spec = importlib.util.spec_from_file_location('configure_v6', ROOT / 'scripts/configure.py')
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / 'config').mkdir()
            shutil.copyfile(ROOT / 'config/settings.example.json', root / 'config/settings.example.json')
            module.ROOT = root
            module.CONF = root / 'config'
            module.SETTINGS = root / 'config/settings.json'
            module.SECRET_DIR = root / 'config/secrets'
            passwords = iter(['A' * 43 + '=', 'tskey-auth-test', 'abcd1234', 'yes',
                              'thisisalongtestpassphrase', 'thisisalongtestpassphrase'])
            import contextlib
            import io
            stderr = io.StringIO()
            from types import SimpleNamespace
            with patch('builtins.input', return_value=''), \
                 patch.object(module.getpass, 'getpass', side_effect=lambda _: next(passwords)), \
                 patch.object(module.subprocess, 'run', return_value=SimpleNamespace(returncode=125, stdout='', stderr='daemon down')), \
                 patch.object(module.sys, 'argv', ['configure.py']), contextlib.redirect_stderr(stderr):
                result = module.main()
            self.assertEqual(result, 1)
            self.assertIn('docker info', stderr.getvalue())
            self.assertNotIn('thisisalongtestpassphrase', stderr.getvalue())
            self.assertFalse((root / 'config/settings.json').exists())


if __name__ == '__main__':
    unittest.main()

"""Synthetic recovery deadline and narrowly scoped repair tests."""
import importlib.util
import json
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('recovery', ROOT / 'scripts/recover-vpn.py')
recovery = importlib.util.module_from_spec(spec)
spec.loader.exec_module(recovery)


class RecoveryTests(unittest.TestCase):
    def setUp(self):
        self.now = 0
        self.calls = []
        self.public_count = 0
        self.settings = {'namespace': 'test', 'vpn_recovery_timeout_seconds': 45}

    def sleep(self, seconds):
        self.now += seconds

    def result(self, args, code=0, stdout=''):
        return subprocess.CompletedProcess(args, code, stdout, '')

    def healthy_runner(self, args, timeout):
        self.calls.append(args)
        self.now += min(0.1, timeout)
        if 'funnel' in args:
            return self.result(args, stdout='# Funnel on:\nhttps://test.example.ts.net\n')
        if 'https://api.ipify.org' in args:
            return self.result(args, stdout='198.51.100.1')
        if '--write-out' in args:
            return self.result(args, stdout='401')
        return self.result(args)

    def recover(self, **kwargs):
        return recovery.recover(self.settings, '/fixture/kubeconfig',
                                clock=lambda: self.now, sleep=self.sleep, emit=lambda line: None,
                                **kwargs)

    def test_default_target_is_45_seconds(self):
        example = json.loads((ROOT / 'config/settings.example.json').read_text())
        self.assertEqual(example['vpn_recovery_timeout_seconds'], 45)
        self.assertEqual(recovery.DEFAULT_TIMEOUT, 45)

    def test_healthy_vpn_needs_no_restart(self):
        result = self.recover(runner=self.healthy_runner)
        self.assertTrue(result['success'])
        self.assertFalse(result['tailscale_restart_requested'])
        self.assertFalse(any('kill -TERM 1' in call for call in self.calls))

    def test_unavailable_vpn_fails_by_deadline_without_mutation(self):
        def unavailable(args, timeout):
            self.calls.append(args)
            self.now += min(6, timeout)
            return self.result(args, 1)
        result = self.recover(runner=unavailable, repair_funnel=True)
        self.assertFalse(result['success'])
        self.assertEqual(self.now, 45)
        self.assertFalse(result['tailscale_restart_requested'])
        self.assertTrue(all('/gluetun-entrypoint' in call for call in self.calls))

    def test_funnel_repair_only_after_vpn_recovers(self):
        def runner(args, timeout):
            result = self.healthy_runner(args, timeout)
            if '--write-out' in args:
                self.public_count += 1
                return self.result(args, 35 if self.public_count <= 2 else 0,
                                   '000' if self.public_count <= 2 else '401')
            return result
        result = self.recover(runner=runner, repair_funnel=True)
        self.assertTrue(result['success'])
        restarts = [call for call in self.calls if 'kill -TERM 1' in call]
        self.assertEqual(len(restarts), 1)
        self.assertIn('tailscale', restarts[0])
        self.assertLess(result['elapsed_seconds'], 45)

    def test_single_transient_funnel_failure_does_not_restart_tailscale(self):
        def runner(args, timeout):
            result = self.healthy_runner(args, timeout)
            if '--write-out' in args:
                self.public_count += 1
                if self.public_count == 1:
                    return self.result(args, 35, '000')
            return result
        result = self.recover(runner=runner, repair_funnel=True)
        self.assertTrue(result['success'])
        self.assertTrue(result['vpn_target_met'])
        self.assertTrue(result['funnel_verified'])
        self.assertFalse(result['tailscale_restart_requested'])

    def test_normal_funnel_handshake_is_not_limited_to_two_seconds(self):
        def runner(args, timeout):
            result = self.healthy_runner(args, timeout)
            if '--write-out' in args:
                self.assertGreaterEqual(timeout, 5)
                self.now += 4
            return result
        result = self.recover(runner=runner, repair_funnel=True)
        self.assertTrue(result['success'])
        self.assertFalse(result['tailscale_restart_requested'])

    def test_disabled_funnel_is_not_enabled_or_restarted(self):
        def runner(args, timeout):
            result = self.healthy_runner(args, timeout)
            return self.result(args, stdout='No serve config') if 'funnel' in args else result
        result = self.recover(runner=runner, repair_funnel=True)
        self.assertFalse(result['success'])
        self.assertFalse(result['tailscale_restart_requested'])

    def test_public_authentication_bypass_is_not_counted_as_recovery(self):
        def runner(args, timeout):
            result = self.healthy_runner(args, timeout)
            return self.result(args, stdout='200') if '--write-out' in args else result
        result = self.recover(runner=runner, repair_funnel=True)
        self.assertFalse(result['success'])
        self.assertIn('authentication', result['reason'])
        self.assertFalse(result['tailscale_restart_requested'])

    def test_invalid_deadline_is_rejected(self):
        with self.assertRaises(ValueError):
            self.recover(runner=self.healthy_runner, timeout=0)

    def test_lockdown_prevents_any_recovery_commands(self):
        with tempfile.TemporaryDirectory() as directory:
            (Path(directory) / 'LOCKED_DOWN').touch()
            result = recovery.recover(self.settings, Path(directory) / 'kubeconfig',
                                      runner=self.healthy_runner, emit=lambda line: None)
        self.assertFalse(result['success'])
        self.assertEqual(self.calls, [])


if __name__ == '__main__':
    unittest.main()

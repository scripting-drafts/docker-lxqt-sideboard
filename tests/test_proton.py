"""Pure static and synthetic-config tests. Never contact Proton or alter host routes."""
import base64
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / 'scripts/proton-wireguard.py'

spec = importlib.util.spec_from_file_location('proton_wireguard', SCRIPT)
proton = importlib.util.module_from_spec(spec)
spec.loader.exec_module(proton)


def mock_config():
    private = base64.b64encode(bytes([7]) * 32).decode()
    public = base64.b64encode(bytes([8]) * 32).decode()
    return f'''[Interface]
PrivateKey = {private}
Address = 192.0.2.2/32
DNS = 192.0.2.1
[Peer]
PublicKey = {public}
AllowedIPs = 0.0.0.0/0, ::/0
Endpoint = 198.51.100.4:51820
PersistentKeepalive = 25
'''


class ProtonConfigTests(unittest.TestCase):
    def test_wireguard_proton_config_import(self):
        with tempfile.TemporaryDirectory() as td:
            src = Path(td) / 'wg-example.conf'
            src.write_text(mock_config())
            with patch.object(proton, 'SECRETS', Path(td) / 'secrets'), patch.object(proton, 'SOURCE', Path(td) / 'proton-source.json'):
                meta = proton.import_for_pod(src)
                self.assertEqual(meta['host_profile'], 'wg-example')
                self.assertEqual(meta['endpoint'], '198.51.100.4:51820')
                secret = proton.SECRETS / 'wireguard_private_key'
                self.assertEqual(secret.stat().st_mode & 0o777, 0o600)
                self.assertEqual(proton.SOURCE.stat().st_mode & 0o777, 0o600)
                self.assertNotIn('PrivateKey', proton.SOURCE.read_text())

    def check(self, profiles, *, pod='pod-key', repair=True, scores=None):
        state = [dict(profile) for profile in profiles]
        calls = []

        def list_profiles():
            return [dict(profile) for profile in state]

        def disconnect(name):
            calls.append(('down', name))
            for profile in state:
                if profile['name'] == name:
                    profile['active'] = False

        def activate(name):
            calls.append(('up', name))
            for profile in state:
                if profile['name'] == name:
                    profile['active'] = True

        def probe(name):
            calls.append(('probe', name))
            if scores is None:
                raise AssertionError('unexpected load probe')
            return proton.WireGuardInterface(
                name=name, device=name, active=True, public_key='other-key',
                latency_ms=scores[name], packet_loss=0,
            )

        with patch.object(proton, 'pod_public_key', return_value=pod), \
             patch.object(proton, 'list_wireguard_profiles', side_effect=list_profiles), \
             patch.object(proton, 'disconnect_profile', side_effect=disconnect), \
             patch.object(proton, 'activate_profile', side_effect=activate), \
             patch.object(proton, 'probe_profile_load', side_effect=probe):
            return proton.check_host_conflict(repair=repair), calls, state

    def test_host_key_verification(self):
        result, calls, _ = self.check([
            {'name': 'wg-host', 'public_key': 'other-key', 'active': True, 'device': 'wg-host'},
        ])
        self.assertEqual(result, 0)
        self.assertEqual(calls, [])

        result, calls, _ = self.check([
            {'name': 'wg-host', 'public_key': 'pod-key', 'active': True, 'device': 'wg-host'},
        ], repair=False)
        self.assertEqual(result, 3)
        self.assertEqual(calls, [])

    def test_conflict_disconnects_and_fails_over(self):
        result, calls, state = self.check([
            {'name': 'wg-host', 'public_key': 'pod-key', 'active': True, 'device': 'wg-host'},
            {'name': 'wireguard', 'public_key': 'other-key', 'active': False, 'device': ''},
        ])
        self.assertEqual(result, 0)
        self.assertEqual(calls, [('down', 'wg-host'), ('up', 'wireguard')])
        self.assertTrue(next(profile for profile in state if profile['name'] == 'wireguard')['active'])

    def test_load_based_failover_prefers_lower_score(self):
        result, calls, state = self.check([
            {'name': 'wg-host', 'public_key': 'pod-key', 'active': True, 'device': 'wg-host'},
            {'name': 'wireguard-a', 'public_key': 'key-a', 'active': False, 'device': ''},
            {'name': 'wireguard-b', 'public_key': 'key-b', 'active': False, 'device': ''},
        ], scores={'wireguard-a': 100.0, 'wireguard-b': 10.0})
        self.assertEqual(result, 0)
        self.assertEqual(calls, [
            ('down', 'wg-host'), ('probe', 'wireguard-a'),
            ('probe', 'wireguard-b'), ('up', 'wireguard-b'),
        ])
        self.assertTrue(next(p for p in state if p['name'] == 'wireguard-b')['active'])

    def test_conflict_without_fallback_leaves_vpn_down(self):
        result, calls, state = self.check([
            {'name': 'wg-host', 'public_key': 'pod-key', 'active': True, 'device': 'wg-host'},
            {'name': 'wireguard', 'public_key': 'pod-key', 'active': False, 'device': ''},
        ])
        self.assertEqual(result, 0)
        self.assertEqual(calls, [('down', 'wg-host')])
        self.assertFalse(any(profile['active'] for profile in state))

    def test_unverifiable_active_profile_fails_closed(self):
        result, calls, _ = self.check([
            {'name': 'wg-host', 'public_key': None, 'active': True, 'device': 'wg-host'},
        ])
        self.assertEqual(result, 2)
        self.assertEqual(calls, [])

    def test_rejects_invalid_key_and_missing_peer(self):
        with tempfile.TemporaryDirectory() as td:
            bad = Path(td) / 'invalid.conf'
            bad.write_text(mock_config().replace('PrivateKey = ', 'PrivateKey = invalid'))
            with self.assertRaises(ValueError):
                proton.parse_config(bad)
            bad.write_text('[Interface]\nPrivateKey = ' + base64.b64encode(bytes(32)).decode())
            with self.assertRaises(ValueError):
                proton.parse_config(bad)

    def test_long_filename_valid_for_pod_not_host(self):
        with tempfile.TemporaryDirectory() as td:
            src = Path(td) / 'example-wireguard-profile-name.conf'
            src.write_text(mock_config())
            data = proton.parse_config(src)
            self.assertEqual(data['host_profile'], '')
            with self.assertRaises(ValueError):
                proton.import_for_pod(src, profile_name='example-wireguard-profile-name')

    def test_setup_shell_and_integrations(self):
        for script in ('scripts/setup-proton.sh', 'scripts/verify-proton.sh', 'scripts/bootstrap-host.sh', 'scripts/deploy.sh'):
            subprocess.run(['bash','-n',str(ROOT / script)], check=True)
        bootstrap=(ROOT/'scripts/bootstrap-host.sh').read_text()
        self.assertIn('wireguard-tools iproute2',bootstrap)
        deploy=(ROOT/'scripts/deploy.sh').read_text()
        self.assertIn('proton-wireguard.py check-host', deploy)
        self.assertIn('--driver-opt network=host', deploy)
        setup=(ROOT/'scripts/setup-proton.sh').read_text()
        self.assertNotIn('nmcli connection up',setup)
        self.assertNotIn('wg-quick up',setup)
        self.assertIn('--host-profile',setup)
        self.assertIn('config/proton-source.json',(ROOT/'.gitignore').read_text())


if __name__=='__main__':
    unittest.main()

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
                with patch.object(proton, 'active_host_connections', return_value=[('wg-example', 'wireguard', 'wg-example')]):
                    self.assertEqual(proton.check_host_conflict(), 3)
                with patch.object(proton, 'active_host_connections', return_value=[]):
                    self.assertEqual(proton.check_host_conflict(), 0)

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
        setup=(ROOT/'scripts/setup-proton.sh').read_text()
        self.assertNotIn('nmcli connection up',setup)
        self.assertNotIn('wg-quick up',setup)
        self.assertIn('--host-profile',setup)
        self.assertIn('config/proton-source.json',(ROOT/'.gitignore').read_text())


if __name__=='__main__':
    unittest.main()

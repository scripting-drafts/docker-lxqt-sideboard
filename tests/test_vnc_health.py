"""RFB readiness must reject a listening socket that cannot negotiate VNC."""
import importlib.util
from pathlib import Path
import socket
import struct
import unittest
from unittest.mock import patch

import yaml

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('vnc_health', ROOT / 'scripts/check-vnc.py')
health = importlib.util.module_from_spec(spec)
spec.loader.exec_module(health)


class FakeSocket:
    def __init__(self, data=b'', silent=False):
        self.data = data
        self.silent = silent
        self.sent = b''
        self.closed = False

    def __enter__(self):
        return self

    def __exit__(self, *arguments):
        self.closed = True

    def settimeout(self, seconds):
        self.timeout = seconds

    def recv(self, size):
        if self.silent:
            raise socket.timeout('timed out')
        size = min(size, 2)  # Exercise fragmented protocol messages.
        result, self.data = self.data[:size], self.data[size:]
        return result

    def sendall(self, data):
        self.sent += data


class VncHealthTests(unittest.TestCase):
    def check(self, connection, **options):
        with patch.object(health.socket, 'create_connection', return_value=connection):
            return health.check_vnc(**options)

    def test_valid_fragmented_handshake_without_reading_or_sending_a_password(self):
        connection = FakeSocket(b'RFB 003.008\n\x01\x02')
        self.assertTrue(self.check(connection))
        self.assertEqual(connection.sent, b'RFB 003.008\n')
        self.assertTrue(connection.closed)

    def test_tcp_accept_without_rfb_response_is_not_ready(self):
        with self.assertRaises(TimeoutError):
            self.check(FakeSocket(silent=True))

    def test_truncated_banner_is_not_ready(self):
        with self.assertRaises(ConnectionError):
            self.check(FakeSocket(b'RFB '))

    def test_no_password_authentication_is_not_ready(self):
        for types in (b'\x01\x01', b'\x02\x01\x02'):
            with self.assertRaises(ValueError):
                self.check(FakeSocket(b'RFB 003.008\n' + types))

    def test_rfb_33_requires_vnc_authentication(self):
        self.assertTrue(self.check(FakeSocket(b'RFB 003.003\n' + struct.pack('!I', 2))))

    def test_whole_handshake_has_one_deadline(self):
        ticks = iter(i * 0.4 for i in range(100))
        with self.assertRaises(TimeoutError):
            self.check(FakeSocket(b'RFB 003.008\n\x01\x02'), timeout=2, clock=lambda: next(ticks))

    def test_image_and_build_context_include_only_the_required_probe(self):
        dockerfile = (ROOT / 'Dockerfile').read_text()
        self.assertIn('COPY --chmod=0755 scripts/check-vnc.py /usr/local/bin/check-vnc', dockerfile)
        self.assertIn('/usr/local/bin/check-vnc --timeout 2', dockerfile)
        overlay = (ROOT / 'Dockerfile.overlay').read_text()
        self.assertIn('FROM desktop-base', overlay)
        self.assertIn('COPY --chmod=0755 scripts/check-vnc.py /usr/local/bin/check-vnc', overlay)
        self.assertIn('/usr/local/bin/check-vnc --timeout 2', overlay)
        self.assertNotIn('\nRUN ', overlay)
        ignore = (ROOT / '.dockerignore').read_text().splitlines()
        self.assertIn('config/', ignore)
        self.assertIn('scripts/*', ignore)
        self.assertIn('!scripts/check-vnc.py', ignore)

    def test_threaded_startup_and_kubernetes_rfb_probes(self):
        source = (ROOT / 'start-desktop.sh').read_text()
        self.assertIn('vnc_args=(-threads ', source)
        self.assertIn('vnc_args+=(-multiptr)', source)
        resources = list(yaml.safe_load_all((ROOT / 'examples/workload.example.yaml').read_text()))
        deployment = next(item for item in resources if item['kind'] == 'Deployment')
        desktop = next(c for c in deployment['spec']['template']['spec']['containers'] if c['name'] == 'desktop')
        for name in ('startupProbe', 'readinessProbe'):
            command = desktop[name]['exec']['command'][-1]
            self.assertIn('/usr/local/bin/check-vnc --timeout 2', command)
            self.assertEqual(desktop[name]['timeoutSeconds'], 5)

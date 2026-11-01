"""Minimal unit checks for the WireGuard inventory/load library."""
import importlib.util
import pathlib
import sys
import unittest
from unittest.mock import patch

ROOT = pathlib.Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('wireguard_library', ROOT / 'scripts/wireguard_library.py')
library = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = library
spec.loader.exec_module(library)


class WireGuardLibraryTests(unittest.TestCase):
    def test_parse_ping(self):
        output = '3 packets transmitted, 3 received, 0% packet loss, time 2002ms\n' \
                 'rtt min/avg/max/mdev = 20.1/24.5/30.0/2.0 ms\n'
        self.assertEqual(library.parse_ping(output), (24.5, 0.0))

    def test_score_and_rank(self):
        fast = library.WireGuardInterface('fast', 'wg0', True, 'key', latency_ms=20, packet_loss=0)
        slow = library.WireGuardInterface('slow', 'wg1', True, 'key', latency_ms=300, packet_loss=5)
        unknown = library.WireGuardInterface('unknown', '', False, 'key')
        self.assertLess(fast.score, slow.score)
        self.assertEqual([item.name for item in library.ranked([slow, unknown, fast])],
                         ['fast', 'slow', 'unknown'])

    def test_measure_inactive_does_not_run_ping(self):
        interface = library.WireGuardInterface('idle', '', False, 'key')
        with patch.object(library.subprocess, 'run') as run:
            measured = library.measure(interface)
        self.assertEqual(measured.error, 'not active')
        run.assert_not_called()

    def test_measure_parses_active_tunnel(self):
        interface = library.WireGuardInterface('active', 'wg0', True, 'key')
        output = '3 packets transmitted, 3 received, 0% packet loss, time 2002ms\n' \
                 'rtt min/avg/max/mdev = 10.0/12.0/14.0/1.0 ms\n'
        with patch.object(library.shutil, 'which', return_value='/usr/bin/ping'), \
             patch.object(library.subprocess, 'run',
                          return_value=library.subprocess.CompletedProcess([], 0, output, '')):
            measured = library.measure(interface)
        self.assertEqual(measured.latency_ms, 12.0)
        self.assertEqual(measured.packet_loss, 0.0)
        self.assertEqual(measured.score, 12.0)

    def test_discovery_filters_and_derives_public_keys(self):
        rows = [['wg-a', 'wireguard'], ['eth0', '802-3-ethernet'], ['wg-b', 'wireguard']]
        active = [['wg-a', 'wireguard', 'wg-a']]
        with patch.object(library, 'nmcli_rows', side_effect=[rows, active]), \
             patch.object(library, 'profile_private_key', return_value='private'), \
             patch.object(library, 'wireguard_public_key', return_value='public'):
            interfaces = library.discover()
        self.assertEqual([item.name for item in interfaces], ['wg-a', 'wg-b'])
        self.assertTrue(interfaces[0].active)
        self.assertEqual(interfaces[0].device, 'wg-a')
        self.assertFalse(interfaces[1].active)


if __name__ == '__main__':
    unittest.main()

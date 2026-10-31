"""Public-IP reporting must work without additional filesystem capabilities."""
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

import yaml

ROOT = Path(__file__).resolve().parents[1]


class GluetunStatusTests(unittest.TestCase):
    def check_deployment(self, deployment):
        pod = deployment['spec']['template']['spec']
        self.assertEqual(pod['dnsPolicy'], 'None')
        self.assertEqual(pod['dnsConfig']['nameservers'], ['127.0.0.1'])
        self.assertEqual(pod['dnsConfig']['searches'], [])
        self.assertEqual({x['name']: x['value'] for x in pod['dnsConfig']['options']}['ndots'], '1')
        vpn = pod['initContainers'][0]
        env = {item['name']: item['value'] for item in vpn['env']}
        self.assertEqual(env['VPN_TYPE'], 'wireguard')
        self.assertEqual(env['PUBLICIP_FILE'], '/tmp/gluetun-ip')
        self.assertNotIn('PUBLICIP_ENABLED', env)
        self.assertEqual(env['PUID'], '0')
        self.assertEqual(env['PGID'], '0')
        self.assertEqual(vpn['securityContext']['runAsUser'], 0)
        self.assertEqual(vpn['securityContext']['runAsGroup'], 0)
        self.assertNotIn('FIREWALL', env)
        self.assertNotIn('FIREWALL_OUTBOUND_SUBNETS', env)
        self.assertEqual(vpn['securityContext']['capabilities'],
                         {'drop': ['ALL'], 'add': ['NET_ADMIN']})
        self.assertEqual(vpn['restartPolicy'], 'Always')
        for probe in ('startupProbe', 'readinessProbe'):
            self.assertEqual(vpn[probe]['exec']['command'],
                             ['/gluetun-entrypoint', 'healthcheck'])

    def test_renderer_preserves_health_and_firewall_without_extra_capabilities(self):
        spec = importlib.util.spec_from_file_location('render_status', ROOT / 'scripts/render.py')
        render = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(render)
        with tempfile.TemporaryDirectory() as directory:
            temporary = Path(directory)
            render.CONFIG = temporary / 'settings.json'
            settings = json.loads((ROOT / 'config/settings.example.json').read_text())
            render.CONFIG.write_text(json.dumps(settings))
            render.OUT = temporary / 'generated'
            render.main()
            documents = list(yaml.safe_load_all((render.OUT / 'workload.yaml').read_text()))
            self.check_deployment(next(d for d in documents if d['kind'] == 'Deployment'))

    def test_example_matches_renderer_policy(self):
        documents = list(yaml.safe_load_all((ROOT / 'examples/workload.example.yaml').read_text()))
        self.check_deployment(next(d for d in documents if d['kind'] == 'Deployment'))


if __name__ == '__main__':
    unittest.main()

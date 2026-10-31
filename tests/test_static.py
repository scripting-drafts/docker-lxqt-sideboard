import json
import os
import pathlib
import subprocess
import tempfile
import unittest
import yaml

ROOT=pathlib.Path(__file__).resolve().parents[1]

class StaticProjectTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cfg=ROOT/'config/settings.json'
        if cfg.exists():
            raise RuntimeError('Testing will not overwrite an existing local user settings.json')
        s=json.loads((ROOT/'config/settings.example.json').read_text())
        s['host_data_dir']='/tmp/lxqt-kind-secure-test-persistence'
        cfg.write_text(json.dumps(s))
        try:
            subprocess.run(['python3',str(ROOT/'scripts/render.py')],cwd=ROOT,check=True)
        finally:
            cfg.unlink()
        cls.kind=yaml.safe_load((ROOT/'generated/kind.yaml').read_text())
        cls.resources=list(yaml.safe_load_all((ROOT/'generated/workload.yaml').read_text()))
        cls.deploy=next(r for r in cls.resources if r['kind']=='Deployment')
        cls.spec=cls.deploy['spec']['template']['spec']

    def test_kind_mounts_and_cpu(self):
        mounts=self.kind['nodes'][0]['extraMounts']
        self.assertTrue(any(v['containerPath']=='/var/local/lxqt-data' for v in mounts))
        self.assertTrue(any(v['containerPath']=='/dev/net/tun' for v in mounts))
        deploy=(ROOT/'scripts/deploy.sh').read_text()
        self.assertIn('--cpuset-cpus',deploy)

    def test_vpn_first_and_privileges(self):
        gluetun=self.spec['initContainers'][0]
        self.assertEqual(gluetun['name'],'gluetun')
        self.assertEqual(gluetun['restartPolicy'],'Always')
        self.assertIn('startupProbe',gluetun)
        self.assertEqual(gluetun['securityContext']['capabilities']['add'],['NET_ADMIN'])
        self.assertFalse(self.spec['hostNetwork'])
        self.assertFalse(self.spec['automountServiceAccountToken'])
        self.assertEqual(self.deploy['spec']['replicas'],1)
        self.assertEqual(self.deploy['spec']['strategy']['type'],'Recreate')
        for container in self.spec['containers']:
            self.assertEqual(container['securityContext']['capabilities']['drop'],['ALL'])
            self.assertFalse(container['securityContext']['allowPrivilegeEscalation'])

    def test_no_public_cluster_ports_or_plaintext_secret(self):
        self.assertFalse(any(r['kind'] in ('Service','Ingress') for r in self.resources))
        raw=(ROOT/'generated/workload.yaml').read_text()
        self.assertNotIn('tskey-auth-',raw)
        self.assertNotIn('wireguard_private_key: ',raw)
        self.assertNotIn('hostPort:',raw)
        self.assertNotIn('NodePort',raw)
        self.assertEqual([c['name'] for c in self.spec['containers']],['desktop','caddy','tailscale'])

    def test_persistent_host_mounts(self):
        pvs=[r for r in self.resources if r['kind']=='PersistentVolume']
        self.assertEqual(len(pvs),2)
        self.assertEqual([p['spec']['persistentVolumeReclaimPolicy'] for p in pvs],['Retain','Retain'])
        self.assertTrue(all(p['spec']['hostPath']['path'].startswith('/var/local/lxqt-data/') for p in pvs))

if __name__=='__main__':
    unittest.main()

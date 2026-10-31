"""Rollout timeout and read-only diagnosis regression tests."""
from pathlib import Path
import os
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]

class RolloutTests(unittest.TestCase):
    def test_startup_budgets_fit_deployment_deadline(self):
        import yaml
        docs = list(yaml.safe_load_all((ROOT/'examples/workload.example.yaml').read_text()))
        deployment = next(x for x in docs if x['kind'] == 'Deployment')
        self.assertEqual(deployment['spec']['progressDeadlineSeconds'], 1200)
        pod = deployment['spec']['template']['spec']
        vpn = pod['initContainers'][0]
        desktop = next(c for c in pod['containers'] if c['name'] == 'desktop')
        startup_total = vpn['startupProbe']['failureThreshold']*vpn['startupProbe']['periodSeconds'] + desktop['startupProbe']['failureThreshold']*desktop['startupProbe']['periodSeconds']
        self.assertGreater(startup_total, 420, 'test needs to cover old timeout')
        self.assertLess(startup_total, deployment['spec']['progressDeadlineSeconds'])
        render = (ROOT/'scripts/render.py').read_text()
        self.assertIn("'progressDeadlineSeconds':1200", render)
    def test_deploy_reports_rollout_errors(self):
        s = (ROOT/'scripts/deploy.sh').read_text()
        self.assertIn('LXQT_ROLLOUT_TIMEOUT:-900s', s)
        self.assertIn('diagnose-rollout.sh', s)
        self.assertIn('rollout status deployment/lxqt-desktop --timeout="$ROLLOUT_TIMEOUT"', s)
        self.assertIn('if ! "${K[@]}" -n "$NS" rollout status', s)
    def test_diagnostics_use_pod_events_and_logs_not_secret_values(self):
        s = (ROOT/'scripts/diagnose-rollout.sh').read_text()
        for term in ('get events', 'describe pod', 'logs "$POD"', 'get pvc', 'initContainerStatuses'):
            self.assertIn(term, s)
        self.assertNotIn('get secrets', s)
        self.assertNotIn('get secret ', s)
        r = subprocess.run(['bash','-n',str(ROOT/'scripts/diagnose-rollout.sh')],capture_output=True,text=True)
        self.assertEqual(r.returncode,0,r.stderr)
    def test_diagnose_script_requires_kubeconfig_without_accessing_kubernetes(self):
        # No credentialed cluster is present in this source archive.
        env = dict(os.environ)
        env['LXQT_ALLOWED_CPUS'] = sorted(os.sched_getaffinity(0) - {2,8})[0].__str__()
        proc = subprocess.run([str(ROOT/'scripts/diagnose-rollout.sh')],env=env,capture_output=True,text=True)
        self.assertNotEqual(proc.returncode,0)
        self.assertIn('Missing config/settings.json or config/kubeconfig',proc.stderr)

if __name__=='__main__':
    unittest.main()

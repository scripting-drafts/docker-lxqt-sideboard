"""CPU-affinity safety regression checks (no privileged installation)."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
import yaml
ROOT=Path(__file__).resolve().parents[1]

class CpuSafetyTests(unittest.TestCase):
    def test_default_never_includes_suspect_cpus(self):
        s=json.loads((ROOT/'config/settings.example.json').read_text())
        self.assertEqual(s['host_cpu_set'],'0,1,3-7,9-11')
        helper=ROOT/'scripts/cpu-affinity.sh'
        for cpus in ('0,1,2,3','2','8','0-11'):
            r=subprocess.run(['bash','-c',f'source "{helper}"; lxqt_cpu_expand "$1"', 'bash',cpus],capture_output=True,text=True)
            self.assertNotEqual(r.returncode,0)
            self.assertIn('REFUSED',r.stderr)
    def test_real_affinity_on_available_safe_cpu(self):
        cpus=sorted(os.sched_getaffinity(0)-{2,8})
        if not cpus: self.skipTest('no safe CPU available')
        project=ROOT
        # child guard must produce a restricted CPU mask without modifying this process
        code=f'source "{project}/scripts/cpu-affinity.sh"; lxqt_cpu_guard "{cpus[0]}"; grep Cpus_allowed_list /proc/$$/status'
        r=subprocess.run(['bash','-c',code],text=True,capture_output=True)
        self.assertEqual(r.returncode,0,r.stderr)
        self.assertIn('Cpus_allowed_list:',r.stdout)
        self.assertIn(str(cpus[0]),r.stdout)
    def test_dedicated_builder_and_node_enforced(self):
        deploy=(ROOT/'scripts/deploy.sh').read_text()
        self.assertIn('docker update --cpuset-cpus "$CPUS" "$NODE"',deploy)
        self.assertIn('--driver-opt "\\\"cpuset-cpus=$CPUS\\\""',deploy)
        self.assertIn('docker buildx build --builder "$BUILDER"',deploy)
        self.assertNotIn('docker build --pull',deploy)
        self.assertIn('scripts/verify-cpus.sh',deploy)
    def test_buildx_driver_opts_csv_quoting(self):
        # Buildx parses --driver-opt with a CSV parser before splitting k=v.
        # The comma-containing cpuset must remain ONE CSV field.
        import csv
        import io
        import re
        deploy=(ROOT/'scripts/deploy.sh').read_text()
        lines=deploy.splitlines()
        create=next((i for i,l in enumerate(lines)
                     if 'docker buildx create --name "$BUILDER"' in l),None)
        self.assertIsNotNone(create,'Buildx create invocation not found')
        block='\n'.join(lines[create:create+5])
        self.assertIn('--driver-opt network=host',block)
        line=next((l.strip() for l in lines[create:create+5]
                   if 'cpuset-cpus=$CPUS' in l),None)
        self.assertIsNotNone(line,'Buildx cpuset option not found')
        self.assertTrue(line.startswith('--driver-opt '),line)
        line=line.removeprefix('--driver-opt ').removesuffix(' >/dev/null')
        code='CPUS="0,1,3-7,9-11"; printf "%s\\n" '+line
        result=subprocess.run(['bash','-c',code],capture_output=True,text=True)
        self.assertEqual(result.returncode,0,result.stderr)
        token=result.stdout.strip()
        options=next(csv.reader(io.StringIO(token)))
        self.assertEqual(options,['cpuset-cpus=0,1,3-7,9-11'],repr(token))
        self.assertNotIn('2', options[0].replace('cpuset-cpus=','').split(','))
        self.assertNotIn('8', options[0].replace('cpuset-cpus=','').split(','))
    def test_operations_and_maintenance_affinity(self):
        for file in ['install.sh','scripts/install-all.sh','scripts/deploy.sh','scripts/update.sh',
                     'scripts/bootstrap-host.sh','scripts/install-python.sh','scripts/install-docker.sh',
                     'scripts/audit-host.sh','scripts/watchdog.sh']:
            content=(ROOT/file).read_text()
            self.assertIn('lxqt_cpu_guard',content,file)
        self.assertIn('CPUAffinity=$CPU_LIST',(ROOT/'scripts/install-systemd.sh').read_text())
        self.assertIn('"$(lxqt_cpu_set)"',(ROOT/'scripts/verify-tun.sh').read_text())
        self.assertIn("'--cpuset-cpus', data['host_cpu_set']",(ROOT/'scripts/configure.py').read_text())

if __name__=='__main__':unittest.main()

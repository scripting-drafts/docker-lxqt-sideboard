#!/usr/bin/env bash
set -Eeuo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
export LXQT_PROJECT_ROOT="$ROOT"
source "$ROOT/scripts/cpu-affinity.sh"
lxqt_cpu_guard "$(lxqt_cpu_set)"
cd "$ROOT"
[[ ! -e config/LOCKED_DOWN ]] || exit 0
# Do not race ongoing updates or deployment.
exec 9>config/.maintenance.lock
flock -n 9 || exit 0
set +e
python3 - "$ROOT" <<'PY'
import json,subprocess,sys
from pathlib import Path
root=Path(sys.argv[1]); s=json.loads((root/'config/settings.json').read_text())
K=['kubectl','--kubeconfig',str(root/'config/kubeconfig')]
def output(args):return subprocess.check_output(args,text=True,stderr=subprocess.PIPE).strip()
def bad(msg):
    # cannot invoke lockdown while lock held; exit code signals shell wrapper.
    print('WATCHDOG ALERT:',msg,file=sys.stderr);sys.exit(42)
try:
    if output(['docker','inspect',s['cluster']+'-control-plane','--format','{{.HostConfig.CpusetCpus}}']) != s['host_cpu_set']:
        bad('Kind CPU affinity changed')
    d=json.loads(output(K+['-n',s['namespace'],'get','deployment','lxqt-desktop','-o','json']))
    if d['spec']['replicas']!=1: bad('Unexpected replica count')
    t=d['spec']['template']['spec']
    if t.get('hostNetwork') or t.get('hostPID') or t.get('automountServiceAccountToken') != False:bad('Pod isolation drift')
    if not any(c['name']=='gluetun' and c.get('restartPolicy')=='Always' and c.get('securityContext',{}).get('capabilities',{}).get('add')==['NET_ADMIN'] for c in t.get('initContainers',[])):bad('VPN sidecar changed')
    if sorted(c['name'] for c in t['containers'])!=['caddy','desktop','tailscale']:bad('container composition changed')
    if d['status'].get('readyReplicas',0)<1:bad('deployment is not ready (possible VPN outage)')
    pods=json.loads(output(K+['-n',s['namespace'],'get','pods','-l','app=lxqt-desktop','-o','json']))['items']
    if len(pods)!=1:bad('unexpected number of pods')
    statuses=pods[0]['status'].get('initContainerStatuses',[])
    if not any(st['name']=='gluetun' and st.get('ready') for st in statuses):bad('VPN health probe failed')
except (subprocess.CalledProcessError,KeyError,ValueError) as err:
    bad(str(err))
PY
code=$?
set -e
if [[ "$code" != 0 ]]; then
    # release lock before calling lockdown
    flock -u 9
    "$ROOT/scripts/lockdown.sh" "WATCHDOG: deployment security or VPN health invariant failed" || true
    exit 1
fi

#!/usr/bin/env bash
# Fail-closed administrative action. Does not certify arbitrary intrusion detection.
set -Eeuo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
export LXQT_PROJECT_ROOT="$ROOT"
source "$ROOT/scripts/cpu-affinity.sh"
lxqt_cpu_guard "$(lxqt_cpu_set)"
exec 9>"$ROOT/config/.maintenance.lock"
flock -x 9
umask 077
reason="${1:-manual emergency lockdown}"
printf 'Lockdown requested: %s\n' "$reason" >&2
printf '%s\t%s\n' "$(date -Is)" "$reason" > "$ROOT/config/LOCKED_DOWN"
if [[ -s "$ROOT/config/settings.json" && -s "$ROOT/config/kubeconfig" ]]; then
  NS="$(python3 -c 'import json,sys;print(json.load(open(sys.argv[1]))["namespace"])' "$ROOT/config/settings.json")"
  kubectl --kubeconfig "$ROOT/config/kubeconfig" -n "$NS" exec deployment/lxqt-desktop -c tailscale -- tailscale funnel reset 2>/dev/null || true
  kubectl --kubeconfig "$ROOT/config/kubeconfig" -n "$NS" scale deployment/lxqt-desktop --replicas=0
fi
printf 'LOCKED_DOWN marker retained. Do not unlock before investigation.\n' >&2

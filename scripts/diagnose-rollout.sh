#!/usr/bin/env bash
# Read-only diagnosis for a stalled LXQt Deployment. Never prints Secret objects,
# passwords, private keys, or Caddy configuration.
set -Eeuo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
export LXQT_PROJECT_ROOT="$ROOT"
source "$ROOT/scripts/cpu-affinity.sh"
lxqt_cpu_guard "$(lxqt_cpu_set)"
cd "$ROOT"
[[ -r config/settings.json && -r config/kubeconfig ]] || {
  echo 'Missing config/settings.json or config/kubeconfig. Deploy/create the cluster first.' >&2
  exit 1
}
command -v kubectl >/dev/null || { echo 'Missing kubectl' >&2; exit 1; }
NS="$(python3 - <<'PY'
import json
with open('config/settings.json', encoding='utf-8') as f: print(json.load(f)['namespace'])
PY
)"
K=(kubectl --kubeconfig "$ROOT/config/kubeconfig" -n "$NS")
show() {
  local caption="$1"; shift
  printf '\n=== %s ===\n' "$caption"
  "$@" || echo "(Command failed: $caption)" >&2
}
show 'Deployment status' "${K[@]}" get deployment lxqt-desktop -o wide
show 'Pods' "${K[@]}" get pods -l app=lxqt-desktop -o wide
show 'Persistent volume claims' "${K[@]}" get pvc
show 'Recent namespace events (last 40)' bash -c 'kubectl --kubeconfig "$1" -n "$2" get events --sort-by=.lastTimestamp 2>&1 | tail -n 40' _ "$ROOT/config/kubeconfig" "$NS"
# Include old and new Pod during a Recreate rollout, if both temporarily exist.
PODS="$("${K[@]}" get pods -l app=lxqt-desktop -o jsonpath='{range .items[*]}{.metadata.name}{"\n"}{end}')" || exit 1
if [[ -z "$PODS" ]]; then
  echo 'No LXQt Pod exists. Inspect Deployment events, PVCs and node scheduling above.'
  exit 0
fi
while IFS= read -r POD; do
  [[ -n "$POD" ]] || continue
  show "Pod $POD description (look for Events, failed probes, mount errors)" "${K[@]}" describe pod "$POD"
  printf '\n=== Container state summary for %s ===\n' "$POD"
  "${K[@]}" get pod "$POD" -o jsonpath='{range .status.initContainerStatuses[*]}init {.name}: ready={.ready} restarts={.restartCount} waiting={.state.waiting.reason} terminated={.lastTerminationState.terminated.reason}{"\n"}{end}{range .status.containerStatuses[*]}app {.name}: ready={.ready} restarts={.restartCount} waiting={.state.waiting.reason} terminated={.lastTerminationState.terminated.reason}{"\n"}{end}' || true
  printf '\n'
  for CONTAINER in gluetun desktop caddy tailscale; do
    show "$POD / $CONTAINER recent logs (up to 60 lines)" "${K[@]}" logs "$POD" -c "$CONTAINER" --tail=60
  done
  echo 'If a container is restarting, run: kubectl --kubeconfig config/kubeconfig -n '"$NS"' logs '"$POD"' -c <container> --previous --tail=100'
done <<< "$PODS"
echo 'When sharing diagnostics, remove public IP addresses, authentication tokens, and other sensitive details.'

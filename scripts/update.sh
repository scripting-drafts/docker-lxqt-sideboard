#!/usr/bin/env bash
set -Eeuo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
export LXQT_PROJECT_ROOT="$ROOT"
source "$ROOT/scripts/cpu-affinity.sh"
lxqt_cpu_guard "$(lxqt_cpu_set)"
cd "$ROOT"
[[ ! -e config/LOCKED_DOWN ]] || { echo 'Locked down'; exit 1; }
exec 9>config/.maintenance.lock
flock -x 9
# Rebuild updated images; deploy.sh handles rolling restart (Recreate strategy).
./scripts/deploy.sh
NS="$(python3 -c 'import json;print(json.load(open("config/settings.json"))["namespace"])')"
kubectl --kubeconfig config/kubeconfig -n "$NS" rollout status deployment/lxqt-desktop --timeout=420s
# Funnel config persists in tailscale state, but verify after restart.
kubectl --kubeconfig config/kubeconfig -n "$NS" exec deployment/lxqt-desktop -c tailscale -- tailscale funnel status || true

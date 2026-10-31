#!/usr/bin/env bash
set -Eeuo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
export LXQT_PROJECT_ROOT="$ROOT"
source "$ROOT/scripts/cpu-affinity.sh"
lxqt_cpu_guard "$(lxqt_cpu_set)"
NS="$(python3 -c 'import json,sys;print(json.load(open(sys.argv[1]))["namespace"])' "$ROOT/config/settings.json")"
kubectl --kubeconfig "$ROOT/config/kubeconfig" -n "$NS" exec deployment/lxqt-desktop -c tailscale -- tailscale funnel reset

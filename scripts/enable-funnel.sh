#!/usr/bin/env bash
set -Eeuo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
export LXQT_PROJECT_ROOT="$ROOT"
source "$ROOT/scripts/cpu-affinity.sh"
lxqt_cpu_guard "$(lxqt_cpu_set)"
[[ ! -e "$ROOT/config/LOCKED_DOWN" ]] || { echo 'Locked down' >&2; exit 1; }
NS="$(python3 -c 'import json,sys;print(json.load(open(sys.argv[1]))["namespace"])' "$ROOT/config/settings.json")"
K=(kubectl --kubeconfig "$ROOT/config/kubeconfig" -n "$NS")
"${K[@]}" exec deployment/lxqt-desktop -c tailscale -- tailscale funnel --bg 8081
"${K[@]}" exec deployment/lxqt-desktop -c tailscale -- tailscale funnel status
printf '\nWhen Tailscale requests Funnel enablement, approve it in your tailnet admin interface.\n'

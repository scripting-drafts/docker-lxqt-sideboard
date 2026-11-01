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
if desktop_url="$("$ROOT/scripts/funnel-url.sh" 2>/dev/null)"; then
  printf '\nDesktop URL: %s\n' "$desktop_url"
else
  printf '\nDesktop URL: not assigned yet; approve Funnel, then run ./scripts/funnel-url.sh\n'
fi
printf 'When Tailscale requests Funnel enablement, approve it in your tailnet admin interface.\n'

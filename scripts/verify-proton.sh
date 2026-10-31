#!/usr/bin/env bash
# Read-only status verification. Not a formal proof of absence of network leaks.
set -Eeuo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
export LXQT_PROJECT_ROOT="$ROOT"
source "$ROOT/scripts/cpu-affinity.sh"
lxqt_cpu_guard "$(lxqt_cpu_set)"
cd "$ROOT"
[[ -r config/settings.json && -r config/kubeconfig ]] || { echo 'Run configure.py and deploy.sh first' >&2; exit 1; }
NS="$(python3 -c 'import json;print(json.load(open("config/settings.json"))["namespace"])')"
K=(kubectl --kubeconfig "$ROOT/config/kubeconfig" -n "$NS")
printf 'Gluetun health check:\n'
"${K[@]}" exec deployment/lxqt-desktop -c gluetun -- /gluetun-entrypoint healthcheck
printf '\nDesktop egress IP (via its network namespace):\n'
POD_IP="$("${K[@]}" exec deployment/lxqt-desktop -c desktop -- curl --fail --silent --show-error --max-time 15 https://api.ipify.org)"
printf '%s\n' "$POD_IP"
if command -v curl >/dev/null; then
  HOST_IP="$(curl --fail --silent --show-error --max-time 15 https://api.ipify.org || true)"
  [[ -z "$HOST_IP" ]] || printf 'Host egress IP (for comparison): %s\n' "$HOST_IP"
  if [[ -n "$HOST_IP" && "$HOST_IP" == "$POD_IP" ]]; then
    echo 'WARNING: Host and Pod appear to use the same public IP. Investigate whether the host VPN is active or routing leaks exist.' >&2
  fi
fi
printf '\nNote: a healthy tunnel and different IP do not guarantee fail-closed behavior. Test disruption carefully.\n'

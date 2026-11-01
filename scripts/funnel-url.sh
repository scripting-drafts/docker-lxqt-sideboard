#!/usr/bin/env bash
# Print the assigned Tailscale Funnel URL with the noVNC page path appended.
set -Eeuo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
NS="$(python3 -c 'import json,sys;print(json.load(open(sys.argv[1]))["namespace"])' "$ROOT/config/settings.json")"
K=(kubectl --kubeconfig "$ROOT/config/kubeconfig" -n "$NS")
status="$("${K[@]}" exec deployment/lxqt-desktop -c tailscale -- tailscale funnel status 2>/dev/null)" || exit 1
url="$(printf '%s\n' "$status" | grep -oE 'https://[A-Za-z0-9.-]+\.ts\.net' | head -n 1 || true)"
[[ -n "$url" ]] || exit 1
path="${1:-/vnc.html}"
[[ "$path" == /* ]] || path="/$path"
printf '%s%s\n' "${url%/}" "$path"

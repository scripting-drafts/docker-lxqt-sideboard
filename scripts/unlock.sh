#!/usr/bin/env bash
set -Eeuo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
export LXQT_PROJECT_ROOT="$ROOT"
source "$ROOT/scripts/cpu-affinity.sh"
lxqt_cpu_guard "$(lxqt_cpu_set)"
[[ -e "$ROOT/config/LOCKED_DOWN" ]] || { echo 'Not locked'; exit 0; }
cat "$ROOT/config/LOCKED_DOWN"
read -r -p 'After investigation, type UNLOCK to permit redeployment: ' answer
[[ "$answer" == 'UNLOCK' ]] || exit 1
rm -f "$ROOT/config/LOCKED_DOWN"
echo 'Lock removed. Run scripts/deploy.sh; then enable Funnel manually.'

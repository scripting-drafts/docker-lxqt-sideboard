#!/usr/bin/env bash
# Observe Gluetun auto-healing; optionally repair an already-enabled Funnel.
set -Eeuo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
export LXQT_PROJECT_ROOT="$ROOT"
source "$ROOT/scripts/cpu-affinity.sh"
lxqt_cpu_guard "$(lxqt_cpu_set)"
exec 9>"$ROOT/config/.maintenance.lock"
flock -n 9 || { echo 'Another project maintenance operation is active.' >&2; exit 1; }
python3 "$ROOT/scripts/recover-vpn.py" "$@"

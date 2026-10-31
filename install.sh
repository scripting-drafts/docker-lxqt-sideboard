#!/usr/bin/env bash
set -Eeuo pipefail
# Kind is installed system-wide at /usr/local/bin/kind. Some non-login shells
# (including `sg docker -c`) inherit a restricted PATH, so normalize it before
# invoking the all-in-one installer and throughout its child processes.
export PATH="/usr/local/bin:$HOME/.local/bin:$PATH"
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
export LXQT_PROJECT_ROOT="$ROOT"
source "$ROOT/scripts/cpu-affinity.sh"
lxqt_cpu_guard "$(lxqt_cpu_set)"
exec "$ROOT/scripts/install-all.sh" "$@"

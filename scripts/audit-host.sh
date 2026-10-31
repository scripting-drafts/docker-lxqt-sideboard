#!/usr/bin/env bash
# Read-only inventory of software and kernel features managed by the LXQt Kind installer.
# Does not read or reveal Proton, Tailscale, Caddy, or VNC secrets.
set -Eeuo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
export LXQT_PROJECT_ROOT="$ROOT"
source "$ROOT/scripts/cpu-affinity.sh"
lxqt_cpu_guard "$(lxqt_cpu_set)"
export PATH="/usr/local/bin:$HOME/.local/bin:$PATH"
KIND_VERSION="${LXQT_KIND_VERSION:-v0.33.0}"
PY_VERSION="${LXQT_PYTHON_VERSION:-3.15.2}"
PY_YAML_VERSION="${LXQT_PYYAML_VERSION:-6.0.3}"
venv="$HOME/.local/share/lxqt-kind-python/bin/python"
row() { printf '%-22s %-28s %-24s %s\n' "$1" "$2" "$3" "$4"; }
pkg() {
  local installed candidate state
  installed="$(dpkg-query -W -f='${Version}' "$1" 2>/dev/null || true)"
  # Do not exit awk early: apt-cache may print a long version table.
  # With pipefail, an early awk exit causes apt-cache SIGPIPE (status 141),
  # silently aborting the audit after its table header on some hosts.
  if ! candidate="$(apt-cache policy "$1" 2>/dev/null | awk '$1=="Candidate:" && !found++ {print $2}')"; then
    row "$1" "${installed:-missing}" 'APT query failed' 'CHECK_APT'
    return 0
  fi
  if [[ -z "$installed" ]]; then state=MISSING
  elif [[ "$candidate" != '(none)' && -n "$candidate" ]] && dpkg --compare-versions "$candidate" gt "$installed"; then state=UPDATE_AVAILABLE
  elif [[ "$candidate" != '(none)' && -n "$candidate" ]] && dpkg --compare-versions "$installed" gt "$candidate"; then state=NEWER_THAN_REPOSITORY
  else state=OK; fi
  row "$1" "${installed:-missing}" "${candidate:-unknown}" "$state"
}
trap 'rc=$?; printf "ERROR: audit-host.sh at line %s (exit %s). No packages were modified.\n" "$LINENO" "$rc" >&2' ERR
row 'COMPONENT' 'INSTALLED' 'TARGET/CANDIDATE' 'STATUS' 
for name in docker-ce docker-ce-cli containerd.io docker-compose-plugin docker-buildx-plugin wireguard-tools iproute2 python3 python3-yaml; do pkg "$name"; done
for binary in docker kind kubectl; do
  case "$binary" in
    docker) installed="$(docker --version 2>/dev/null || echo missing)"; target='Docker Engine + healthy daemon';;
    kind) installed="$(kind version 2>/dev/null | awk '{print $2}' || true)"; target="$KIND_VERSION";;
    kubectl) installed="$(kubectl version --client -o json 2>/dev/null | python3 -c 'import json,sys; print(json.load(sys.stdin).get("clientVersion", {}).get("gitVersion", ""))' 2>/dev/null || true)"; target='latest stable Kubernetes client';;
  esac
  if [[ "$binary" == kind ]]; then
    [[ "$installed" == "$target" ]] && state=OK || state=RECONCILE
  else
    [[ -n "$installed" && "$installed" != missing ]] && state=PRESENT || state=MISSING
  fi
  row "$binary command" "${installed:-missing}" "$target" "$state"
done
if command -v docker >/dev/null && sudo -n docker --context default info >/dev/null 2>&1; then
  row 'Docker daemon' 'running' 'healthy' OK
else
  row 'Docker daemon' 'unverified' 'healthy' CHECK_OR_REPAIR
fi
if [[ -x "$venv" ]]; then
  installed="$($venv -c 'import sys; print(sys.version.split()[0])' 2>/dev/null || true)"
  yaml="$($venv -c 'import yaml; print(yaml.__version__)' 2>/dev/null || true)"
else
  installed=missing; yaml=missing
fi
[[ "$installed" == "$PY_VERSION" ]] && state=OK || state=RECONCILE
row 'Project Python' "$installed" "$PY_VERSION" "$state"
[[ "$yaml" == "$PY_YAML_VERSION" ]] && state=OK || state=RECONCILE
row 'Project PyYAML' "$yaml" "$PY_YAML_VERSION" "$state"
if [[ -c /dev/net/tun ]]; then row '/dev/net/tun' 'character device' '10:200' PRESENT
else row '/dev/net/tun' 'missing/invalid' '10:200' REPAIR; fi
printf '\nRead-only inventory. Repository candidate versions reflect local APT indexes; they are refreshed during installation.\n'

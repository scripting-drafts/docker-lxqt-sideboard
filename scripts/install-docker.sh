#!/usr/bin/env bash
# Install or repair Docker Engine (not merely the CLI) on supported Ubuntu hosts.
# Opt-in for changing intentionally masked systemd units or Docker group membership.
set -Eeuo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
export LXQT_PROJECT_ROOT="$ROOT"
source "$ROOT/scripts/cpu-affinity.sh"
lxqt_cpu_guard "$(lxqt_cpu_set)"
DRY_RUN=0
REPAIR=0
ADD_GROUP=0
while (($#)); do
  case "$1" in
    --dry-run) DRY_RUN=1 ;;
    --repair-docker) REPAIR=1 ;;
    --add-docker-group) ADD_GROUP=1 ;;
    --help)
      cat <<'USAGE'
Usage: ./scripts/install-docker.sh [--dry-run] [--repair-docker] [--add-docker-group]
Run as a normal Ubuntu user; sudo is requested only for system changes.
 --dry-run           Show current state and planned actions; make no changes.
 --repair-docker     Explicitly allow unmasking Docker systemd units (including docker.socket).
 --add-docker-group  Add current user to docker group (ROOT-EQUIVALENT privilege; re-login required).
Existing Docker data is not deleted. No conflicting packages are automatically removed.
USAGE
      exit 0 ;;
    *) echo "Unknown argument: $1" >&2; exit 2 ;;
  esac
  shift
done
[[ "$(id -u)" != 0 ]] || { echo 'Run as a regular user, not sudo.' >&2; exit 1; }
source /etc/os-release
[[ "${ID:-}" == ubuntu ]] || { echo 'Supported only on Ubuntu, not derivatives.' >&2; exit 1; }
case "${VERSION_ID:-}" in 22.04|24.04|26.04) ;; *) echo "Unsupported Ubuntu release: ${VERSION_ID:-unknown}" >&2; exit 1 ;; esac
if [[ "${DOCKER_HOST:-}" != '' ]]; then
  echo "DOCKER_HOST is set; this installer targets the local Docker Engine. Unset DOCKER_HOST and review contexts first." >&2
  exit 1
fi
if command -v docker >/dev/null && [[ "$(docker context show 2>/dev/null || true)" != default ]]; then
  echo "Docker context is not 'default'. Review existing Docker Desktop/remote workloads before proceeding; use 'docker context use default' explicitly." >&2
  exit 1
fi
is_installed() { dpkg-query -W -f='${Status}' "$1" 2>/dev/null | grep -qx 'install ok installed'; }
unit_state() { systemctl is-enabled "$1" 2>/dev/null || true; }
masked() { case "$(unit_state "$1")" in masked|masked-runtime) return 0 ;; *) return 1 ;; esac; }
unit_path() { systemctl show -p FragmentPath --value docker.service 2>/dev/null || true; }
engine_present() { [[ -x /usr/bin/dockerd ]] && is_installed docker-ce; }
packages=(docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin)
missing=()
for p in "${packages[@]}"; do is_installed "$p" || missing+=("$p"); done
needs_reinstall=0
repair_plugins=()
if is_installed docker-compose-plugin && ! docker compose version >/dev/null 2>&1; then
  repair_plugins+=(docker-compose-plugin)
fi
if is_installed docker-buildx-plugin && ! docker buildx version >/dev/null 2>&1; then
  repair_plugins+=(docker-buildx-plugin)
fi
if is_installed docker-ce-cli && ! docker --version >/dev/null 2>&1; then
  repair_plugins+=(docker-ce-cli)
fi
# Compare installed Docker-managed package versions to current APT candidate versions.
# APT metadata is refreshed by bootstrap-host.sh before this script is called.
updates=()
for p in "${packages[@]}"; do
  current="$(dpkg-query -W -f='${Version}' "$p" 2>/dev/null || true)"
  # Read all output to avoid SIGPIPE/141 with Bash pipefail on hosts
  # whose Docker apt repositories contain a large version table.
  if ! candidate="$(apt-cache policy "$p" | awk '$1=="Candidate:" && !found++ {print $2}')"; then
    printf 'ERROR: Could not query APT candidate for %s; run apt-cache policy %q\n' "$p" "$p" >&2
    exit 1
  fi
  if [[ -n "$current" && -n "$candidate" && "$candidate" != '(none)' ]] && dpkg --compare-versions "$candidate" gt "$current"; then
    updates+=("$p")
  fi
done
if is_installed docker-ce; then
  # Missing binary OR legacy-only generated service can indicate a broken CE package.
  case "$(unit_path)" in
    /lib/systemd/system/docker.service|/usr/lib/systemd/system/docker.service)
      [[ -x /usr/bin/dockerd ]] || needs_reinstall=1 ;;
    *) needs_reinstall=1 ;;
  esac
fi
if [[ "$DRY_RUN" == 1 ]]; then
  printf 'Docker diagnostic (read-only):\n  Ubuntu: %s\n  docker CLI: %s\n  dockerd /usr/bin: %s\n  Docker CE package installed: %s\n  Missing packages: %s\n  docker.service: %s\n  docker.socket: %s\n  Service unit path: %s\n  Docker group requested: %s\n' \
    "$VERSION_ID" "$(command -v docker || echo missing)" \
    "$(test -x /usr/bin/dockerd && echo present || echo MISSING)" \
    "$(is_installed docker-ce && echo yes || echo no)" "${missing[*]:-none}" \
    "$(unit_state docker.service)" "$(unit_state docker.socket)" "$(unit_path)" "$ADD_GROUP"
  printf 'Upgrade available for Docker packages: %s\n' "${updates[*]:-none}"
  printf 'Damaged Docker plugins to reinstall: %s\n' "${repair_plugins[*]:-none}"
  echo 'Plan: reconcile installed Docker packages with available repository versions; preserve existing Docker volumes and images.'
  if masked docker.service || masked docker.socket; then
    echo 'Masked unit detected. Use --repair-docker to authorize unmasking (not done automatically).'
  fi
  exit 0
fi
# Refuse to change packages if a deliberate Docker lockdown is present unless the
# caller explicitly opts into Docker service repair. Check before APT mutations.
if masked docker.service || masked docker.socket; then
  if [[ "$REPAIR" != 1 ]]; then
    echo 'Docker service/socket is masked; --repair-docker is required before modifying Docker packages.' >&2
    exit 1
  fi
fi
sudo -v
if (( ${#missing[@]} > 0 || ${#updates[@]} > 0 || ${#repair_plugins[@]} > 0 )) || [[ "$needs_reinstall" == 1 ]]; then
  conflicts=()
  for pkg in docker.io docker-compose docker-compose-v2 podman-docker containerd runc moby-engine moby-cli; do
    if is_installed "$pkg"; then conflicts+=("$pkg"); fi
  done
  if (( ${#conflicts[@]} > 0 )); then
    printf 'Potential Docker CE conflicts: %s\n' "${conflicts[*]}" >&2
    echo 'Refusing automated package replacement. Review workloads and migration manually.' >&2
    exit 1
  fi
  printf 'Docker changes found — missing: [%s]; newer packages: [%s]; damaged plugins: [%s]\n' \
    "${missing[*]:-none}" "${updates[*]:-none}" "${repair_plugins[*]:-none}"
  echo 'WARNING: Upgrading Docker Engine can restart dockerd and briefly affect existing workloads.'
  # Use an already-configured official source, otherwise add Docker's verified apt repository.
  if ! grep -RqlF 'download.docker.com/linux/ubuntu' /etc/apt/sources.list /etc/apt/sources.list.d 2>/dev/null; then
    sudo apt-get update
    sudo DEBIAN_FRONTEND=noninteractive apt-get install -y ca-certificates curl gnupg
    sudo install -m 0755 -d /etc/apt/keyrings
    keyfile="$(mktemp)"
    trap 'rm -f "${keyfile:-}" "${planfile:-}"' EXIT
    curl --proto '=https' --tlsv1.2 -fsSL https://download.docker.com/linux/ubuntu/gpg -o "$keyfile"
    fingerprint="$(gpg --show-keys --with-colons "$keyfile" | awk -F: '$1=="fpr" && !found++ {print $10}')"
    [[ "$fingerprint" == 9DC858229FC7DD38854AE2D88D81803C0EBFCD88 ]] || {
      echo 'Docker apt key fingerprint mismatch; refusing install.' >&2; exit 1;
    }
    sudo install -m 0644 "$keyfile" /etc/apt/keyrings/docker.asc
    cat <<EOF | sudo tee /etc/apt/sources.list.d/docker.sources >/dev/null
Types: deb
URIs: https://download.docker.com/linux/ubuntu
Suites: ${UBUNTU_CODENAME:-$VERSION_CODENAME}
Components: stable
Architectures: $(dpkg --print-architecture)
Signed-By: /etc/apt/keyrings/docker.asc
EOF
    rm -f "$keyfile"
  fi
  sudo apt-get update
  planfile="$(mktemp)"
  trap 'rm -f "${keyfile:-}" "${planfile:-}"' EXIT
  if ! LC_ALL=C sudo apt-get -s install "${packages[@]}" >"$planfile" 2>&1; then
    cat "$planfile" >&2; exit 1
  fi
  if grep -Eq '^Remv ' "$planfile"; then
    cat "$planfile" >&2
    echo 'APT plans to remove installed packages; aborting. Review with administrator.' >&2
    exit 1
  fi
  sudo DEBIAN_FRONTEND=noninteractive apt-get install -y "${packages[@]}"
  if (( ${#repair_plugins[@]} > 0 )); then
    if ! LC_ALL=C sudo apt-get -s install --reinstall "${repair_plugins[@]}" >"$planfile" 2>&1; then
      cat "$planfile" >&2; exit 1
    fi
    if grep -Eq '^Remv ' "$planfile"; then cat "$planfile" >&2; exit 1; fi
    sudo DEBIAN_FRONTEND=noninteractive apt-get install -y --reinstall "${repair_plugins[@]}"
  fi
  if [[ "$needs_reinstall" == 1 ]]; then
    echo 'docker-ce package is installed but its daemon executable or native unit is missing: reinstalling Docker Engine.'
    if ! LC_ALL=C sudo apt-get -s install --reinstall docker-ce >"$planfile" 2>&1; then
      cat "$planfile" >&2; exit 1
    fi
    if grep -Eq '^Remv ' "$planfile"; then cat "$planfile" >&2; exit 1; fi
    sudo DEBIAN_FRONTEND=noninteractive apt-get install -y --reinstall docker-ce
  fi
fi
[[ -x /usr/bin/dockerd ]] || {
  echo 'ERROR: /usr/bin/dockerd still missing. Check apt/dpkg logs and package ownership.' >&2
  exit 1
}
sudo systemctl daemon-reload
case "$(unit_path)" in
  /lib/systemd/system/docker.service|/usr/lib/systemd/system/docker.service) ;;
  *) echo "Expected native Docker unit, found '$(unit_path)'. Refusing legacy SysV-only startup." >&2; exit 1 ;;
esac
if masked docker.service || masked docker.socket; then
  if [[ "$REPAIR" != 1 ]]; then
    echo 'Docker systemd service/socket is masked (possibly intentionally).' >&2
    echo 'After confirming Docker Engine should run, rerun with --repair-docker to unmask it.' >&2
    exit 1
  fi
  echo 'Unmasking Docker service/socket at your explicit request ...'
  sudo systemctl unmask docker.service docker.socket
  sudo systemctl daemon-reload
fi
sudo systemctl enable --now containerd.service
sudo systemctl enable --now docker.service
if ! sudo docker --context default info >/dev/null; then
  echo 'Docker Engine failed health check: sudo docker --context default info' >&2
  sudo journalctl -u docker.service -b -n 50 --no-pager >&2 || true
  exit 1
fi
printf 'Docker Engine operational: %s\n' "$(sudo docker --context default version --format '{{.Server.Version}}')"
if [[ "$ADD_GROUP" == 1 ]] && ! id -nG | grep -qw docker; then
  echo 'WARNING: Membership in docker group grants root-level host privileges.'
  sudo usermod -aG docker "$(id -un)"
  echo 'Docker group added. Log out and back in (or start a new group-enabled shell) before configure.py.'
elif ! docker --context default info >/dev/null 2>&1; then
  echo 'Docker works with sudo but not your current user. Use --add-docker-group (root-equivalent) if acceptable, then re-login.'
else
  echo 'Current user can access Docker Engine.'
fi

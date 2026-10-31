#!/usr/bin/env bash
# Ubuntu host bootstrap for LXQt Kind. No cluster or desktop is deployed by this installer.
set -Eeuo pipefail
export PATH="/usr/local/bin:$HOME/.local/bin:$PATH"
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
export LXQT_PROJECT_ROOT="$ROOT"
source "$ROOT/scripts/cpu-affinity.sh"
lxqt_cpu_guard "$(lxqt_cpu_set)"
DRY_RUN=0
ADD_GROUP=0
SKIP_PYTHON=0
SKIP_TUN_CONTAINER=0
REPAIR_DOCKER=0
KIND_VERSION="${LXQT_KIND_VERSION:-v0.33.0}"
while (($#)); do
  case "$1" in
    --dry-run) DRY_RUN=1 ;;
    --add-docker-group) ADD_GROUP=1 ;;
    --skip-python) SKIP_PYTHON=1 ;;
    --repair-docker) REPAIR_DOCKER=1 ;;
    --skip-tun-container-test) SKIP_TUN_CONTAINER=1 ;;
    --help) cat <<'USAGE'
Usage: ./scripts/bootstrap-host.sh [--dry-run] [--add-docker-group] [--repair-docker] [--skip-python] [--skip-tun-container-test]
 Installs Docker Engine + Compose, kind, kubectl, Python 3.15.2 + PyYAML, and validates TUN.
 Run as the normal user, not sudo. sudo is requested for OS-level changes.
 --add-docker-group grants root-equivalent Docker access to this user. Takes effect after re-login.
 --dry-run explains planned actions without executing them.
USAGE
        exit 0 ;;
    *) echo "Unknown argument: $1" >&2; exit 2 ;;
  esac
  shift
done
[[ "$(id -u)" != 0 ]] || { echo 'Run as regular user (script invokes sudo for system changes).' >&2; exit 1; }
source /etc/os-release
[[ "${ID:-}" == 'ubuntu' ]] || { echo 'Supported: Ubuntu 22.04/24.04/26.04 (not Ubuntu-derived distributions).' >&2; exit 1; }
case "${VERSION_ID:-}" in 22.04|24.04|26.04) ;; *) echo "Ubuntu $VERSION_ID not validated; refusing unattended install." >&2; exit 1 ;; esac
case "$(uname -m)" in x86_64) ARCH=amd64 ;; aarch64) ARCH=arm64 ;; *) echo 'Unsupported architecture.' >&2; exit 1 ;; esac
[[ "$KIND_VERSION" =~ ^v[0-9]+\.[0-9]+\.[0-9]+$ ]] || { echo 'Invalid LXQT_KIND_VERSION.' >&2; exit 1; }
if [[ "$DRY_RUN" == 1 ]]; then
  "$ROOT/scripts/audit-host.sh"
  cat <<EOF2
DRY RUN — no changes made.
 Host: Ubuntu ${VERSION_ID}, architecture ${ARCH}
 1. apt base tools, Ubuntu system Python 3 / python3-yaml, wireguard-tools, iproute2 and build dependencies.
 2. Verify or repair the official Docker Engine daemon and Compose using scripts/install-docker.sh.
    Masked Docker services require explicit --repair-docker; conflicts cause a safe refusal.
 3. Verify Kind against ${KIND_VERSION}; install/replace with checksum validation when version differs.
 4. Compare kubectl with upstream latest stable; install/replace with checksum validation if different.
 5. Reconcile Python 3.15.2 and PyYAML 6.0.3, repairing a missing or changed environment while preserving backups.
 6. Verify /dev/net/tun, modprobe if missing, create missing 10:200 device only if needed.
 7. Optionally test TUNSETIFF inside a Docker --network none container.
 Docker group access requested: ${ADD_GROUP} (root-equivalent permission).
 Python installation skipped: ${SKIP_PYTHON}.
EOF2
  exit 0
fi
# Inventory before reconciliation; this is safe to rerun for existing installations.
echo '=== Software inventory before reconciliation ==='
"$ROOT/scripts/audit-host.sh"
sudo -v
sudo apt-get update
sudo DEBIAN_FRONTEND=noninteractive apt-get install -y \
  ca-certificates curl gnupg kmod util-linux xz-utils tar coreutils python3 python3-yaml \
  wireguard-tools iproute2 \
  gcc make build-essential pkg-config zlib1g-dev libssl-dev libbz2-dev \
  libreadline-dev libsqlite3-dev libffi-dev liblzma-dev libncursesw5-dev \
  libgdbm-dev libnss3-dev libexpat1-dev uuid-dev tk-dev libdb-dev
# Verified, non-destructive official Docker Engine repair/installation from user-supplied v6 script.
DOCKER_ARGS=()
[[ "$REPAIR_DOCKER" == 1 ]] && DOCKER_ARGS+=(--repair-docker)
[[ "$ADD_GROUP" == 1 ]] && DOCKER_ARGS+=(--add-docker-group)
"$ROOT/scripts/install-docker.sh" "${DOCKER_ARGS[@]}"
# Reconcile required Kind version rather than merely checking whether it starts.
# Install in /usr/local/bin to avoid changing unrelated apt/snap installations.
kind_current="$(kind version 2>/dev/null | awk '{print $2}' || true)"
if [[ "$kind_current" != "$KIND_VERSION" ]]; then
  printf 'Kind change detected: %s -> %s\n' "${kind_current:-missing}" "$KIND_VERSION"
  echo "Installing Kind ${KIND_VERSION} ..."
  TMP="$(mktemp -d)"; trap 'rm -rf "$TMP"' EXIT
  ASSET="kind-linux-${ARCH}"
  curl --proto '=https' --tlsv1.2 -fL --retry 3 -o "$TMP/$ASSET" "https://github.com/kubernetes-sigs/kind/releases/download/${KIND_VERSION}/${ASSET}"
  curl --proto '=https' --tlsv1.2 -fL --retry 3 -o "$TMP/$ASSET.sha256sum" "https://github.com/kubernetes-sigs/kind/releases/download/${KIND_VERSION}/${ASSET}.sha256sum"
  (cd "$TMP" && sha256sum -c "${ASSET}.sha256sum")
  if [[ -e /usr/local/bin/kind ]]; then
    sudo cp -p /usr/local/bin/kind "/usr/local/bin/kind.lxqt-backup-$(date +%Y%m%d%H%M%S)"
  fi
  sudo install -m 0755 "$TMP/$ASSET" /usr/local/bin/kind
  hash -r
  [[ "$(kind version 2>/dev/null | awk '{print $2}')" == "$KIND_VERSION" ]] || {
    echo 'Kind installation did not produce the expected version.' >&2; exit 1;
  }
  rm -rf "$TMP"
fi
# Compare kubectl with upstream stable, and replace only when different.
# A locally installed previous binary is copied aside before replacement.
echo 'Checking current stable kubectl release ...'
K_VERSION="$(curl --proto '=https' --tlsv1.2 -fsSL https://dl.k8s.io/release/stable.txt)"
[[ "$K_VERSION" =~ ^v1\.[0-9]+\.[0-9]+$ ]] || { echo "Unexpected kubectl version: $K_VERSION" >&2; exit 1; }
K_INSTALLED="$(kubectl version --client -o json 2>/dev/null | python3 -c 'import json,sys; print(json.load(sys.stdin).get("clientVersion", {}).get("gitVersion", ""))' 2>/dev/null || true)"
if [[ "$K_INSTALLED" != "$K_VERSION" ]]; then
  printf 'kubectl change detected: %s -> %s\n' "${K_INSTALLED:-missing/broken}" "$K_VERSION"
  TMP="$(mktemp -d)"; trap 'rm -rf "$TMP"' EXIT
  BASE="https://dl.k8s.io/release/${K_VERSION}/bin/linux/${ARCH}"
  curl --proto '=https' --tlsv1.2 -fL --retry 3 -o "$TMP/kubectl" "$BASE/kubectl"
  curl --proto '=https' --tlsv1.2 -fL --retry 3 -o "$TMP/kubectl.sha256" "$BASE/kubectl.sha256"
  (cd "$TMP" && echo "$(cat kubectl.sha256)  kubectl" | sha256sum -c -)
  if [[ -e /usr/local/bin/kubectl ]]; then
    sudo cp -p /usr/local/bin/kubectl "/usr/local/bin/kubectl.lxqt-backup-$(date +%Y%m%d%H%M%S)"
  fi
  sudo install -m 0755 "$TMP/kubectl" /usr/local/bin/kubectl
  hash -r
  [[ "$(kubectl version --client -o json 2>/dev/null | python3 -c 'import json,sys; print(json.load(sys.stdin).get("clientVersion", {}).get("gitVersion", ""))' 2>/dev/null || true)" == "$K_VERSION" ]] || {
    echo 'kubectl installation did not produce the expected version.' >&2; exit 1;
  }
  rm -rf "$TMP"
fi
hash -r
[[ -x /usr/local/bin/kind || -n "$(command -v kind 2>/dev/null)" ]] || {
  echo 'Kind installation failed: missing /usr/local/bin/kind.' >&2; exit 1;
}
kind version >/dev/null || { echo 'Kind command failed after installation.' >&2; exit 1; }
kubectl version --client >/dev/null || { echo 'kubectl command failed after installation.' >&2; exit 1; }
if [[ "$SKIP_PYTHON" == 0 ]]; then
  "$ROOT/scripts/install-python.sh"
fi
if [[ "$SKIP_TUN_CONTAINER" == 1 ]]; then
  "$ROOT/scripts/verify-tun.sh" --repair
else
  "$ROOT/scripts/verify-tun.sh" --repair --container-test
fi
echo '=== Software inventory after reconciliation ==='
"$ROOT/scripts/audit-host.sh"
printf '\nHost bootstrap finished. Installed tools:\n' 
docker --version
docker compose version
kind version
kubectl version --client
if ! docker --context default info >/dev/null 2>&1; then
  echo 'Docker daemon is healthy but not accessible in this shell. If membership was just added, the all-in-one installer can re-enter the docker group automatically.'
fi
printf 'Next: open new shell, verify python --version, then python scripts/configure.py and ./scripts/deploy.sh\n'

#!/usr/bin/env bash
# Proton VPN Free + WireGuard integration for Gluetun/Kubernetes.
# May reconcile host WireGuard profiles when their key collides with the Pod.
set -Eeuo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
export LXQT_PROJECT_ROOT="$ROOT"
source "$ROOT/scripts/cpu-affinity.sh"
lxqt_cpu_guard "$(lxqt_cpu_set)"
CONFIG_FILE=""
HOST_PROFILE="${LXQT_HOST_PROFILE:-}"
CHECK_TUN=1
while (($#)); do
  case "$1" in
    --config)
      [[ $# -ge 2 && -n "$2" && "$2" != --* ]] || { echo '--config requires a filename or path' >&2; exit 2; }
      [[ -z "$CONFIG_FILE" ]] || { echo '--config specified more than once' >&2; exit 2; }
      CONFIG_FILE="$2"; shift 2 ;;
    --host-profile) [[ $# -ge 2 ]] || { echo '--host-profile requires a name' >&2; exit 2; }; HOST_PROFILE="$2"; shift 2 ;;
    --skip-tun-test) CHECK_TUN=0; shift ;;
    --help)
      cat <<'EOF'
Usage: ./scripts/setup-proton.sh --config FILE [--host-profile NAME] [--skip-tun-test]
- Installs Ubuntu WireGuard utilities if needed.
- Validates /dev/net/tun and optionally tests its ioctl inside a disposable container.
- Parses Proton's downloaded .conf and imports its private key as a protected Gluetun secret.
- Verifies host/Pod key separation; may disconnect a conflicting host WireGuard profile and activate a non-conflicting one.
- Never uploads credentials or prints private keys.
- Requires Docker for --container-test; run ./scripts/bootstrap-host.sh first.
EOF
      exit 0 ;;
    *) echo "Unknown option: $1" >&2; exit 2 ;;
  esac
done
[[ -n "$CONFIG_FILE" ]] || { echo 'ERROR: --config FILE is required; no default filename is assumed.' >&2; exit 2; }
[[ -f "$CONFIG_FILE" ]] || { echo "Missing Proton .conf: $CONFIG_FILE" >&2; echo 'Download one from https://account.proton.me/u/0/vpn/WireGuard' >&2; exit 1; }
if ! command -v wg >/dev/null || ! command -v ip >/dev/null; then
  command -v apt-get >/dev/null || { echo 'Install wireguard-tools/iproute2 using your package manager.' >&2; exit 1; }
  sudo apt-get update
  sudo apt-get install -y wireguard-tools iproute2
fi
if ((CHECK_TUN)); then
  "$ROOT/scripts/verify-tun.sh" --repair --container-test
fi
cmd=(python3 "$ROOT/scripts/proton-wireguard.py" import-pod "$CONFIG_FILE")
[[ -n "$HOST_PROFILE" ]] && cmd+=(--host-profile "$HOST_PROFILE")
"${cmd[@]}"
if ! python3 "$ROOT/scripts/proton-wireguard.py" check-host; then
  echo 'Configuration imported, but host/Pod WireGuard key separation could not be secured.' >&2
  echo 'Review the host WireGuard profiles before deploying.' >&2
fi
printf '\nProton configuration prepared. Next: python3 scripts/configure.py and ./scripts/deploy.sh\n'

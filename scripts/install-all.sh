#!/usr/bin/env bash
# One entry point for host bootstrap, secure configuration, Kind deployment and Funnel.
# Run as the regular login user; never as root. Passwords are always requested interactively.
set -Eeuo pipefail
# Explicitly cover restricted non-login shells when re-entered via `sg docker`.
export PATH="/usr/local/bin:$HOME/.local/bin:$PATH"
CALLER_DIR="$PWD"
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
export LXQT_PROJECT_ROOT="$ROOT"
source "$ROOT/scripts/cpu-affinity.sh"
lxqt_cpu_guard "$(lxqt_cpu_set)"
WIREGUARD_CONFIG=""
HOST_PROFILE="${LXQT_HOST_PROFILE:-}"
BOOTSTRAP_CPUS="${LXQT_PYTHON_BUILD_CPUSET:-$(lxqt_cpu_set)}"
ADD_GROUP=0
REPAIR_DOCKER=0
SKIP_PYTHON=0
SKIP_TIMERS=0
SKIP_FUNNEL=0
DRY_RUN=0
RESUME=0
ORIGINAL_ARGS=("$@")
usage() {
  cat <<'HELP'
Usage: ./install.sh [options]
       ./install.sh --wireguard-config FILE [options]
  (no arguments)           Use ~/Documents/wireguard.conf or prompt if absent
  --wireguard-config FILE   Optional custom Proton WireGuard .conf path
  --host-profile NAME       Optional existing host NetworkManager connection name (independent of file name)
  --bootstrap-cpus SET     CPU set for Python compilation (default: 0,1,3-7,9-11)
  --add-docker-group       Explicitly grant root-equivalent Docker-group access (otherwise ask if needed)
  --repair-docker          Explicitly allow unmasking masked Docker service/socket
  --skip-python            Keep existing system Python 3; don't compile separate newer Python
  --skip-timers            Don't install user-systemd security watchdog / weekly updates
  --no-funnel              Deploy privately but don't enable the public Tailscale URL yet
  --dry-run                Display prerequisites and intended phases, change nothing
  --help                   Show this help

Configuration is prompted before Kubernetes deployment. Tailscale auth key, VNC password,
Proton WireGuard private key, and browser password are never passed on the command line.
Manual download of the Proton WireGuard .conf and Tailscale account authorization remain required.
Relative FILE paths resolve against the directory where ./install.sh was invoked.
If the default file does not exist, you will be asked for its path before host changes.
The script will NEVER activate or disconnect a host VPN.
HELP
}
while (( $# )); do
  case "$1" in
    --wireguard-config)
      [[ $# -ge 2 && -n "$2" && "$2" != --* ]] || { echo '--wireguard-config requires a filename or path' >&2; exit 2; }
      [[ -z "$WIREGUARD_CONFIG" ]] || { echo 'WireGuard filename specified more than once' >&2; exit 2; }
      WIREGUARD_CONFIG="$2"; shift 2;;
    --host-profile) [[ $# -ge 2 ]] || { echo 'Missing --host-profile name' >&2; exit 2; }; HOST_PROFILE="$2"; shift 2;;
    --bootstrap-cpus) [[ $# -ge 2 ]] || { echo "Missing --bootstrap-cpus value" >&2; exit 2; }; BOOTSTRAP_CPUS="$2"; shift 2;;
    --add-docker-group) ADD_GROUP=1; shift;;
    --repair-docker) REPAIR_DOCKER=1; shift;;
    --skip-python) SKIP_PYTHON=1; shift;;
    --skip-timers) SKIP_TIMERS=1; shift;;
    --no-funnel) SKIP_FUNNEL=1; shift;;
    --dry-run) DRY_RUN=1; shift;;
    --help) usage; exit 0;;
    --resume-after-group) RESUME=1; shift;;
    --*) printf 'Unknown argument: %q\n' "$1" >&2; exit 2;;
    *)
      [[ -z "$WIREGUARD_CONFIG" ]] || { echo 'WireGuard filename specified more than once' >&2; exit 2; }
      WIREGUARD_CONFIG="$1"; shift;;
  esac
done
# No-argument installation: prefer the documented path, and only ask if absent.
# Never try to infer the actual Proton key from NetworkManager or copy a host profile.
if [[ -z "$WIREGUARD_CONFIG" ]]; then
  WIREGUARD_CONFIG="$HOME/Documents/wireguard.conf"
  WG_FROM_DEFAULT=1
else
  WG_FROM_DEFAULT=0
fi
[[ $(id -u) -ne 0 ]] || { echo 'Run as your normal Ubuntu user, never sudo ./install.sh' >&2; exit 1; }
[[ "$BOOTSTRAP_CPUS" =~ ^[0-9,\-]+$ ]] || { echo "Invalid --bootstrap-cpus" >&2; exit 2; }
lxqt_cpu_expand "$BOOTSTRAP_CPUS" >/dev/null || exit 2
if [[ -n "$WIREGUARD_CONFIG" ]]; then
  [[ "$WIREGUARD_CONFIG" == /* ]] || WIREGUARD_CONFIG="$CALLER_DIR/$WIREGUARD_CONFIG"
  WIREGUARD_CONFIG="$(realpath -m -- "$WIREGUARD_CONFIG")"
fi
step() { printf '\n\n========== %s ==========\n' "$*"; }
if (( DRY_RUN )); then
  step 'DRY RUN — inventory and reconciliation plan; no changes'
  "$ROOT/scripts/bootstrap-host.sh" --dry-run
  "$ROOT/scripts/install-docker.sh" --dry-run
  printf 'WireGuard filename: %s%s\n' "$WIREGUARD_CONFIG" "$( [[ -f "$WIREGUARD_CONFIG" ]] || printf ' (missing: installer will ask for a path)' )"
  printf 'Python build CPU affinity: %s\n' "$BOOTSTRAP_CPUS"
  printf 'Optional host profile hint: %s\n' "${HOST_PROFILE:-<none; host and Pod keys are compared directly>}"
  printf 'Will reconcile missing/changed software first, then run interactive credential wizard and deploy Kind.\n'
  printf 'Timers enabled: %s; Funnel enabled: %s\n' "$((1-SKIP_TIMERS))" "$((1-SKIP_FUNNEL))"
  exit 0
fi
# A missing default file is recovered by an interactive prompt, before any
# privileged installation. An explicitly named missing file remains a hard error.
if [[ ! -f "$WIREGUARD_CONFIG" ]] && (( WG_FROM_DEFAULT )); then
  printf 'No Proton WireGuard configuration found at: %s\n' "$WIREGUARD_CONFIG"
  while :; do
    printf 'Enter the path to your downloaded Proton WireGuard .conf (Ctrl-D to cancel): '
    if ! IFS= read -r candidate; then
      echo 'Cancelled: a WireGuard configuration is required before installation.' >&2
      exit 2
    fi
    [[ -n "$candidate" ]] || { echo 'A file path is required.' >&2; continue; }
    case "$candidate" in
      '~') candidate="$HOME" ;;
      '~/'*) candidate="$HOME/${candidate#\~/}" ;;
    esac
    [[ "$candidate" == /* ]] || candidate="$CALLER_DIR/$candidate"
    candidate="$(realpath -m -- "$candidate")"
    if [[ -f "$candidate" && -r "$candidate" ]]; then
      WIREGUARD_CONFIG="$candidate"
      break
    fi
    printf 'File not found or unreadable: %s\n' "$candidate" >&2
  done
fi
if [[ ! -f "$WIREGUARD_CONFIG" || ! -r "$WIREGUARD_CONFIG" ]]; then
  printf 'ERROR: Proton WireGuard configuration not found or not readable: %s\n' "$WIREGUARD_CONFIG" >&2
  exit 1
fi
# Docker group access is a root-equivalent privilege. Never grant it silently.
# Existing membership may only need a group-refresh (sg docker) later.
if (( ! RESUME && ! ADD_GROUP )) && ! docker --context default info >/dev/null 2>&1; then
  if id -nG "$(id -un)" | tr ' ' '\n' | grep -qx docker; then
    ADD_GROUP=1  # already granted by administrator; allow re-entry if needed
  else
    printf '\nYour account cannot currently access the local Docker daemon.\n'
    printf 'Adding your user to the docker group grants ROOT-EQUIVALENT host privileges.\n'
    printf 'Authorize Docker-group enrollment? [y/N]: '
    if ! IFS= read -r choice; then
      echo 'Cancelled: Docker access must be configured before deployment.' >&2
      exit 1
    fi
    case "$choice" in
      [yY]|[yY][eE][sS]) ADD_GROUP=1 ;;
      *) echo 'Docker-group access declined. Configure Docker access yourself before rerunning ./install.sh.' >&2; exit 1 ;;
    esac
  fi
fi
if (( ! RESUME )); then
  step '1/7 — Audit and reconcile Docker, Kind, kubectl, Python/PyYAML, WireGuard and TUN'
  BOOTSTRAP_ARGS=()
  (( ADD_GROUP )) && BOOTSTRAP_ARGS+=(--add-docker-group)
  (( REPAIR_DOCKER )) && BOOTSTRAP_ARGS+=(--repair-docker)
  (( SKIP_PYTHON )) && BOOTSTRAP_ARGS+=(--skip-python)
  LXQT_PYTHON_BUILD_CPUSET="$BOOTSTRAP_CPUS" "$ROOT/scripts/bootstrap-host.sh" "${BOOTSTRAP_ARGS[@]}"
fi
# Kind/kubectl come from bootstrap-host.sh. Verify before any configuration
# or deployment: otherwise downstream scripts report a misleading missing
# config/kubeconfig after cluster creation never happened.
hash -r
if ! command -v kind >/dev/null 2>&1; then
  echo 'Kind is not on PATH after bootstrap. Expected /usr/local/bin/kind; installation stopped before Kubernetes deployment.' >&2
  exit 1
fi
if ! kind version >/dev/null 2>&1; then
  echo "Kind was found at $(command -v kind), but is not executable/healthy. Installation stopped." >&2
  exit 1
fi
if ! command -v kubectl >/dev/null 2>&1 || ! kubectl version --client >/dev/null 2>&1; then
  echo 'kubectl is missing or unusable after bootstrap. Installation stopped before Kubernetes deployment.' >&2
  exit 1
fi
# Guarantee future interactive shells can find Kind without manually exporting
# PATH. This block is idempotent and avoids replacing the user's profile.
if (( ! RESUME )); then
  for profile in "$HOME/.profile" "$HOME/.bashrc"; do
    if [[ ! -e "$profile" ]] || [[ -f "$profile" && -w "$profile" ]]; then
      if ! grep -qF '# LXQt Kind Kubernetes CLI path' "$profile" 2>/dev/null; then
        {
          printf '\n# LXQt Kind Kubernetes CLI path\n'
          printf '%s\n' 'export PATH="/usr/local/bin:$HOME/.local/bin:$PATH"'
        } >> "$profile"
      fi
    else
      echo "WARNING: cannot update $profile; CLI paths are available in this installer process." >&2
    fi
  done
fi
printf 'Kubernetes CLI verified: %s (%s)\n' "$(command -v kind)" "$(kind version)"
if ! docker --context default info >/dev/null 2>&1; then
  # A new group becomes effective without logging out by re-entering a child shell.
  if (( ADD_GROUP && ! RESUME )) && id -nG "$(id -un)" | tr ' ' '\n' | grep -qx docker && command -v sg >/dev/null; then
    step 'Docker group membership: re-entering without root privileges'
    # Re-exec with the absolute path: the working directory changes after cd "$ROOT".
    restart=("$ROOT/scripts/install-all.sh" --resume-after-group --wireguard-config "$WIREGUARD_CONFIG")
    for ((i=0;i<${#ORIGINAL_ARGS[@]};i++)); do
      case "${ORIGINAL_ARGS[i]}" in
        --wireguard-config) (( i+=1 )) || true ;; # Skip original relative FILE as well.
        --dry-run|--resume-after-group) ;;
        --host-profile|--bootstrap-cpus) restart+=("${ORIGINAL_ARGS[i]}"); (( i+=1 )) || true; restart+=("${ORIGINAL_ARGS[i]}");;
        --*) restart+=("${ORIGINAL_ARGS[i]}");;
        *) ;; # Positional WireGuard filename already normalized above.
      esac
    done
    printf -v restart_q '%q ' "${restart[@]}"
    exec sg docker -c "exec ${restart_q}"
  fi
  echo 'Docker daemon is running but this shell cannot access it. Re-login after Docker-group enrollment and run ./install.sh again.' >&2
  exit 1
fi
step '2/7 — Verify TUN inside an isolated Docker namespace'
"$ROOT/scripts/verify-tun.sh" --repair --container-test
step '3/7 — Load Proton WireGuard configuration (host VPN stays unchanged)'
cmd=(python3 "$ROOT/scripts/proton-wireguard.py" import-pod "$WIREGUARD_CONFIG")
[[ -n "$HOST_PROFILE" ]] && cmd+=(--host-profile "$HOST_PROFILE")
"${cmd[@]}"
step '4/7 — Interactive deployment configuration and credential prompts'
configure=(python3 "$ROOT/scripts/configure.py")
# Already imported via stage 3; keep wizard from prompting twice for filename.
"${configure[@]}"
step '5/7 — Verify host/Pod WireGuard key separation and fail over'
python3 "$ROOT/scripts/proton-wireguard.py" check-host
step '6/7 — Deploy Kind cluster, Pod and persistent storage'
"$ROOT/scripts/deploy.sh"
if (( SKIP_FUNNEL )); then
  echo 'Funnel intentionally disabled. Start later with ./scripts/enable-funnel.sh'
else
  step '7/7 — Enable Tailscale Funnel (requires tailnet authorization)'
  "$ROOT/scripts/enable-funnel.sh"
fi
if (( ! SKIP_TIMERS )); then
  step 'Install user-level watchdog and weekly update timers'
  if ! "$ROOT/scripts/install-systemd.sh"; then
    echo 'WARNING: User systemd timer installation failed; deployment remains running. Run scripts/install-systemd.sh after fixing the user session.' >&2
  fi
fi
step 'Completed — current deployment status'
"$ROOT/scripts/status.sh"
printf '\nUse ./scripts/lockdown.sh "reason" to disable access and scale down the Pod.\n'

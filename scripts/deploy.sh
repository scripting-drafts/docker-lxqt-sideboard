#!/usr/bin/env bash
set -Eeuo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
export LXQT_PROJECT_ROOT="$ROOT"
source "$ROOT/scripts/cpu-affinity.sh"
lxqt_cpu_guard "$(lxqt_cpu_set)"
cd "$ROOT"
for exe in docker kind kubectl python3; do command -v "$exe" >/dev/null || { echo "Missing $exe" >&2; exit 1; }; done
python3 -c 'import yaml' || { echo 'Install: sudo apt install python3-yaml' >&2; exit 1; }
[[ ! -e config/LOCKED_DOWN ]] || { echo 'Deployment locked down; examine cause before running scripts/unlock.sh' >&2; exit 1; }
for f in config/settings.json config/Caddyfile config/secrets/wireguard_private_key config/secrets/tailscale_authkey config/secrets/vnc_password; do
  [[ -s "$f" ]] || { echo "Missing $f; first run python3 scripts/configure.py" >&2; exit 1; }
done
grep -qF '@shared path /shared' config/Caddyfile && \
  grep -qF 'redir / /vnc.html' config/Caddyfile || {
  echo 'Missing the UI Caddy routes in config/Caddyfile; run ./scripts/fix-caddy.sh' >&2
  exit 1
}
[[ -c /dev/net/tun ]] || { echo 'Missing /dev/net/tun on host. Load tun module / check host.' >&2; exit 1; }
python3 scripts/proton-wireguard.py check-host
docker info >/dev/null
if ! getent hosts registry-1.docker.io >/dev/null 2>&1; then
  echo 'Cannot resolve registry-1.docker.io; fix host DNS/VPN before building.' >&2
  exit 1
fi
python3 scripts/render.py
readarray -t opts < <(python3 - <<'PY'
import json
s=json.load(open('config/settings.json'))
print(s['cluster']);print(s['namespace']);print(s['host_data_dir']);print(s['host_cpu_set']);print(s['desktop_image']);print(s['gluetun_image']);print(s['caddy_image']);print(s['tailscale_image'])
PY
)
CLUSTER="${opts[0]}"; NS="${opts[1]}"; DATA="${opts[2]}"; CPUS="${opts[3]}"; IMAGE="${opts[4]}"
mkdir -p "$DATA/home" "$DATA/tailscale"
chmod 700 "$DATA" "$DATA/home"
# The Tailscale process runs as UID 0 but drops ALL capabilities, including
# DAC_OVERRIDE. It cannot enter a 0700 state directory owned by the host user.
# Repair persistent ownership without deleting stored Tailscale identity.
if [[ "$(stat -c '%u:%g' "$DATA/tailscale")" != '0:0' ]]; then
  echo "Correcting Tailscale state ownership (root:root, mode 0700): $DATA/tailscale"
  sudo chown -R 0:0 -- "$DATA/tailscale"
fi
if [[ "$(stat -c '%a' "$DATA/tailscale")" != '700' ]]; then
  sudo chmod 700 -- "$DATA/tailscale"
fi
if [[ "$(stat -c '%u:%g %a' "$DATA/tailscale")" != '0:0 700' ]]; then
  echo "Tailscale state permissions must be 0:0 700: $DATA/tailscale" >&2
  exit 1
fi
if [[ "$(stat -c %u "$DATA/home")" != 1000 ]]; then
  echo "Desktop home requires host owner UID 1000: $DATA/home" >&2
  echo "Run: sudo chown -R 1000:1000 '$DATA/home'" >&2
  exit 1
fi
NODE="${CLUSTER}-control-plane"
if ! kind get clusters | grep -Fxq "$CLUSTER"; then
  echo 'NOTE: Kind bootstrap briefly creates the node before Docker cpuset update; initial bootstrap is not fully isolated.'
  kind create cluster --name "$CLUSTER" --config generated/kind.yaml
else
  echo "Using existing Kind cluster: $CLUSTER"
  if ! docker inspect "$NODE" --format '{{range .Mounts}}{{if eq .Destination "/var/local/lxqt-data"}}{{.Source}}{{end}}{{end}}' | grep -Fxq "$DATA"; then
    echo "Existing Kind node does not mount configured host_data_dir; refusing to deploy." >&2; exit 1
  fi
fi
# Docker node cpuset restricts kubelet and every pod process because all live under this node cgroup.
docker update --cpuset-cpus "$CPUS" "$NODE" >/dev/null
ACTUAL="$(docker inspect "$NODE" --format '{{.HostConfig.CpusetCpus}}')"
[[ "$ACTUAL" == "$CPUS" ]] || { echo 'Host CPU cpuset enforcement failed' >&2; exit 1; }
kind export kubeconfig --name "$CLUSTER" --kubeconfig "$ROOT/config/kubeconfig" >/dev/null
chmod 600 config/kubeconfig
K=(kubectl --kubeconfig "$ROOT/config/kubeconfig")
# Pull fresh upstream images. Kind nodes otherwise may not have access to these.
for img in "${opts[5]}" "${opts[6]}" "${opts[7]}"; do
  docker pull "$img"
  kind load docker-image "$img" --name "$CLUSTER"
done
# Never use the unrestricted Docker-daemon builder for project compilation.
# A project-specific BuildKit container has an enforced cpuset from creation.
# Use host networking so the long-lived builder follows the host resolver and
# VPN state; bridge networking pins stale DNS servers into the container and
# breaks base-image metadata resolution after a VPN/DNS change.
BUILDER="lxqt-cpu-${CLUSTER}"
BUILDER_NODE="buildx_buildkit_${BUILDER}0"
if docker buildx inspect "$BUILDER" >/dev/null 2>&1; then
  current_network="$(docker inspect "$BUILDER_NODE" --format '{{.HostConfig.NetworkMode}}' 2>/dev/null || true)"
  if [[ "$current_network" != 'host' ]]; then
    echo "Recreating BuildKit builder $BUILDER (network=${current_network:-unknown}; stale DNS state)."
    docker buildx use default >/dev/null 2>&1 || true
    docker buildx rm "$BUILDER" >/dev/null
  fi
fi
if ! docker buildx inspect "$BUILDER" >/dev/null 2>&1; then
  # Buildx CSV-parses --driver-opt. Quote the *entire* key=value token because
  # cpuset-cpus contains commas (unquoted: invalid value "1", expecting k=v).
  docker buildx create --name "$BUILDER" --driver docker-container \
    --driver-opt network=host \
    --driver-opt "\"cpuset-cpus=$CPUS\"" >/dev/null
fi
# Also reconcile existing builder containers when CPU settings change.
if docker inspect "$BUILDER_NODE" >/dev/null 2>&1; then
  docker update --cpuset-cpus "$CPUS" "$BUILDER_NODE" >/dev/null
fi
docker buildx inspect "$BUILDER" --bootstrap >/dev/null
# Inspect again because bootstrap may create the container on this invocation.
[[ "$(docker inspect "$BUILDER_NODE" --format '{{.HostConfig.CpusetCpus}}')" == "$CPUS" ]] || {
  echo 'Project BuildKit container CPU restriction missing; refusing build.' >&2; exit 1;
}
[[ "$(docker inspect "$BUILDER_NODE" --format '{{.HostConfig.NetworkMode}}')" == 'host' ]] || {
  echo 'Project BuildKit container is not on host networking; refusing build.' >&2; exit 1;
}
docker buildx build --builder "$BUILDER" --pull --load -t "$IMAGE" "$ROOT"
kind load docker-image "$IMAGE" --name "$CLUSTER"
"${K[@]}" create namespace "$NS" --dry-run=client -o yaml | "${K[@]}" apply -f -
"${K[@]}" -n "$NS" create secret generic lxqt-runtime-secrets \
  --from-file="wireguard_private_key=config/secrets/wireguard_private_key" \
  --from-file="tailscale_authkey=config/secrets/tailscale_authkey" \
  --from-file="vnc_password=config/secrets/vnc_password" \
  --from-file="Caddyfile=config/Caddyfile" \
  --dry-run=client -o yaml | "${K[@]}" apply -f -
"${K[@]}" apply -f generated/workload.yaml
# Force secret rotations and image re-pulls to take effect even if YAML is unchanged.
"${K[@]}" -n "$NS" rollout restart deployment/lxqt-desktop
# The VPN sidecar and desktop each have startup probes. Together they may
# legitimately need more than the old 420s deadline on a cold Kind node.
ROLLOUT_TIMEOUT="${LXQT_ROLLOUT_TIMEOUT:-900s}"
if [[ ! "$ROLLOUT_TIMEOUT" =~ ^[1-9][0-9]*(s|m)$ ]]; then
  echo "Invalid LXQT_ROLLOUT_TIMEOUT=$ROLLOUT_TIMEOUT (expected e.g. 900s or 15m)" >&2
  exit 2
fi
# Show live diagnostic snapshots *during* rollout, rather than only after the
# kubectl rollout timeout. The watcher never prints Secret objects or values.
# Requires only the kubectl already used by this deployment script.
DIAG_INTERVAL="${LXQT_ROLLOUT_DIAG_INTERVAL:-30}"
if [[ ! "$DIAG_INTERVAL" =~ ^[1-9][0-9]*$ ]]; then
  echo "Invalid LXQT_ROLLOUT_DIAG_INTERVAL=$DIAG_INTERVAL (expected seconds, e.g. 30)" >&2
  exit 2
fi

rollout_snapshot() {
  local pod podlist
  printf '\n===== LXQt rollout diagnostic %s =====\n' "$(date --iso-8601=seconds)"
  echo '[Deployment progress]'
  "${K[@]}" -n "$NS" --request-timeout=10s get deployment lxqt-desktop \
    -o 'custom-columns=NAME:.metadata.name,DESIRED:.spec.replicas,UPDATED:.status.updatedReplicas,READY:.status.readyReplicas,AVAILABLE:.status.availableReplicas' \
    2>&1 || true
  echo '[Pod status and restarts]'
  "${K[@]}" -n "$NS" --request-timeout=10s get pods -l app=lxqt-desktop -o wide 2>&1 || true
  echo '[Individual container states]'
  "${K[@]}" -n "$NS" --request-timeout=10s get pods -l app=lxqt-desktop \
    -o jsonpath='{range .items[*]}POD {.metadata.name}{"\n"}{range .status.initContainerStatuses[*]}  init {.name}: ready={.ready}, restarts={.restartCount}, waiting={.state.waiting.reason}, last-exit={.lastTerminationState.terminated.exitCode}{"\n"}{end}{range .status.containerStatuses[*]}  app  {.name}: ready={.ready}, restarts={.restartCount}, waiting={.state.waiting.reason}, last-exit={.lastTerminationState.terminated.exitCode}{"\n"}{end}{end}' \
    2>&1 || true
  printf '\n'
  podlist="$("${K[@]}" -n "$NS" --request-timeout=10s get pods -l app=lxqt-desktop \
    -o jsonpath='{range .items[*]}{.metadata.name}{"\n"}{end}' 2>/dev/null)" || podlist=''
  if [[ -z "$podlist" ]]; then
    echo '[No matching Pod yet: inspect Deployment events or scheduling]'
  fi
  while IFS= read -r pod; do
    [[ -n "$pod" ]] || continue
    echo "[Recent warnings/events for $pod]"
    "${K[@]}" -n "$NS" --request-timeout=10s get events \
      --field-selector "involvedObject.name=$pod" \
      --sort-by=.lastTimestamp 2>&1 | tail -n 8 || true
    # Use recent-only, bounded output. Gluetun remains a restartable init
    # container, so its healthcheck errors are essential to troubleshoot.
    echo "[Gluetun warnings/errors in last ${DIAG_INTERVAL}s for $pod]"
    "${K[@]}" -n "$NS" --request-timeout=10s logs "$pod" -c gluetun \
      --since="${DIAG_INTERVAL}s" --tail=30 --timestamps=true 2>/dev/null \
      | grep -Ei 'warn|error|healthcheck|timeout|permission denied|operation not permitted|handshake' \
      | tail -n 12 || true
    # If other containers have begun to start, show only their failures.
    local container
    for container in desktop caddy tailscale; do
      local lines
      lines="$("${K[@]}" -n "$NS" --request-timeout=10s logs "$pod" -c "$container" \
        --since="${DIAG_INTERVAL}s" --tail=20 --timestamps=true 2>/dev/null \
        | grep -Ei 'warn|error|fatal|timeout|permission denied|failed' | tail -n 5)" || lines=''
      if [[ -n "$lines" ]]; then
        printf '[%s warnings/errors for %s]\n%s\n' "$container" "$pod" "$lines"
      fi
    done
  done <<< "$podlist"
  echo '===== end of diagnostic snapshot ====='
}

# The watcher is a child of this CPU-pinned process; it inherits the same
# exclusion of CPUs 2 and 8. Stop it when the rollout completes or is aborted.
rollout_monitor() {
  while :; do
    rollout_snapshot
    sleep "$DIAG_INTERVAL" || break
  done
}
MONITOR_PID=''
stop_rollout_monitor() {
  if [[ -n "$MONITOR_PID" ]]; then
    kill "$MONITOR_PID" 2>/dev/null || true
    wait "$MONITOR_PID" 2>/dev/null || true
    MONITOR_PID=''
  fi
}
trap stop_rollout_monitor EXIT
trap 'exit 130' INT
trap 'exit 143' TERM
printf '\nWaiting for LXQt readiness (timeout=%s; diagnostics every %ss).\n' "$ROLLOUT_TIMEOUT" "$DIAG_INTERVAL"
rollout_monitor &
MONITOR_PID=$!
if ! "${K[@]}" -n "$NS" rollout status deployment/lxqt-desktop --timeout="$ROLLOUT_TIMEOUT"; then
  stop_rollout_monitor
  echo "ERROR: LXQt Deployment did not become Ready within $ROLLOUT_TIMEOUT." >&2
  echo 'Collecting pod states, probe failures, events and bounded logs.' >&2
  "$ROOT/scripts/diagnose-rollout.sh" || true
  echo 'Do not delete the Kind cluster or blindly restart the Deployment: inspect the diagnostics first.' >&2
  exit 1
fi
stop_rollout_monitor
trap - EXIT INT TERM
"$ROOT/scripts/verify-cpus.sh"
printf '\nDeployed: %s, namespace=%s, cpus=%s\n' "$CLUSTER" "$NS" "$CPUS"
printf 'Your public URL is assigned when you run: ./scripts/enable-funnel.sh\n'
printf 'Pod status: kubectl --kubeconfig config/kubeconfig -n %s get pods\n' "$NS"

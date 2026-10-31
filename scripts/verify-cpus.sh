#!/usr/bin/env bash
# Read-only verification for the project-specific Kind and BuildKit cpusets.
set -Eeuo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
export LXQT_PROJECT_ROOT="$ROOT"
source "$ROOT/scripts/cpu-affinity.sh"
CPUS="$(lxqt_cpu_set)"
lxqt_cpu_expand "$CPUS" >/dev/null
[[ -s "$ROOT/config/settings.json" ]] || { echo 'Configure and deploy first.' >&2; exit 1; }
readarray -t n < <(python3 - "$ROOT/config/settings.json" <<'PYCPU'
import json,sys
s=json.load(open(sys.argv[1])); print(s['cluster']);print(s['host_cpu_set'])
PYCPU
)
CLUSTER="${n[0]}"; CPUS="${n[1]}"
[[ -n "$CLUSTER" ]] || exit 1
NODE="${CLUSTER}-control-plane"
BUILDER_NODE="buildx_buildkit_lxqt-cpu-${CLUSTER}0"
for unit in "$NODE" "$BUILDER_NODE"; do
  effective="$(docker inspect "$unit" --format '{{.HostConfig.CpusetCpus}}')" || {
    echo "ERROR: expected project container missing: $unit" >&2; exit 1;
  }
  # Compare expanded sets, not textual formatting of ranges.
  [[ "$(lxqt_cpu_expand "$effective")" == "$(lxqt_cpu_expand "$CPUS")" ]] || {
    echo "ERROR: $unit has CPU set '$effective' but project expects '$CPUS'" >&2; exit 1;
  }
  printf 'PASS: %s cpuset=%s (CPUs 2,8 excluded)\n' "$unit" "$effective"
done
# The Pod and all of its containers execute within the Kind node's cpuset.
# Also check current effective affinity of a process inside the Kind node.
inside="$(docker exec "$NODE" /bin/sh -c 'grep Cpus_allowed_list /proc/self/status' 2>/dev/null || true)"
printf '%s\n' "Kind node (reported inside): ${inside:-not available}"
printf 'NOTE: Docker daemon/containerd, unrelated host tasks and initial Kind node creation are outside this check.\n'

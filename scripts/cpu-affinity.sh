#!/usr/bin/env bash
# Shared CPU-affinity policy. Restricts the calling bash process and its future
# children; Docker daemon, host kernel threads and other workloads are separate.
LXQT_DEFAULT_CPUS='0,1,3-7,9-11'

lxqt_cpu_set() {
  if [[ -n "${LXQT_ALLOWED_CPUS:-}" ]]; then
    printf '%s\n' "$LXQT_ALLOWED_CPUS"
  elif [[ -s "${LXQT_PROJECT_ROOT}/config/settings.json" ]]; then
    python3 - "${LXQT_PROJECT_ROOT}/config/settings.json" <<'PYCPU'
import json,sys
print(json.load(open(sys.argv[1]))['host_cpu_set'])
PYCPU
  else
    printf '%s\n' "$LXQT_DEFAULT_CPUS"
  fi
}

lxqt_cpu_expand() {
  python3 - "$1" <<'PYCPU'
import sys
cpu_set=set()
for part in sys.argv[1].split(','):
    parts=part.split('-')
    if len(parts) not in (1,2) or not all(p.isdecimal() for p in parts):
        sys.exit('Invalid CPU set element: '+part)
    lo=int(parts[0]);hi=int(parts[-1])
    if lo>hi or hi>4095: sys.exit('Invalid CPU range: '+part)
    cpu_set.update(range(lo,hi+1))
if not cpu_set or {2,8}&cpu_set:
    sys.exit('REFUSED: CPU 2 or CPU 8 is present in the project CPU set')
print(' '.join(str(c) for c in sorted(cpu_set)))
PYCPU
}

lxqt_cpu_guard() {
  local selected="${1:-$(lxqt_cpu_set)}" expanded
  command -v taskset >/dev/null || { echo 'taskset required for project CPU safety; install util-linux.' >&2; return 1; }
  expanded="$(lxqt_cpu_expand "$selected")" || return 1
  # Fail if any requested CPU is unavailable. This is safer than silently using
  # a different CPU selection on a host with a changed topology.
  python3 - "$expanded" <<'PYCPU'
import os,sys
requested={int(x) for x in sys.argv[1].split()}
online=open('/sys/devices/system/cpu/online').read().strip()
cpus=set()
for section in online.split(','):
    a,b=section.split('-') if '-' in section else (section,section)
    cpus.update(range(int(a),int(b)+1))
if not requested.issubset(cpus):
    sys.exit('Requested project CPUs offline/unavailable: '+str(sorted(requested-cpus)))
PYCPU
  taskset -pc "$selected" "$$" >/dev/null || { echo 'Failed to enforce CPU affinity for installer process.' >&2; return 1; }
  export LXQT_ALLOWED_CPUS="$selected"
}

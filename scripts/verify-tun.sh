#!/usr/bin/env bash
# Check host TUN device; optionally repair missing device and perform an isolated container ioctl test.
set -Eeuo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
export LXQT_PROJECT_ROOT="$ROOT"
source "$ROOT/scripts/cpu-affinity.sh"
lxqt_cpu_guard "$(lxqt_cpu_set)"
REPAIR=0
CONTAINER_TEST=0
while (($#)); do
  case "$1" in
    --repair) REPAIR=1 ;;
    --container-test) CONTAINER_TEST=1 ;;
    --help) echo 'Usage: scripts/verify-tun.sh [--repair] [--container-test]'; exit 0 ;;
    *) echo "Unknown option: $1" >&2; exit 2 ;;
  esac
  shift
done
if [[ ! -c /dev/net/tun && "$REPAIR" == 1 ]]; then
  echo 'Repairing missing /dev/net/tun using the host TUN driver ...'
  sudo modprobe tun
  if [[ ! -e /dev/net/tun ]]; then
    sudo install -d -m 0755 /dev/net
    sudo mknod -m 0666 /dev/net/tun c 10 200
  fi
fi
[[ -c /dev/net/tun ]] || { echo 'TUN char device /dev/net/tun missing (try --repair).' >&2; exit 1; }
DEV=$(stat -c '%t:%T' /dev/net/tun)
[[ "${DEV,,}" == 'a:c8' ]] || { echo "Incorrect TUN major/minor (expected 10:200, observed 0x$DEV)" >&2; exit 1; }
# TUNGETFEATURES does not create a network interface or change routing. Open checks access.
python3 - <<'PY'
import fcntl, os, struct
path='/dev/net/tun'
with os.fdopen(os.open(path,os.O_RDWR),'rb',closefd=True) as device:
    features=struct.unpack('I', fcntl.ioctl(device, 0x800454cf, struct.pack('I',0)))[0]
    print(f'TUN device: OK (10:200, TUNGETFEATURES=0x{features:x})')
PY
if [[ "$CONTAINER_TEST" == 1 ]]; then
    DOCKER=(docker)
    if ! docker info >/dev/null 2>&1; then
        sudo docker info >/dev/null || { echo 'Docker daemon not available' >&2; exit 1; }
        DOCKER=(sudo docker)
    fi
    echo 'Testing TUNSETIFF inside a temporary network-isolated Docker container ...'
    "${DOCKER[@]}" run --rm --cpuset-cpus "$(lxqt_cpu_set)" -i --network none --device /dev/net/tun \
      --cap-add NET_ADMIN --security-opt no-new-privileges --entrypoint python \
      python:3.15-slim - <<'PY'
import fcntl,os,struct
with os.fdopen(os.open('/dev/net/tun', os.O_RDWR),'rb') as dev:
    result=fcntl.ioctl(dev, 0x400454ca, struct.pack('16sH',b'lxqttest%d',0x1001))
    print('Isolated Docker TUN creation: OK, interface name:',struct.unpack('16sH',result[:18])[0].rstrip(b'\0').decode())
PY
fi

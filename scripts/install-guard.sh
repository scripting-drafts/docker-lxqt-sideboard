#!/usr/bin/env bash
# Install the explicit-authorization guard for this project.
# Default: user unit plus linger. --system: system unit, requires sudo.
set -Eeuo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
export LXQT_PROJECT_ROOT="$ROOT"
MODE=user
UNINSTALL=0
AUTHORIZE_CURRENT=0

usage() {
  cat >&2 <<'EOF'
Usage: scripts/install-guard.sh [--user|--system] [--authorize-current] [--uninstall]
  --user              install the per-user guard (default)
  --system            install a boot-level systemd unit (requires sudo)
  --authorize-current mark an already-running stack as explicitly authorized
  --uninstall         remove the selected guard unit
EOF
}

while (($#)); do
  case "$1" in
    --user) MODE=user ;;
    --system) MODE=system ;;
    --authorize-current) AUTHORIZE_CURRENT=1 ;;
    --uninstall) UNINSTALL=1 ;;
    -h|--help) usage; exit 0 ;;
    *) usage; exit 2 ;;
  esac
  shift
done

CLUSTER="$(python3 - "$ROOT/config/settings.json" <<'PY' 2>/dev/null
import json
import sys
try:
    print(json.load(open(sys.argv[1], encoding="utf-8")).get("cluster", ""))
except Exception:
    pass
PY
)"
CLUSTER="${CLUSTER:-lxqt-secure}"
USER_NAME="${SUDO_USER:-$(id -un)}"
USER_HOME="$(getent passwd "$USER_NAME" | cut -d: -f6)"
USER_GROUP="$(id -gn "$USER_NAME")"
WRAPPER="$USER_HOME/.local/bin/lxqt-stack"
UNIT_USER="$USER_HOME/.config/systemd/user/lxqt-stack-guard.service"
UNIT_SYSTEM="/etc/systemd/system/lxqt-stack-guard.service"

install_wrapper() {
  install -d -m 0700 "$USER_HOME/.local/bin"
  cat > "$WRAPPER" <<EOF
#!/usr/bin/env bash
export LXQT_PROJECT_ROOT=$(printf '%q' "$ROOT")
exec $(printf '%q' "$ROOT/scripts/lxqt-stack") "\$@"
EOF
  chmod 0755 "$WRAPPER"
}

authorize_current() {
  if docker inspect -f '{{.State.Running}}' "${CLUSTER:-lxqt-secure}-control-plane" 2>/dev/null | grep -qx true; then
    "$ROOT/scripts/lxqt-stack" authorize "install-guard.sh"
  else
    echo "Stack is not running; no authorization marker created."
  fi
}

if [[ "$UNINSTALL" -eq 1 ]]; then
  if [[ "$MODE" == system ]]; then
    sudo systemctl disable --now lxqt-stack-guard.service 2>/dev/null || true
    sudo rm -f "$UNIT_SYSTEM"
    sudo systemctl daemon-reload
  else
    systemctl --user disable --now lxqt-stack-guard.service 2>/dev/null || true
    rm -f "$UNIT_USER"
    systemctl --user daemon-reload 2>/dev/null || true
  fi
  "$ROOT/scripts/lxqt-stack" revoke 2>/dev/null || true
  echo "Guard removed."
  exit 0
fi

install_wrapper

# Authorize a running stack BEFORE enabling/restarting the guard, otherwise
# the fresh guard could stop the stack during installation.
if [[ "$AUTHORIZE_CURRENT" -eq 1 ]]; then
  authorize_current
fi

if [[ "$MODE" == system ]]; then
  [[ "$(id -u)" -eq 0 || -n "${SUDO_USER:-}" ]] || {
    echo "Use sudo for --system: sudo $0 --system" >&2; exit 1;
  }
  tmp="$(mktemp)"
  trap 'rm -f "$tmp"' EXIT
  sed -e "s|@USER@|$USER_NAME|g" \
      -e "s|@GROUP@|$USER_GROUP|g" \
      -e "s|@HOME@|$USER_HOME|g" \
      -e "s|@ROOT@|$ROOT|g" \
      "$ROOT/systemd/lxqt-stack-guard-system.service.in" > "$tmp"
  sudo install -m 0644 "$tmp" "$UNIT_SYSTEM"
  sudo systemctl daemon-reload
  sudo systemctl enable --now lxqt-stack-guard.service
  sudo systemctl restart lxqt-stack-guard.service
  systemctl --user disable --now lxqt-stack-guard.service 2>/dev/null || true
  echo "Installed system guard: $UNIT_SYSTEM"
else
  [[ "$(id -u)" -ne 0 ]] || { echo "Run --user as the normal user, not root." >&2; exit 1; }
  install -d -m 0700 "$USER_HOME/.config/systemd/user"
  install -m 0644 "$ROOT/systemd/lxqt-stack-guard.service" "$UNIT_USER"
  systemctl --user daemon-reload
  systemctl --user enable --now lxqt-stack-guard.service
  systemctl --user restart lxqt-stack-guard.service
  if ! loginctl enable-linger "$USER_NAME" 2>/dev/null; then
    echo "WARNING: could not enable linger; the guard starts at login only." >&2
  fi
  echo "Installed user guard: $UNIT_USER"
fi

"$ROOT/scripts/lxqt-stack" status 2>/dev/null || true
echo "Starting the stack now requires: lxqt-stack start --yes (or interactive START)"

#!/usr/bin/env bash
set -Eeuo pipefail
umask 077

: "${DISPLAY:=:99}"
: "${XAUTHORITY:=$HOME/.Xauthority}"
: "${XDG_RUNTIME_DIR:=/tmp/xdg-runtime}"
: "${RESOLUTION:=1920x1080x24}"
: "${MULTIPTR:=0}"

if [[ ! "$RESOLUTION" =~ ^[0-9]{3,5}x[0-9]{3,5}x(16|24|32)$ ]]; then
  echo 'Invalid RESOLUTION (expected e.g. 1920x1080x24)' >&2; exit 1
fi
if [[ "$MULTIPTR" != 0 && "$MULTIPTR" != 1 ]]; then
  echo 'MULTIPTR must be 0 or 1' >&2; exit 1
fi
if [[ ! -r /run/secrets/vnc_password ]]; then
  echo 'Missing /run/secrets/vnc_password' >&2; exit 1
fi
vnc_password="$(tr -d '\r\n' </run/secrets/vnc_password)"
if [[ ! "$vnc_password" =~ ^[[:alnum:]]{8}$ ]]; then
  echo 'VNC secret must be exactly eight ASCII letters/digits (legacy VNC uses first 8)' >&2
  exit 1
fi

install -d -m 0700 "$HOME/.vnc" "$XDG_RUNTIME_DIR"

# x11vnc legacy password authentication is intentionally the second layer;
# Caddy Argon2id browser authentication is the outer access boundary.
x11vnc -storepasswd "$vnc_password" "$HOME/.vnc/passwd" >/dev/null 2>&1
unset vnc_password
chmod 0600 "$HOME/.vnc/passwd"

# X11 cookie instead of -ac (which disables X access controls).
touch "$XAUTHORITY"; chmod 0600 "$XAUTHORITY"
cookie="$(od -An -N16 -tx1 /dev/urandom | tr -d ' \n')"
xauth -f "$XAUTHORITY" add "$DISPLAY" MIT-MAGIC-COOKIE-1 "$cookie" >/dev/null
unset cookie

pids=()
cleanup() {
  trap - INT TERM EXIT
  for pid in "${pids[@]}"; do kill "$pid" 2>/dev/null || true; done
  wait 2>/dev/null || true
}
trap cleanup INT TERM EXIT

Xvfb "$DISPLAY" -screen 0 "$RESOLUTION" -nolisten tcp -auth "$XAUTHORITY" &
pids+=("$!")
for attempt in $(seq 1 50); do
  if xdpyinfo -display "$DISPLAY" >/dev/null 2>&1; then break; fi
  if ! kill -0 "${pids[0]}" 2>/dev/null; then
    echo 'Xvfb exited unexpectedly' >&2; exit 1
  fi
  sleep 0.2
done
xdpyinfo -display "$DISPLAY" >/dev/null 2>&1 || { echo 'Xvfb not ready' >&2; exit 1; }

# One desktop session is shared among every connected browser.
dbus-run-session -- startlxqt &
pids+=("$!")

# The packaged non-threaded event loop can accept TCP but stall RFB negotiation.
# Threaded handling was verified with authenticated simultaneous framebuffer clients.
vnc_args=(-threads -display "$DISPLAY" -auth "$XAUTHORITY" -rfbport 5900 -localhost
          -forever -shared -alwaysshared -rfbauth "$HOME/.vnc/passwd"
          -noxdamage -xkb -noremote -noclipboard)
if [[ "$MULTIPTR" == 1 && "${LXQT_ALLOW_MULTIPTR:-0}" != 1 ]]; then
  echo 'WARNING: x11vnc multi-pointer disabled for noVNC input compatibility.' >&2
  echo 'Set LXQT_ALLOW_MULTIPTR=1 only for experimental independent pointers.' >&2
  MULTIPTR=0
fi
if [[ "$MULTIPTR" == 1 ]]; then
  vnc_args+=(-multiptr)
  echo 'NOTE: experimental multi-pointer enabled; verified compatible browser/XInput2 behavior is required.' >&2
fi
x11vnc "${vnc_args[@]}" &
pids+=("$!")

# VNC is localhost-only; Websockify is reachable only inside the VPN namespace.
websockify --web /usr/share/novnc 127.0.0.1:6080 127.0.0.1:5900 &
pids+=("$!")

# Shared User Space service: storage repair, image/text uploads, and Caddy
# exposure at /shared/ behind the same browser authentication as the desktop.
python3 -B /usr/local/bin/shared-space serve \
  --root "$HOME/Shared User Space" --port 8082 &
pids+=("$!")

# Terminate the group if any critical component exits.
wait -n "${pids[@]}"
echo 'A desktop component exited; stopping the session' >&2
exit 1

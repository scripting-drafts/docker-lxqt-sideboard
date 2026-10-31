#!/usr/bin/env bash
# Optional *user-level* systemd timers; avoids executing user-writable project scripts as root.
set -Eeuo pipefail
[[ $(id -u) -ne 0 ]] || { echo 'Run as your normal Docker-authorized user, NOT with sudo.' >&2; exit 1; }
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
export LXQT_PROJECT_ROOT="$ROOT"
source "$ROOT/scripts/cpu-affinity.sh"
lxqt_cpu_guard "$(lxqt_cpu_set)"
[[ "$ROOT" != *' '* ]] || { echo 'Project path may not contain spaces for this simple systemd installer' >&2; exit 1; }
[[ -s "$ROOT/config/kubeconfig" ]] || { echo 'Deploy first' >&2; exit 1; }
CPU_LIST="$(lxqt_cpu_expand "$(lxqt_cpu_set)")"
DIR="$HOME/.config/systemd/user"
mkdir -p "$DIR"
for type in watchdog update; do
cat > "$DIR/lxqt-kind-${type}.service" <<EOF
[Unit]
Description=LXQt Kind ${type} task
[Service]
Type=oneshot
WorkingDirectory=$ROOT
CPUAffinity=$CPU_LIST
ExecStart=/bin/bash $ROOT/scripts/${type}.sh
EOF
done
cat > "$DIR/lxqt-kind-watchdog.timer" <<'EOF'
[Unit]
Description=Check LXQt Kind security and VPN health
[Timer]
OnBootSec=5min
OnUnitActiveSec=1min
AccuracySec=10s
Unit=lxqt-kind-watchdog.service
[Install]
WantedBy=timers.target
EOF
cat > "$DIR/lxqt-kind-update.timer" <<'EOF'
[Unit]
Description=Weekly refresh of LXQt Kind images
[Timer]
OnCalendar=Sun *-*-* 04:00:00
Persistent=true
Unit=lxqt-kind-update.service
[Install]
WantedBy=timers.target
EOF
systemctl --user daemon-reload
systemctl --user enable --now lxqt-kind-watchdog.timer lxqt-kind-update.timer
printf 'User timers enabled. Verify: systemctl --user list-timers lxqt-kind-*\n'
printf 'To run when logged out: sudo loginctl enable-linger %s\n' "$USER"

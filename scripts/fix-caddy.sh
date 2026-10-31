#!/usr/bin/env bash
# Plain-HTTP loopback listener, formatting / HTTP3 fix; opt-in Kubernetes apply.
# For LXQt Secure Browser Desktop Kind v6.3.7. Does NOT change host sysctls.
set -Eeuo pipefail
umask 077
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
export LXQT_PROJECT_ROOT="$ROOT"
source "$ROOT/scripts/cpu-affinity.sh"
lxqt_cpu_guard "$(lxqt_cpu_set)"
cd "$ROOT"

if (( $# > 1 )) || { (( $# == 1 )) && [[ "$1" != '--apply' ]]; }; then
  echo 'Usage: ./scripts/fix-caddy.sh [--apply]' >&2
  echo '  No flag: safely patch the HTTP loopback listener, format, and validate.' >&2
  echo '  --apply: also update the existing Kubernetes Secret and restart the Pod.' >&2
  exit 2
fi
for file in config/settings.json config/Caddyfile; do
  [[ -s "$file" ]] || { echo "Missing $file" >&2; exit 1; }
done
command -v docker >/dev/null || { echo 'Docker required to format and validate Caddyfile' >&2; exit 1; }

readarray -t opts < <(python3 - <<'PY'
import json
from pathlib import Path
s=json.loads(Path('config/settings.json').read_text())
print(s['host_cpu_set'])
print(s['caddy_image'])
print(s['namespace'])
PY
)
CPUS="${opts[0]}"
IMAGE="${opts[1]}"
NS="${opts[2]}"

draft="$(mktemp "${ROOT}/config/.Caddyfile.draft.XXXXXXXX")"
formatted="$(mktemp "${ROOT}/config/.Caddyfile.formatted.XXXXXXXX")"
trap 'rm -f -- "$draft" "$formatted"' EXIT

# Don't regenerate user auth or rewrite other directives; only configure
# supported HTTP protocols in the existing Caddyfile's global options block.
python3 - "$ROOT/config/Caddyfile" "$draft" <<'PY'
from pathlib import Path
import re, sys
source=Path(sys.argv[1]).read_text()
lines=source.splitlines(keepends=True)
if not lines or lines[0].strip()!='{':
    sys.exit('Unexpected Caddyfile: global options must start with { on line 1; no changes made')
depth=0
closing=None
for i, line in enumerate(lines):
    depth += line.count('{') - line.count('}')
    if depth==0:
        closing=i
        break
if closing is None:
    sys.exit('Malformed Caddyfile: global options block is not closed')
global_body=''.join(lines[1:closing])
if re.search(r'(?m)^\s*servers(?:\s+\S+)?\s*\{',global_body):
    if not re.search(r'(?m)^\s*protocols\s+h1\s+h2\s*$',global_body):
        sys.exit('Existing custom servers{} block detected; edit protocols manually, no changes made')
else:
    lines[closing:closing]=['\tservers {\n','\t\tprotocols h1 h2\n','\t}\n']
source=''.join(lines)
old_site=r'(?m)^127\.0\.0\.1:8081\s*\{[ \t]*$'
if re.search(old_site, source):
    source=re.sub(old_site, 'http://:8081 {\n\tbind 127.0.0.1', source, count=1)
elif not re.search(r'(?m)^http://:8081\s*\{[ \t]*\n[ \t]*bind 127\.0\.0\.1[ \t]*$', source):
    sys.exit('Unexpected Caddy listener; review HTTP scheme and loopback bind manually, no changes made')
Path(sys.argv[2]).write_text(source)
PY

# `caddy fmt -` reads stdin and writes only formatted Caddyfile to stdout;
# keep stdout in a 0600 temp file because it contains a Basic Auth hash.
docker --context default run --rm --cpuset-cpus "$CPUS" -i \
  --entrypoint caddy "$IMAGE" fmt - < "$draft" > "$formatted"
[[ -s "$formatted" ]] || { echo 'Caddy formatter returned an empty file; no changes made' >&2; exit 1; }
# Parse and verify using the same image version; do not expose the adapted
# config JSON to the terminal or a file (it contains credential hashes).
docker --context default run --rm --cpuset-cpus "$CPUS" -i \
  --entrypoint caddy "$IMAGE" adapt --config - --adapter caddyfile \
  < "$formatted" > /dev/null

if cmp -s "$ROOT/config/Caddyfile" "$formatted"; then
  echo 'Local Caddyfile is formatted, HTTP-only on loopback, and HTTP/3 disabled.'
else
  backup="${ROOT}/config/secrets/Caddyfile.backup.$(date +%Y%m%d-%H%M%S)"
  mkdir -p "$ROOT/config/secrets"
  chmod 700 "$ROOT/config/secrets"
  cp "$ROOT/config/Caddyfile" "$backup"
  chmod 600 "$backup"
  mv -f -- "$formatted" "$ROOT/config/Caddyfile"
  chmod 600 "$ROOT/config/Caddyfile"
  echo 'Updated config/Caddyfile (HTTP on loopback, formatted, HTTP/3 disabled).'
  echo 'A restricted-permission backup was saved under config/secrets/.'
fi

if [[ "${1:-}" != '--apply' ]]; then
  echo 'Local validation succeeded. Run ./scripts/fix-caddy.sh --apply to update the Pod.'
  exit 0
fi

# Existing Secret contains multiple credentials: update them *together*.
# Never replace it with a Secret containing only the Caddyfile.
for file in config/kubeconfig config/secrets/wireguard_private_key config/secrets/tailscale_authkey config/secrets/vnc_password; do
  [[ -s "$file" ]] || { echo "Missing $file; cannot update Kubernetes" >&2; exit 1; }
done
K=(kubectl --kubeconfig "$ROOT/config/kubeconfig" -n "$NS")
"${K[@]}" create secret generic lxqt-runtime-secrets \
  --from-file='wireguard_private_key=config/secrets/wireguard_private_key' \
  --from-file='tailscale_authkey=config/secrets/tailscale_authkey' \
  --from-file='vnc_password=config/secrets/vnc_password' \
  --from-file='Caddyfile=config/Caddyfile' \
  --dry-run=client -o yaml | "${K[@]}" apply -f -
"${K[@]}" rollout restart deployment/lxqt-desktop
echo 'Pod restarted to load the new Caddyfile (Gluetun and Tailscale also restart).'
"${K[@]}" rollout status deployment/lxqt-desktop --timeout=900s
"${K[@]}" get pods -l app=lxqt-desktop
"${K[@]}" logs deployment/lxqt-desktop -c caddy --tail=25
printf '\nCheck Tailscale Funnel and Proton VPN status after the restart.\n'

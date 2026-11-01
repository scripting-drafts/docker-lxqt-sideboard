#!/usr/bin/env python3
"""Interactive, local-only deployment configuration. Secrets never enter Kubernetes YAML sources."""
import argparse
import getpass
import json
import os
import pathlib
import re
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
CONF = ROOT / 'config'
SETTINGS = CONF / 'settings.json'
SECRET_DIR = CONF / 'secrets'

def prompt(label, default, *, valid=None):
    while True:
        val = input(f'{label} [{default}]: ').strip() or str(default)
        if valid is None or valid(val):
            return val
        print('Invalid value, try again.')

def save_secret(name, value):
    file = SECRET_DIR / name
    file.write_text(value + '\n', encoding='utf-8')
    os.chmod(file, 0o600)

def main():
    ap = argparse.ArgumentParser(description='Interactive Kind configuration; can import Proton .conf')
    ap.add_argument('--wireguard-config', metavar='FILE', help='Existing Proton WireGuard .conf to import securely')
    ap.add_argument('--host-profile', metavar='NAME', help='Known host WireGuard profile (for duplicate-tunnel safety check)')
    args = ap.parse_args()
    os.umask(0o077)
    CONF.mkdir(exist_ok=True)
    SECRET_DIR.mkdir(exist_ok=True, mode=0o700)
    os.chmod(SECRET_DIR, 0o700)
    defaults = json.loads((CONF / 'settings.example.json').read_text())
    if not SETTINGS.exists():
        defaults['host_data_dir'] = str(pathlib.Path.home() / '.local/share/lxqt-kind-secure')
    if SETTINGS.exists():
        defaults.update(json.loads(SETTINGS.read_text()))
    print('Configure the LXQt Kind deployment. Press Enter to keep each default.\n')
    data = {}
    dns = lambda s: bool(re.fullmatch(r'[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?', s))
    data['cluster'] = prompt('Kind cluster name', defaults['cluster'], valid=dns)
    data['namespace'] = prompt('Kubernetes namespace', defaults['namespace'], valid=dns)
    data['host_data_dir'] = prompt('Host persistence directory (absolute, dedicated)', defaults['host_data_dir'], valid=lambda s: s.startswith('/') and s not in ('/', '/home','/mnt','/var','/tmp','/etc') and '\n' not in s and not any(x in pathlib.Path(s).parts for x in ('..',)))
    data['host_cpu_set'] = prompt('Allowed host logical CPUs (exclude 2 & 8)', defaults['host_cpu_set'], valid=lambda s: bool(re.fullmatch(r'[0-9,\-]+',s)))
    data['vpn_recovery_timeout_seconds'] = int(prompt('VPN recovery target (seconds)', defaults.get('vpn_recovery_timeout_seconds', 45), valid=lambda s: s.isdecimal() and 10 <= int(s) <= 300))
    data['resolution'] = prompt('Desktop display resolution', defaults['resolution'], valid=lambda s: bool(re.fullmatch(r'[0-9]{3,5}x[0-9]{3,5}x(16|24|32)',s)))
    data['multi_pointer'] = prompt('Experimental independent pointers (yes/no)', 'yes' if defaults['multi_pointer'] else 'no',valid=lambda s: s in ('yes','no')) == 'yes'
    data['tailscale_hostname'] = prompt('Tailscale device hostname (URL assigned by Tailscale)',defaults['tailscale_hostname'],valid=dns)
    data['proton_free_only'] = prompt('Proton free servers only (yes/no)', 'yes' if defaults['proton_free_only'] else 'no', valid=lambda s:s in ('yes','no')) == 'yes'
    data['proton_server_countries'] = prompt('Optional Proton server countries, comma-separated; blank = any',defaults['proton_server_countries'],valid=lambda s:bool(re.fullmatch(r'[A-Za-z ,\-]*',s)))
    for key in ('desktop_image','gluetun_image','caddy_image','tailscale_image'):
        data[key] = prompt(key.replace('_',' ').title(), defaults[key],valid=lambda s:bool(re.fullmatch(r'[a-zA-Z0-9_./:@\-]+',s)))
    for key in ('desktop_storage_gib','tailscale_storage_gib'):
        data[key] = int(prompt(key.replace('_',' ').title(),defaults[key],valid=lambda s:s.isdecimal() and 1<=int(s)<=10000))
    data['viewer_username'] = prompt('Browser auth username',defaults['viewer_username'], valid=lambda s:bool(re.fullmatch('[A-Za-z0-9_.-]{1,48}',s)))
    if '2' in split_set(data['host_cpu_set']) or '8' in split_set(data['host_cpu_set']):
        print('ERROR: CPU 2 or CPU 8 is included; refusal to save.', file=sys.stderr)
        return 1
    # Reuse the existing Proton .conf without changing host routes or printing keys.
    source = args.wireguard_config
    if not source and not (SECRET_DIR / 'wireguard_private_key').exists():
        source = input('Proton WireGuard .conf path (Enter for manual secret input): ').strip()
    if source:
        command = [sys.executable, str(ROOT / 'scripts' / 'proton-wireguard.py'), 'import-pod',
                   str(pathlib.Path(source).expanduser())]
        if args.host_profile:
            command += ['--host-profile', args.host_profile]
        subprocess.run(command, check=True)
    secret_prompts = {
        'wireguard_private_key':'Proton WireGuard private key (base64)', 
        'tailscale_authkey':'Tailscale auth key (tskey-auth-...)',
        'vnc_password':'Legacy VNC password (exactly 8 ASCII letters/digits)',
    }
    for name, label in secret_prompts.items():
        existing = (SECRET_DIR / name).exists()
        while True:
            val = getpass.getpass(label+(' [Enter to keep saved]: ' if existing else ': '))
            if not val and existing: break
            if name == 'vnc_password' and not re.fullmatch(r'[A-Za-z0-9]{8}',val):
                print('Must be exactly 8 ASCII letters/digits.'); continue
            if name == 'wireguard_private_key' and not re.fullmatch(r'[A-Za-z0-9+/]{43}=',val):
                print('Expected 44-character WireGuard base64 private key.'); continue
            if name == 'tailscale_authkey' and not val.startswith('tskey-'):
                print('Expected a Tailscale auth key beginning tskey-.'); continue
            save_secret(name, val)
            if name == 'wireguard_private_key' and not source:
                # Avoid stale metadata falsely associating a freshly typed key with an old host profile.
                metadata = CONF / 'proton-source.json'
                if metadata.exists():
                    metadata.unlink()
            break
    caddy_path = CONF / 'Caddyfile'
    if getpass.getpass('Change browser password? Type yes to update; Enter to keep: ') == 'yes' or not caddy_path.exists() or (SETTINGS.exists() and defaults.get('viewer_username') != data['viewer_username']):
        password = getpass.getpass('Browser access password (>=16 characters): ')
        repeat = getpass.getpass('Repeat browser password: ')
        if len(password)<16 or password!=repeat:
            print('Password mismatch or too short; configuration incomplete.',file=sys.stderr)
            return 1
        print('Hashing browser password with Caddy (Docker daemon required)...')
        # Pass password through stdin; never in CLI args, logs, docker metadata or env.
        cmd = ['docker', '--context', 'default', 'run', '--rm', '--cpuset-cpus', data['host_cpu_set'], '-i', '--entrypoint',
               'caddy', data['caddy_image'], 'hash-password', '--algorithm', 'argon2id']
        try:
            result = subprocess.run(cmd, input=password+'\n', text=True,
                                    capture_output=True, timeout=180)
        except (OSError, subprocess.TimeoutExpired) as exc:
            print(f'Could not start Docker password hasher ({type(exc).__name__}). '
                  'Run scripts/install-docker.sh --dry-run and check docker info.', file=sys.stderr)
            return 1
        if result.returncode != 0:
            # Never echo untrusted command stderr or entered password. Explain the likely cause.
            print('Docker/Caddy password hashing failed (exit code '
                  f'{result.returncode}). Confirm the daemon and current-user permissions with: '
                  'docker info ; docker run --rm caddy:2 version', file=sys.stderr)
            print('If Docker is masked or damaged, use scripts/install-docker.sh '
                  '--dry-run and (only if authorized) --repair-docker.', file=sys.stderr)
            return 1
        encoded = result.stdout.strip()
        if not encoded.startswith('$argon2id$'):
            print('Caddy returned an unexpected password-hash format.', file=sys.stderr)
            return 1
        caddy_path.write_text('{\n    admin off\n    auto_https off\n    servers {\n        protocols h1 h2\n    }\n}\nhttp://:8081 {\n    bind 127.0.0.1\n    header {\n        Cache-Control "no-store"\n        X-Content-Type-Options "nosniff"\n        X-Frame-Options "DENY"\n        Referrer-Policy "no-referrer"\n        -Server\n    }\n    basic_auth argon2id {\n        '+data['viewer_username']+' '+encoded+'\n    }\n    reverse_proxy http://127.0.0.1:6080 {\n        stream_close_delay 5m\n    }\n}\n')
        route = subprocess.run(
            [sys.executable, str(ROOT / 'scripts' / 'shared_space.py'),
             'patch-caddy', str(caddy_path)],
            capture_output=True, text=True, timeout=30,
        )
        if route.returncode != 0:
            print('Could not add the Shared User Space route; Caddyfile left for inspection.',
                  file=sys.stderr)
            return 1
        # Canonical Caddy formatting prevents noisy startup warnings when the
        # password is rotated later. Run formatter using the same image and CPU
        # restrictions as the password hashing operation. Never print the file.
        fmt_cmd = ['docker', '--context', 'default', 'run', '--rm',
                   '--cpuset-cpus', data['host_cpu_set'], '-i', '--entrypoint',
                   'caddy', data['caddy_image'], 'fmt', '-']
        try:
            fmt = subprocess.run(fmt_cmd, input=caddy_path.read_text(), text=True,
                                 capture_output=True, timeout=60)
        except (OSError, subprocess.TimeoutExpired) as exc:
            print(f'Could not format Caddyfile ({type(exc).__name__}); '
                  'original file remains available for retry.', file=sys.stderr)
            return 1
        if fmt.returncode != 0 or not fmt.stdout.strip():
            print('Caddyfile formatting failed; run caddy fmt - to diagnose.',
                  file=sys.stderr)
            return 1
        caddy_path.write_text(fmt.stdout)
        os.chmod(caddy_path,0o600)
        del password, repeat
    SETTINGS.write_text(json.dumps(data,indent=2)+'\n')
    os.chmod(SETTINGS, 0o600)
    print('\nSaved nonsecret settings to config/settings.json and credential files under config/secrets/.')
    print('Public URL is assigned by Tailscale after Funnel is enabled; it cannot be freely chosen.\n')
    return 0

def split_set(s):
    cpus=set()
    for item in s.split(','):
        parts=item.split('-')
        try:
            a,b=(int(parts[0]),int(parts[-1])); cpus.update(range(a,b+1))
        except ValueError: pass
    return cpus

if __name__=='__main__':
    sys.exit(main())

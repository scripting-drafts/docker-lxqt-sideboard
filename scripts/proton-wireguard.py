#!/usr/bin/env python3
"""Safely import a Proton WireGuard file into Gluetun Kubernetes secrets.

Does NOT connect, disconnect or modify the host VPN. Host import is explicitly opt-in.
"""
from __future__ import annotations

import argparse
import base64
import configparser
import ipaddress
import json
import os
import pathlib
import re
import shutil
import subprocess
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parent.parent
SECRETS = ROOT / 'config' / 'secrets'
SOURCE = ROOT / 'config' / 'proton-source.json'
NAME_RE = re.compile(r'[A-Za-z0-9_.-]{1,15}\Z')
KEY_RE = re.compile(r'[A-Za-z0-9+/]{43}=\Z')


def parse_config(path: pathlib.Path) -> dict:
    if not path.is_file():
        raise ValueError(f'WireGuard file not found: {path}')
    parser = configparser.ConfigParser(interpolation=None, strict=True)
    parser.optionxform = str.lower
    try:
        with path.open(encoding='utf-8-sig') as file:
            parser.read_file(file)
    except (configparser.Error, UnicodeError) as exc:
        # Never echo parser errors: some include offending lines with PrivateKey.
        raise ValueError('WireGuard .conf has invalid encoding or INI structure') from None
    if not parser.has_section('Interface') or not parser.has_section('Peer'):
        raise ValueError('Expected both [Interface] and [Peer] sections')
    key = parser.get('Interface', 'PrivateKey', fallback='').strip()
    if not KEY_RE.fullmatch(key) or len(base64.b64decode(key, validate=True)) != 32:
        raise ValueError('Invalid 32-byte WireGuard private key')
    address = parser.get('Interface', 'Address', fallback='').strip()
    addresses = [s.strip() for s in address.split(',') if s.strip()]
    if not addresses:
        raise ValueError('WireGuard configuration does not include Address')
    for addr in addresses:
        ipaddress.ip_interface(addr)
    endpoint = parser.get('Peer', 'Endpoint', fallback='').strip()
    if ':' not in endpoint:
        raise ValueError('Missing WireGuard peer endpoint')
    host, _, port = endpoint.rpartition(':')
    if not (host and port.isdigit() and 1 <= int(port) <= 65535):
        raise ValueError('Malformed WireGuard endpoint')
    pub = parser.get('Peer', 'PublicKey', fallback='').strip()
    if not KEY_RE.fullmatch(pub) or len(base64.b64decode(pub, validate=True)) != 32:
        raise ValueError('Invalid WireGuard peer public key')
    allowed = parser.get('Peer', 'AllowedIPs', fallback='')
    if not allowed:
        raise ValueError('WireGuard peer AllowedIPs missing')
    for a in allowed.split(','):
        ipaddress.ip_network(a.strip(), strict=False)
    return {'private_key': key, 'endpoint': endpoint, 'addresses': addresses,
            'host_profile': path.stem if NAME_RE.fullmatch(path.stem) else '',
            'filename': path.name}


def atomic_private_text(path: pathlib.Path, text: str):
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(path.parent, 0o700)
    fd, tmp = tempfile.mkstemp(prefix='.tmp-', dir=path.parent)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as out:
            out.write(text)
        os.chmod(tmp, 0o600)
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def import_for_pod(path: pathlib.Path, profile_name: str | None = None) -> dict:
    data = parse_config(path.expanduser().resolve())
    name = profile_name if profile_name is not None else data['host_profile']
    if name and not NAME_RE.fullmatch(name):
        raise ValueError('Host profile name must be a short valid WireGuard interface name')
    atomic_private_text(SECRETS / 'wireguard_private_key', data['private_key'] + '\n')
    metadata = {'source_filename': data['filename'], 'host_profile': name or '',
                'endpoint': data['endpoint'], 'addresses': data['addresses']}
    atomic_private_text(SOURCE, json.dumps(metadata, indent=2) + '\n')
    return metadata


def active_host_connections() -> list[tuple[str, str, str]] | None:
    if not shutil.which('nmcli'):
        return None
    try:
        result = subprocess.run(['nmcli', '-t', '-f', 'NAME,TYPE,DEVICE', 'connection', 'show', '--active'],
                                capture_output=True, text=True, timeout=10, check=True)
    except (OSError, subprocess.SubprocessError):
        return None
    result_rows = []
    for line in result.stdout.splitlines():
        # NM uses backslash escaping for colons. We only need the typical simple names.
        fields = re.split(r'(?<!\\):', line)
        if len(fields) >= 3 and fields[-2] == 'wireguard':
            result_rows.append((fields[0].replace('\\:', ':'), fields[-2], fields[-1]))
    return result_rows


def check_host_conflict() -> int:
    if not (SECRETS / 'wireguard_private_key').is_file():
        print('Missing Proton WireGuard secret. Run configure.py with --wireguard-config FILE.', file=sys.stderr)
        return 2
    try:
        key = (SECRETS / 'wireguard_private_key').read_text().strip()
        if not KEY_RE.fullmatch(key) or len(base64.b64decode(key, validate=True)) != 32:
            raise ValueError('Bad imported WireGuard secret')
        metadata = json.loads(SOURCE.read_text()) if SOURCE.is_file() else {}
    except (ValueError, OSError) as err:
        print(f'Proton configuration invalid: {err}', file=sys.stderr)
        return 2
    active = active_host_connections()
    if active is None:
        print('NOTE: Could not inspect active NetworkManager WireGuard profiles; check host VPN manually.')
        return 0
    profile = metadata.get('host_profile', '')
    for name, _, dev in active:
        if profile and (name == profile or dev == profile):
            print(f'BLOCKED: Host WireGuard profile {name} ({dev}) is active and matches the imported file.', file=sys.stderr)
            print('To avoid simultaneous use of the same WireGuard identity, either:', file=sys.stderr)
            print(f'  1) Disconnect it deliberately: nmcli connection down {name!r} (may disrupt SSH!)', file=sys.stderr)
            print('  2) Generate a separate Proton WireGuard configuration for the Kubernetes Pod.', file=sys.stderr)
            print('The installer will never disconnect your host VPN automatically.', file=sys.stderr)
            return 3
        print(f'NOTE: Active host WireGuard: {name} ({dev}); verify it does not share the Pod private key.')
    print('Proton secret format and known NetworkManager host-conflict checks passed.')
    return 0


def host_import(path: pathlib.Path, profile_name: str):
    """Optional opt-in only. nmcli import may activate VPN and alter host routing."""
    data = parse_config(path.expanduser().resolve())
    if not NAME_RE.fullmatch(profile_name):
        raise ValueError('Invalid profile name. Example: wg-example')
    if not shutil.which('nmcli'):
        raise RuntimeError('nmcli missing: install NetworkManager or use Gluetun-only workflow')
    exists = subprocess.run(['nmcli', 'connection', 'show', profile_name],
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode == 0
    if exists:
        print(f'NetworkManager already has {profile_name}; leaving it unchanged.')
        return
    print('WARNING: nmcli import may activate the host VPN and change your routing/DNS.')
    print('Do not use this option over an SSH-only connection without out-of-band recovery.')
    answer = input('Type IMPORT-HOST to continue: ')
    if answer != 'IMPORT-HOST':
        raise RuntimeError('Host import cancelled')
    with tempfile.TemporaryDirectory(prefix='wg-import-', dir=None) as temp:
        os.chmod(temp, 0o700)
        output = pathlib.Path(temp) / f'{profile_name}.conf'
        # Copy private original to safe short filename without printing the key.
        shutil.copyfile(path.expanduser().resolve(), output)
        os.chmod(output, 0o600)
        subprocess.run(['nmcli', 'connection', 'import', 'type', 'wireguard', 'file', str(output)], check=True)
    print(f'Imported {profile_name}. Verify with: nmcli connection show --active')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    cmd = parser.add_subparsers(dest='command', required=True)
    imp = cmd.add_parser('import-pod', help='Import .conf key for Kubernetes; never changes host VPN')
    imp.add_argument('config', type=pathlib.Path)
    imp.add_argument('--host-profile', default=None, help='Existing host NM profile to guard against concurrent use')
    cmd.add_parser('check-host', help='Check for active matching NetworkManager WireGuard profile before deploying')
    hi = cmd.add_parser('import-host', help='OPTIONAL NetworkManager import; may change host routes')
    hi.add_argument('config', type=pathlib.Path)
    hi.add_argument('--name', default='wireguard')
    args = parser.parse_args()
    try:
        if args.command == 'import-pod':
            info = import_for_pod(args.config, args.host_profile)
            print(f'Imported Proton key from {info["source_filename"]} for the Kubernetes Gluetun secret.')
            print(f'Peer endpoint in source file: {info["endpoint"]} (Gluetun may choose another free server).')
            print('Private key was not printed and is stored in config/secrets/wireguard_private_key (0600).')
        elif args.command == 'check-host':
            return check_host_conflict()
        elif args.command == 'import-host':
            host_import(args.config, args.name)
    except (ValueError, OSError, configparser.Error, subprocess.CalledProcessError, RuntimeError) as err:
        print(f'ERROR: {err}', file=sys.stderr)
        return 1
    return 0

if __name__ == '__main__':
    sys.exit(main())

#!/usr/bin/env python3
"""Safely import a Proton WireGuard file into Gluetun Kubernetes secrets.

Import does not change the host VPN. ``check-host`` verifies host/Pod key
separation; with repair enabled it may disconnect a conflicting host profile
and activate a non-conflicting NetworkManager WireGuard profile, using the
shared interface library's latency/loss score when several candidates exist.
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

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from wireguard_library import (  # noqa: E402
    WireGuardInterface,
    discover,
    measure,
    nmcli_rows,
    wireguard_public_key,
)

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
    try:
        rows = nmcli_rows('NAME,TYPE,DEVICE', active=True)
    except RuntimeError:
        return None
    return [
        (row[0], row[1], row[2])
        for row in rows
        if len(row) >= 3 and row[1] == 'wireguard'
    ]


def pod_public_key() -> str:
    secret = SECRETS / 'wireguard_private_key'
    if not secret.is_file():
        raise RuntimeError('Missing Proton WireGuard secret')
    private_key = secret.read_text().strip()
    if not KEY_RE.fullmatch(private_key):
        raise RuntimeError('Bad imported WireGuard secret')
    try:
        if len(base64.b64decode(private_key, validate=True)) != 32:
            raise ValueError
    except ValueError as exc:
        raise RuntimeError('Bad imported WireGuard secret') from exc
    return wireguard_public_key(private_key)


def list_wireguard_profiles() -> list[dict]:
    return [interface.as_dict(include_key=True) for interface in discover()]


def _interface(profile: dict) -> WireGuardInterface:
    return WireGuardInterface(
        name=profile['name'],
        device=profile.get('device', ''),
        active=bool(profile['active']),
        public_key=profile.get('public_key'),
    )


def probe_profile_load(name: str) -> WireGuardInterface | None:
    """Activate a candidate, measure its tunnel, then disconnect it."""
    activated = False
    try:
        activate_profile(name)
        activated = True
        profile = next(
            (item for item in list_wireguard_profiles()
             if item['name'] == name and item['active']),
            None,
        )
        if profile is None:
            return None
        return measure(_interface(profile))
    finally:
        if activated:
            try:
                disconnect_profile(name)
            except RuntimeError:
                pass


def disconnect_profile(name: str) -> None:
    try:
        result = subprocess.run(
            ['nmcli', 'connection', 'down', name],
            capture_output=True, text=True, timeout=30,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise RuntimeError(f'could not disconnect {name}') from exc
    if result.returncode != 0:
        raise RuntimeError(f'could not disconnect {name}')


def activate_profile(name: str) -> None:
    try:
        result = subprocess.run(
            ['nmcli', 'connection', 'up', name],
            capture_output=True, text=True, timeout=60,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise RuntimeError(f'could not activate {name}') from exc
    if result.returncode != 0:
        raise RuntimeError(f'could not activate {name}')


def _active_conflicts(profiles: list[dict], pod_key: str) -> tuple[list[dict], list[str]]:
    active = [profile for profile in profiles if profile['active']]
    unknown = [profile['name'] for profile in active if not profile['public_key']]
    return [profile for profile in active if profile['public_key'] == pod_key], unknown


def check_host_conflict(repair: bool = True) -> int:
    try:
        pod_key = pod_public_key()
    except RuntimeError as err:
        print(f'Proton configuration invalid: {err}', file=sys.stderr)
        return 2
    try:
        profiles = list_wireguard_profiles()
    except RuntimeError as err:
        if not shutil.which('nmcli'):
            interfaces = []
            try:
                interfaces = subprocess.run(
                    ['wg', 'show', 'interfaces'], capture_output=True, text=True,
                    timeout=10, check=True,
                ).stdout.split()
            except (OSError, subprocess.SubprocessError):
                pass
            if not interfaces:
                print('OK: NetworkManager is absent and no active WireGuard interfaces were found.')
                return 0
        print(f'BLOCKED: Cannot verify active host WireGuard profiles: {err}', file=sys.stderr)
        return 2
    conflicts, unknown = _active_conflicts(profiles, pod_key)
    if unknown:
        print('BLOCKED: Cannot verify the private key of active host WireGuard profile(s): '
              + ', '.join(unknown), file=sys.stderr)
        print('Disconnect them manually or make their NetworkManager secrets readable.', file=sys.stderr)
        return 2
    if not conflicts:
        print('OK: Pod WireGuard key differs from all active host WireGuard profiles.')
        return 0
    names = ', '.join(profile['name'] for profile in conflicts)
    if not repair:
        print(f'BLOCKED: Active host WireGuard shares the Pod key: {names}', file=sys.stderr)
        return 3
    for profile in conflicts:
        print(f'CONFLICT: host WireGuard {profile["name"]} uses the Pod key; disconnecting it.')
        try:
            disconnect_profile(profile['name'])
        except RuntimeError as err:
            print(f'BLOCKED: {err}', file=sys.stderr)
            return 3
    try:
        profiles = list_wireguard_profiles()
    except RuntimeError as err:
        print(f'BLOCKED: cannot re-check host WireGuard profiles: {err}', file=sys.stderr)
        return 2
    conflicts, unknown = _active_conflicts(profiles, pod_key)
    if unknown:
        print('BLOCKED: Cannot verify remaining active host WireGuard profiles: '
              + ', '.join(unknown), file=sys.stderr)
        return 2
    if conflicts:
        print('BLOCKED: Conflicting host WireGuard profile remained active: '
              + ', '.join(profile['name'] for profile in conflicts), file=sys.stderr)
        return 3
    active_safe = [profile for profile in profiles if profile['active']]
    if active_safe:
        print('SECURED: host WireGuard is active with a different key: '
              + ', '.join(profile['name'] for profile in active_safe))
        return 0
    candidates = [
        profile for profile in profiles
        if not profile['active'] and profile['public_key'] and profile['public_key'] != pod_key
    ]
    if not candidates:
        print('SECURED: no active host WireGuard profile shares the Pod key; '
              'no safe failover profile is available.')
        return 0
    ordered = sorted(candidates, key=lambda item: item['name'].lower())
    if len(ordered) > 1:
        probed = []
        for profile in ordered:
            print(f'LOAD: probing host WireGuard {profile["name"]}.')
            try:
                measured = probe_profile_load(profile['name'])
            except RuntimeError as err:
                print(f'LOAD: {err}; skipping this profile.')
                continue
            if measured is not None and measured.score is not None:
                probed.append((measured.score, profile))
        if probed:
            probed.sort(key=lambda item: (item[0], item[1]['name'].lower()))
            ordered = [profile for _, profile in probed]
            print('LOAD: selected order: ' + ', '.join(
                f'{profile["name"]} ({score:.1f})' for score, profile in probed
            ))
    for profile in ordered:
        print(f'FAILOVER: activating host WireGuard {profile["name"]}.')
        try:
            activate_profile(profile['name'])
        except RuntimeError as err:
            print(f'FAILOVER: {err}; trying the next profile.')
            continue
        try:
            refreshed = list_wireguard_profiles()
        except RuntimeError as err:
            print(f'BLOCKED: cannot re-check failover profiles: {err}', file=sys.stderr)
            return 2
        conflicts, unknown = _active_conflicts(refreshed, pod_key)
        if unknown:
            print('BLOCKED: Cannot verify failover profile(s): '
                  + ', '.join(unknown), file=sys.stderr)
            return 2
        if conflicts:
            for conflict in conflicts:
                print(f'CONFLICT: failover profile {conflict["name"]} shares the Pod key; disconnecting it.')
                try:
                    disconnect_profile(conflict['name'])
                except RuntimeError as err:
                    print(f'BLOCKED: {err}', file=sys.stderr)
                    return 3
            continue
        if any(item['name'] == profile['name'] and item['active'] for item in refreshed):
            print(f'SECURED: host WireGuard failover is active with a different key: {profile["name"]}')
            return 0
        print(f'FAILOVER: {profile["name"]} did not become active; trying the next profile.')
    print('SECURED: no active host WireGuard profile shares the Pod key; '
          'no safe failover profile is available.')
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
    check = cmd.add_parser('check-host', help='Verify host WireGuard keys; optionally fail over')
    check.add_argument('--no-repair', action='store_true',
                       help='Report conflicts without disconnecting or activating profiles')
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
            return check_host_conflict(repair=not args.no_repair)
        elif args.command == 'import-host':
            host_import(args.config, args.name)
    except (ValueError, OSError, configparser.Error, subprocess.CalledProcessError, RuntimeError) as err:
        print(f'ERROR: {err}', file=sys.stderr)
        return 1
    return 0

if __name__ == '__main__':
    sys.exit(main())

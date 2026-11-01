#!/usr/bin/env python3
"""NetworkManager WireGuard inventory and latency/loss-based selection.

Private keys are read only to derive public keys for comparison; they are never
printed or passed on a command line. Load is a local latency/loss proxy, not a
provider-side server load metric.
"""
from __future__ import annotations

import argparse
import base64
import dataclasses
import json
import math
import re
import shutil
import subprocess
import sys
import time

KEY_RE = re.compile(r'[A-Za-z0-9+/]{43}=\Z')
LOSS_RE = re.compile(r'([0-9.]+)% packet loss')
RTT_RE = re.compile(r'rtt[^=]*=\s*[0-9.]+/([0-9.]+)/')
DEFAULT_PROBE = '1.1.1.1'
DEFAULT_CACHE_TTL = 30.0
DEFAULT_LOAD_TTL = 15.0


@dataclasses.dataclass(frozen=True)
class WireGuardInterface:
    name: str
    device: str
    active: bool
    public_key: str | None
    latency_ms: float | None = None
    packet_loss: float | None = None
    error: str | None = None

    @property
    def score(self) -> float | None:
        """Lower is better; 100% loss ranks poorly but is still measured."""
        if self.latency_ms is None and self.packet_loss is None:
            return None
        latency = self.latency_ms if self.latency_ms is not None else 1000.0
        loss = self.packet_loss if self.packet_loss is not None else 100.0
        return round(latency + loss * 10.0, 3)

    def as_dict(self, *, include_key: bool = False) -> dict:
        result = {
            'name': self.name,
            'device': self.device,
            'active': self.active,
            'latency_ms': self.latency_ms,
            'packet_loss': self.packet_loss,
            'score': self.score,
            'error': self.error,
        }
        if include_key:
            result['public_key'] = self.public_key
        return result


def nmcli_rows(fields: str, *, active: bool = False) -> list[list[str]]:
    if not shutil.which('nmcli'):
        raise RuntimeError('nmcli is not installed')
    command = ['nmcli', '-t', '-f', fields, 'connection', 'show']
    if active:
        command.append('--active')
    try:
        result = subprocess.run(command, capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.SubprocessError) as exc:
        raise RuntimeError('NetworkManager is unavailable') from exc
    if result.returncode != 0:
        raise RuntimeError('NetworkManager could not list connections')
    return [
        [field.replace('\\:', ':') for field in re.split(r'(?<!\\):', line)]
        for line in result.stdout.splitlines()
    ]


def profile_private_key(name: str) -> str | None:
    if not shutil.which('nmcli'):
        return None
    try:
        result = subprocess.run(
            ['nmcli', '--show-secrets', '--get-values', 'wireguard.private-key',
             'connection', 'show', name],
            capture_output=True, text=True, timeout=10,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if result.returncode != 0:
        return None
    for line in result.stdout.splitlines():
        candidate = line.strip()
        if KEY_RE.fullmatch(candidate):
            try:
                if len(base64.b64decode(candidate, validate=True)) == 32:
                    return candidate
            except ValueError:
                continue
    return None


def wireguard_public_key(private_key: str) -> str:
    if not shutil.which('wg'):
        raise RuntimeError('wg is not installed (install wireguard-tools)')
    try:
        result = subprocess.run(
            ['wg', 'pubkey'], input=private_key + '\n',
            capture_output=True, text=True, timeout=10,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise RuntimeError('wg pubkey failed') from exc
    public_key = result.stdout.strip()
    if result.returncode != 0 or not KEY_RE.fullmatch(public_key):
        raise RuntimeError('wg pubkey returned an invalid public key')
    try:
        if len(base64.b64decode(public_key, validate=True)) != 32:
            raise ValueError
    except ValueError as exc:
        raise RuntimeError('wg pubkey returned an invalid public key') from exc
    return public_key


def discover() -> list[WireGuardInterface]:
    rows = nmcli_rows('NAME,TYPE')
    active_rows = nmcli_rows('NAME,TYPE,DEVICE', active=True)
    active = {
        row[0]: row[2] if len(row) >= 3 else ''
        for row in active_rows if row[1] == 'wireguard'
    }
    interfaces = []
    for row in rows:
        if len(row) < 2 or row[1] != 'wireguard':
            continue
        name = row[0]
        private_key = profile_private_key(name)
        try:
            public_key = wireguard_public_key(private_key) if private_key else None
        except RuntimeError:
            public_key = None
        interfaces.append(WireGuardInterface(
            name=name,
            device=active.get(name, ''),
            active=name in active,
            public_key=public_key,
        ))
    return interfaces


def parse_ping(output: str) -> tuple[float | None, float | None]:
    loss_match = LOSS_RE.search(output)
    rtt_match = RTT_RE.search(output)
    loss = float(loss_match.group(1)) if loss_match else None
    latency = float(rtt_match.group(1)) if rtt_match else None
    return latency, loss


def measure(interface: WireGuardInterface, probe: str = DEFAULT_PROBE,
            count: int = 3, wait: int = 2) -> WireGuardInterface:
    if not interface.active or not interface.device:
        return dataclasses.replace(interface, error='not active')
    if not shutil.which('ping'):
        return dataclasses.replace(interface, error='ping is not installed')
    command = ['ping', '-n', '-q', '-I', interface.device,
               '-c', str(count), '-W', str(wait), probe]
    try:
        result = subprocess.run(
            command, capture_output=True, text=True,
            timeout=count * (wait + 1) + 5,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return dataclasses.replace(interface, error=f'probe failed: {type(exc).__name__}')
    latency, loss = parse_ping(result.stdout + '\n' + result.stderr)
    if latency is None and loss is None:
        return dataclasses.replace(interface, error='no probe result')
    return dataclasses.replace(interface, latency_ms=latency, packet_loss=loss)


def ranked(interfaces: list[WireGuardInterface]) -> list[WireGuardInterface]:
    def key(interface: WireGuardInterface):
        score = interface.score
        return (score is None, score if score is not None else math.inf, interface.name.lower())
    return sorted(interfaces, key=key)


class WireGuardLibrary:
    """Inventory cache with event invalidation and short-lived load probes.

    Inventory is refreshed at most every cache_ttl seconds; load probes are
    cached per active interface for load_ttl seconds. Call invalidate() after
    any nmcli state change so the next read is authoritative.
    """

    def __init__(self, probe: str = DEFAULT_PROBE,
                 cache_ttl: float = DEFAULT_CACHE_TTL,
                 load_ttl: float = DEFAULT_LOAD_TTL,
                 clock=time.monotonic):
        self.probe = probe
        self.cache_ttl = cache_ttl
        self.load_ttl = load_ttl
        self.clock = clock
        self._cache: list[WireGuardInterface] | None = None
        self._cache_at = 0.0
        self._loads: dict[tuple[str, str], tuple[float, WireGuardInterface]] = {}

    def invalidate(self) -> None:
        self._cache = None
        self._loads.clear()

    def refresh(self, *, force: bool = False) -> list[WireGuardInterface]:
        now = self.clock()
        if not force and self._cache is not None and now - self._cache_at < self.cache_ttl:
            return list(self._cache)
        interfaces = discover()
        self._cache = interfaces
        self._cache_at = now
        valid = {(item.name, item.device) for item in interfaces if item.active}
        self._loads = {key: value for key, value in self._loads.items() if key in valid}
        return list(interfaces)

    def measure(self, interface: WireGuardInterface, *, force: bool = False) -> WireGuardInterface:
        if not interface.active or not interface.device:
            return dataclasses.replace(interface, error='not active')
        key = (interface.name, interface.device)
        now = self.clock()
        cached = self._loads.get(key)
        if not force and cached is not None and now - cached[0] < self.load_ttl:
            return cached[1]
        result = measure(interface, self.probe)
        self._loads[key] = (now, result)
        return result

    def list(self, *, probe_active: bool = True, force: bool = False) -> list[WireGuardInterface]:
        interfaces = self.refresh(force=force)
        if probe_active:
            interfaces = [
                self.measure(item, force=force) if item.active else item
                for item in interfaces
            ]
        return ranked(interfaces)

    def best(self, *, probe_active: bool = True, force: bool = False) -> WireGuardInterface | None:
        items = self.list(probe_active=probe_active, force=force)
        return items[0] if items else None


def main() -> int:
    parser = argparse.ArgumentParser(description='WireGuard interface library')
    sub = parser.add_subparsers(dest='command', required=True)
    listing = sub.add_parser('list', help='list profiles and probe active interfaces')
    listing.add_argument('--probe', default=DEFAULT_PROBE)
    listing.add_argument('--json', action='store_true')
    listing.add_argument('--refresh', action='store_true', help='bypass the inventory cache')
    listing.add_argument('--no-probe', action='store_true', help='skip latency/loss probes')
    args = parser.parse_args()
    library = WireGuardLibrary(probe=args.probe)
    try:
        interfaces = library.list(probe_active=not args.no_probe, force=args.refresh)
    except RuntimeError as err:
        print(f'wireguard-library: {err}', file=sys.stderr)
        return 2
    if args.json:
        print(json.dumps([item.as_dict() for item in interfaces], indent=2))
        return 0
    print(f'{"NAME":24} {"ACTIVE":6} {"DEVICE":16} {"LOSS":>6} {"LATENCY":>9} {"SCORE":>9}')
    for item in interfaces:
        loss = '-' if item.packet_loss is None else f'{item.packet_loss:.0f}%'
        latency = '-' if item.latency_ms is None else f'{item.latency_ms:.1f}ms'
        score = '-' if item.score is None else f'{item.score:.1f}'
        print(f'{item.name:24} {str(item.active):6} {item.device or "-":16} {loss:>6} {latency:>9} {score:>9}')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())

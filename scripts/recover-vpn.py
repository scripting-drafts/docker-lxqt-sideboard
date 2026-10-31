#!/usr/bin/env python3
"""Bounded VPN recovery verification; never bypass VPN or enable public access."""
import argparse
import json
from pathlib import Path
import re
import subprocess
import time

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_TIMEOUT = 45


def command(args, timeout):
    try:
        return subprocess.run(args, capture_output=True, text=True, timeout=timeout)
    except (subprocess.TimeoutExpired, OSError) as error:
        # Do not print raw process output, runtime IPs, or config content.
        return subprocess.CompletedProcess(args, 124, '', type(error).__name__)


def recover(settings, kubeconfig, *, repair_funnel=False, timeout=None,
            runner=command, clock=time.monotonic, sleep=time.sleep, emit=print):
    budget = timeout if timeout is not None else settings.get('vpn_recovery_timeout_seconds', DEFAULT_TIMEOUT)
    if type(budget) is not int or not 10 <= budget <= 300:
        raise ValueError('Recovery target must be an integer from 10 to 300 seconds')
    start = clock()
    deadline = start + budget
    kube = ['kubectl', '--kubeconfig', str(kubeconfig), '--request-timeout=5s',
            '-n', settings['namespace']]
    restarted = False
    vpn_recovered_at = None
    public_url = None
    public_failures = 0
    funnel_verified = False

    def event(name, **data):
        emit(json.dumps({'event': name, 'elapsed_seconds': round(clock() - start, 1), **data}))

    def run(args, limit=6):
        remaining = deadline - clock()
        if remaining <= 0:
            return subprocess.CompletedProcess(args, 124, '', 'Deadline exceeded')
        return runner(args, timeout=min(limit, remaining))

    def execute(container, args):
        return run(kube + ['exec', 'deployment/lxqt-desktop', '-c', container, '--'] + args)

    def outcome(success, reason):
        result = {'success': success, 'reason': reason, 'target_seconds': budget,
                  'elapsed_seconds': round(clock() - start, 1),
                  'vpn_recovered_seconds': vpn_recovered_at,
                  'vpn_target_met': vpn_recovered_at is not None and vpn_recovered_at <= budget,
                  'funnel_verified': funnel_verified,
                  'tailscale_restart_requested': restarted}
        event('complete', **result)
        return result

    event('start', target_seconds=budget, repair_existing_funnel=repair_funnel)
    if (Path(kubeconfig).parent / 'LOCKED_DOWN').exists():
        return outcome(False, 'Project is locked down; recovery is disabled')
    while clock() < deadline:
        health = execute('gluetun', ['/gluetun-entrypoint', 'healthcheck'])
        egress = None
        if health.returncode == 0:
            egress = execute('desktop', ['curl', '-4', '--noproxy', '*', '--fail',
                                        '--silent', '--show-error', '--connect-timeout', '2',
                                        '--max-time', '3', 'https://api.ipify.org'])
        healthy = health.returncode == 0 and egress is not None and egress.returncode == 0 and bool(egress.stdout.strip())
        event('vpn_check', healthy=healthy)
        if healthy and clock() < deadline:
            if vpn_recovered_at is None:
                vpn_recovered_at = round(clock() - start, 1)
            if not repair_funnel:
                return outcome(True, 'VPN health and desktop egress recovered')
            if public_url is None:
                status = execute('tailscale', ['tailscale', 'funnel', 'status'])
                if status.returncode == 0:
                    match = re.search(r'https://[a-zA-Z0-9.-]+\.ts\.net(?=\s|/|$)', status.stdout)
                    if match is not None and 'Funnel on' in status.stdout:
                        public_url = match.group() + '/vnc.html'
                    elif 'Funnel on' not in status.stdout:
                        return outcome(False, 'Funnel is not enabled; refusing to enable public access')
            if public_url is not None:
                public = run(['curl', '--http1.1', '--noproxy', '*', '--silent', '--show-error',
                              '--connect-timeout', '5', '--max-time', '8', '--output', '/dev/null',
                              '--write-out', '%{http_code}', public_url], limit=10)
                code = public.stdout.strip()
                funnel_verified = public.returncode == 0 and code == '401'
                event('funnel_check', http_status=code if re.fullmatch(r'\d{3}', code) else 'invalid',
                      curl_exit=public.returncode, verified=funnel_verified)
                if funnel_verified and clock() < deadline:
                    public_failures = 0
                    # Verify VPN again, not just a possibly surviving relay connection.
                    if execute('gluetun', ['/gluetun-entrypoint', 'healthcheck']).returncode == 0 and clock() < deadline:
                        return outcome(True, 'VPN and existing Funnel authentication gate recovered')
                if public.returncode == 0 and code.startswith('2'):
                    return outcome(False, 'Public unauthenticated request was not rejected; investigate authentication')
                if not funnel_verified:
                    public_failures += 1
                if public_failures >= 2 and not restarted and deadline - clock() >= 10:
                    # Only an explicitly requested repair may restart this one
                    # container, and only after real VPN egress has recovered.
                    event('restart_tailscale', reason='Existing Funnel has not recovered')
                    restart = execute('tailscale', ['sh', '-c', 'kill -TERM 1'])
                    restarted = True
                    if restart.returncode not in (0, 137, 143):
                        event('restart_request_failed')
        remaining = deadline - clock()
        if remaining > 0:
            sleep(min(2, remaining))
    reason = ('VPN recovered within target; Funnel verification missed the combined deadline'
              if vpn_recovered_at is not None else 'VPN recovery target missed')
    return outcome(False, reason + '; VPN firewall and host settings were not changed')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--timeout', type=int, help='Override saved recovery target (default 45 seconds)')
    parser.add_argument('--repair-funnel', action='store_true',
                        help='May restart only Tailscale to recover an already-enabled Funnel')
    args = parser.parse_args()
    settings = json.loads((ROOT / 'config/settings.json').read_text())
    if (ROOT / 'config/LOCKED_DOWN').exists():
        parser.error('Project is locked down; recovery must not undo a security lockdown')
    try:
        result = recover(settings, ROOT / 'config/kubeconfig',
                         repair_funnel=args.repair_funnel, timeout=args.timeout)
    except ValueError as error:
        parser.error(str(error))
    return 0 if result['success'] else 1


if __name__ == '__main__':
    raise SystemExit(main())

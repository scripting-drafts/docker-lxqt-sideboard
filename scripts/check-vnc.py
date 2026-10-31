#!/usr/bin/env python3
"""Check actual RFB negotiation, not merely an open TCP port. Never send a password."""
import argparse
import socket
import struct
import sys
import time


def check_vnc(host='127.0.0.1', port=5900, timeout=2.0, *, clock=time.monotonic):
    deadline = clock() + timeout
    with socket.create_connection((host, port), timeout=timeout) as connection:
        def remaining():
            seconds = deadline - clock()
            if seconds <= 0:
                raise TimeoutError('RFB handshake deadline exceeded')
            connection.settimeout(seconds)

        def exact(size):
            data = b''
            while len(data) < size:
                remaining()
                part = connection.recv(size - len(data))
                if not part:
                    raise ConnectionError('VNC closed before negotiation completed')
                data += part
            return data

        banner = exact(12)
        if banner not in (b'RFB 003.003\n', b'RFB 003.007\n', b'RFB 003.008\n'):
            raise ValueError('Unexpected RFB version banner')
        remaining()
        connection.sendall(banner)
        if banner == b'RFB 003.003\n':
            security = [struct.unpack('!I', exact(4))[0]]
        else:
            count = exact(1)[0]
            security = list(exact(count))
        if 2 not in security or 1 in security:
            raise ValueError('Expected mandatory VNC password authentication')
        # Stop before selecting an authentication type: no secret is read and
        # routine probes do not submit failed password attempts.
    return True


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--timeout', type=float, default=2.0)
    parser.add_argument('--port', type=int, default=5900)
    args = parser.parse_args()
    if not 0 < args.timeout <= 10 or not 1 <= args.port <= 65535:
        parser.error('Use timeout > 0 and <= 10 seconds, and a valid TCP port')
    try:
        check_vnc(port=args.port, timeout=args.timeout)
    except (OSError, ValueError) as error:
        print('VNC not ready: ' + str(error), file=sys.stderr)
        return 1
    print('VNC RFB negotiation ready; password authentication required')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())

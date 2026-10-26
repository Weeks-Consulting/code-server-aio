"""Shut down Supervisor when a service exhausts its startup retries."""

import os
import signal
import sys


def fields(data):
    return dict(field.split(b':', 1) for field in bytes(data).split() if b':' in field)


def listen(source, output):
    while True:
        # stdout carries only Supervisor protocol messages; use byte lengths.
        output.write(b'READY\n')
        output.flush()
        header = source.readline()
        if not header:
            return
        length = fields(header).get(b'len', b'')
        if not length.isdigit():
            raise ValueError('Invalid Supervisor event length')
        remaining = int(length)
        payload = bytearray()
        while remaining:
            block = source.read(remaining)
            if not block:
                raise EOFError('Truncated Supervisor event payload')
            payload.extend(block)
            remaining -= len(block)
        output.write(b'RESULT 2\nOK')
        output.flush()
        name = fields(payload).get(b'processname')
        if name:
            print(f'Critical service {name.decode(errors="replace")} entered FATAL; shutting down for container restart',
                  file=sys.stderr, flush=True)
            os.kill(os.getppid(), signal.SIGTERM)


if __name__ == '__main__':
    try:
        listen(sys.stdin.buffer, sys.stdout.buffer)
    except (OSError, ValueError, EOFError) as error:
        sys.exit(str(error))

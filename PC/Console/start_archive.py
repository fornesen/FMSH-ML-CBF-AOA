"""Ensure the PC archive receiver is ready before starting the live webpage."""
import argparse
import http.client
import errno
import ipaddress
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import time
import uuid

BASE = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE.parent))
from pc_config import LOGS, load_settings, resolve_path

RECEIVER = BASE.parents[1] / 'common' / 'archive_receiver.py'


class ArchivePortConflict(RuntimeError):
    """The existing listener is not this folder's requested receiver."""


def receiver_identity(host, port):
    connection = http.client.HTTPConnection(host, port, timeout=2)
    try:
        connection.request('GET', '/health')
        response = connection.getresponse()
        body = response.read(16385)
        if response.status != 200 or len(body) > 16384:
            raise ValueError('Not an archive receiver')
        return json.loads(body)
    finally:
        connection.close()


def matching_receiver(identity, output, bind, port):
    if not isinstance(identity, dict):
        return False
    expected = {
        'application': 'fpga-aoa-archive', 'bind': bind, 'port': port,
        'root': str(RECEIVER.resolve().parents[1]), 'output': str(output.resolve()),
    }
    for name, value in expected.items():
        actual = identity.get(name)
        if name in ('root', 'output') and isinstance(actual, str):
            actual, value = os.path.normcase(actual), os.path.normcase(value)
        if actual != value:
            return False
    return isinstance(identity.get('pid'), int) and identity['pid'] > 0


def port_available(bind, port):
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        if hasattr(socket, 'SO_EXCLUSIVEADDRUSE'):
            probe.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        try:
            probe.bind((bind, port))
        except OSError as error:
            if error.errno in (errno.EADDRINUSE, errno.EACCES) or getattr(error, 'winerror', None) in (10048, 10013):
                return False
            raise
    return True


def check_bind_address(bind):
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
            probe.bind((bind, 0))
    except OSError as error:
        raise RuntimeError(
            f'Cannot bind to {bind}: {error}. The address must belong to this PC. '
            'Use listen_address=0.0.0.0 in settings.json '
            '(or AOA_ARCHIVE_BIND=0.0.0.0) to listen on all local IPv4 interfaces.'
        ) from error


def receiver_failure(log):
    lines = log.read_text(encoding='utf-8', errors='replace').strip().splitlines()
    detail = lines[-1] if lines else 'No receiver error was logged'
    return RuntimeError(f'Archive receiver could not start: {detail}; see {log}')


def stop_created_receiver(args):
    """Only clean up a process created by this particular launch attempt."""
    child = getattr(args, '_receiver_process', None)
    if child is not None:
        if child.poll() is None:
            child.terminate()
        child.wait(timeout=5)
        args._receiver_process = None


def _ensure_port(args, output, check):
    probe_host = '127.0.0.1' if args.bind == '0.0.0.0' else args.bind

    def existing():
        if port_available(args.bind, args.port):
            return None
        try:
            item = receiver_identity(probe_host, args.port)
        except (OSError, ValueError, http.client.HTTPException) as error:
            raise ArchivePortConflict(f'Port {args.port} is occupied by another service') from error
        if not matching_receiver(item, output, args.bind, args.port):
            raise ArchivePortConflict(f'Port {args.port} belongs to another archive configuration')
        return item['pid']

    pid = existing()
    if pid:
        print(f'Archive receiver ready (reused), PID {pid}; data -> {output}', flush=True)
        return pid
    if check:
        raise RuntimeError('Archive receiver is not running')
    LOGS.mkdir(parents=True, exist_ok=True)
    log = LOGS/('archive-' + uuid.uuid4().hex[:8] + '.log')
    with log.open('ab') as stream:
        child = subprocess.Popen(
            [sys.executable, '-u', str(RECEIVER), '--output', str(output),
             '--bind', args.bind, '--port', str(args.port)],
            cwd=BASE, stdin=subprocess.DEVNULL, stdout=stream, stderr=subprocess.STDOUT,
            creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0), close_fds=True)
    args._receiver_process = child
    try:
        for _ in range(8):
            if child.poll() is not None:
                raise receiver_failure(log)
            if any(line.startswith(b'ARCHIVE_RECEIVER_READY ') for line in log.read_bytes().splitlines()):
                identity = receiver_identity(probe_host, args.port)
                if (not matching_receiver(identity, output, args.bind, args.port)
                        or identity['pid'] != child.pid or child.poll() is not None):
                    raise RuntimeError('Archive startup identity differs; see ' + str(log))
                print(f'Archive receiver ready, PID {child.pid}; data -> {output}', flush=True)
                return child.pid
            time.sleep(.2)
        raise RuntimeError('Archive startup not confirmed; see ' + str(log))
    except BaseException:
        stop_created_receiver(args)
        raise


def ensure_receiver(args, check=False, auto_port=False):
    ipaddress.IPv4Address(args.bind)
    if not 1 <= args.port <= 65535:
        raise ValueError('Invalid archive port')
    check_bind_address(args.bind)
    output = resolve_path(args.output)
    output.mkdir(parents=True, exist_ok=True)
    if shutil.disk_usage(output).free < 1024**3:
        raise RuntimeError('Archive drive needs at least 1 GiB free')
    args._receiver_process = None
    preferred = args.port
    # A stable scan also finds the same folder's alternate receiver on a second
    # launch. No existing process or existing output directory is changed.
    ports = range(preferred, min(preferred + 32, 65536)) if auto_port and not check else [preferred]
    for port in ports:
        args.port = port
        try:
            pid = _ensure_port(args, output, check)
            if port != preferred:
                print(f'Archive port {preferred} is occupied; using {port}', flush=True)
            return pid
        except ArchivePortConflict:
            if not auto_port or check:
                raise
    args.port = preferred
    raise RuntimeError(f'No archive port available in {preferred}..{min(preferred+31,65535)}')


def parse_args(argv=None):
    config = load_settings()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, default=os.getenv(
        'AOA_ARCHIVE_OUTPUT', config['archive_output']))
    parser.add_argument('--bind', default=os.getenv('AOA_ARCHIVE_BIND', config['listen_address']))
    parser.add_argument('--port', type=int, default=int(os.getenv('AOA_ARCHIVE_PORT', config['archive_port'])))
    parser.add_argument('--check', action='store_true', help='Only check; never start a process')
    args = parser.parse_args(argv)
    args.output = resolve_path(args.output)
    return args


def main():
    args = parse_args()
    try:
        ensure_receiver(args, args.check)
    except (OSError, RuntimeError, ValueError, subprocess.SubprocessError) as error:
        print('Archive startup error: ' + str(error), file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())

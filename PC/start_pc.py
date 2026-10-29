"""Start the portable console and its optional raw archive receiver."""
import argparse
import subprocess
import sys

from pc_config import BASE, LOGS, load_settings

sys.path.insert(0, str(BASE / 'Console'))
import start_archive


def main(argv=None):
    config = load_settings()
    start_archive.LOGS = LOGS
    archive = start_archive.parse_args([])
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument('--archive-port', type=int)
    parser.add_argument('--no-open', action='store_true')
    selected, remaining = parser.parse_known_args(sys.argv[1:] if argv is None else argv)
    if selected.archive_port is not None:
        archive.port = selected.archive_port
    if not 0 <= archive.port <= 65535:
        raise ValueError('Archive port must be 0..65535')
    if archive.port:
        try:
            start_archive.ensure_receiver(archive, auto_port=True)
        except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as error:
            # An optional recorder failure must not prevent passive monitoring.
            print('Raw archive receiver unavailable; starting viewer only: ' + str(error), flush=True)
            archive.port = 0
    command = [
        sys.executable, str(BASE / 'Console/server.py'),
        '--board-host', config.get('fudan_ip', '192.168.10.51'),
        '--board-port', str(config.get('fudan_telemetry_port', 9000)),
        '--archive-port', str(archive.port),
        '--http-listen', config.get('http_listen', '127.0.0.1'),
        '--http-port', str(config['http_port']), '--reuse-running', '--auto-http-port',
    ]
    if not selected.no_open:
        command.append('--open')
    command += remaining
    try:
        result = subprocess.call(command, cwd=BASE / 'Console')
    except BaseException:
        start_archive.stop_created_receiver(archive)
        raise
    if result:
        start_archive.stop_created_receiver(archive)
    return result


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as error:
        print('PC startup error: ' + str(error), file=sys.stderr)
        raise SystemExit(1)

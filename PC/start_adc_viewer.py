"""Start the independent six-channel viewer with capture paused."""
import sys
import subprocess
from pc_config import BASE, load_settings


def main(argv=None):
    config = load_settings()
    print('Keep capture paused while Fudan is using the 7100. Stop Fudan first to resume standalone capture.')
    return subprocess.call([
        sys.executable, str(BASE/'adc_monitor/adc_monitor.py'), '--paused',
        '--board', config['board_ip'], '--board-port', str(config['board_port']),
        '--save-dir', str(config['adc_save_dir']),
    ] + list(sys.argv[1:] if argv is None else argv), cwd=BASE/'adc_monitor')


if __name__ == '__main__':
    raise SystemExit(main())

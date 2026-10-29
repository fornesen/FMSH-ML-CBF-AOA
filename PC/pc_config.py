"""Resolve the portable PC folder's settings independently of the cwd."""
import json
from pathlib import Path

BASE = Path(__file__).resolve().parent
LOGS = BASE / 'local_config' / 'logs'

def resolve_path(value, base=None):
    root = BASE if base is None else Path(base).resolve()
    path = Path(value).expanduser()
    return (path if path.is_absolute() else root / path).resolve()


def load_settings(base=None):
    root = BASE if base is None else Path(base).resolve()
    config = json.loads((root / 'settings.json').read_text(encoding='utf-8-sig'))
    local = root / 'local_config' / 'settings.json'
    if local.is_file():
        config.update(json.loads(local.read_text(encoding='utf-8-sig')))
    # The board's destination IP need not be assigned while the PC UI starts.
    config.setdefault('listen_address', '0.0.0.0')
    for name in ('archive_output', 'adc_save_dir'):
        config[name] = resolve_path(config[name], root)
    return config

"""Verify the self-contained runtime before starting any hardware process."""
import json
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]


def verify(root=ROOT):
    record = json.loads((root / 'DEPLOYMENT.json').read_text(encoding='utf-8'))
    if not record['ready']:
        raise RuntimeError('Incomplete board runtime: ' + ', '.join(record['missing']))


if __name__ == '__main__':
    verify()
    if sys.argv[1:] in (['start'], ['run']):
        python = str(ROOT / 'python.sh')
        os.execv(python, [python, str(ROOT / 'runtime/service.py'), sys.argv[1]])

"""Build the Windows field console with a local standard-library Python runtime.

No device connection, archive capture, server launch or production changes.
The output folder and optional ZIP must both be new paths.
"""
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys
import zipfile

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_PYTHON = ROOT / 'dependencies/windows/python'
DEFAULT_CXX = 'g++'
PC_FILES = (
    'pc_config.py', 'start_pc.py',
    'Console/server.py', 'Console/protocol.py', 'Console/joint_protocol.py',
    'Console/board_connection.py', 'Console/demo.py', 'Console/start_archive.py',
    'Console/recordings/capture.py',
)
EXCLUDED_RUNTIME_DIRS = {
    '__pycache__', 'site-packages', 'test', 'tests', 'idlelib', 'tkinter',
    'turtledemo', 'ensurepip', 'venv', 'pip', 'tcl',
}


def sha256(path):
    h = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            h.update(block)
    return h.hexdigest()


def within(path, root):
    return path == root or root in path.parents


def runtime_files(home):
    for name in ('python.exe', 'python313.dll', 'LICENSE.txt', 'Lib/encodings/__init__.py'):
        if not (home / name).is_file():
            raise FileNotFoundError('Python 3.13 runtime is incomplete: ' + str(home / name))
    files = [(home / name, Path(name)) for name in (
        'python.exe', 'python3.dll', 'python313.dll', 'vcruntime140.dll',
        'vcruntime140_1.dll', 'LICENSE.txt') if (home / name).is_file()]
    for source in sorted((home / 'Lib').rglob('*')):
        relative = source.relative_to(home)
        if (source.is_file() and not any(p.lower() in EXCLUDED_RUNTIME_DIRS for p in relative.parts)
                and source.suffix.lower() not in ('.pyc', '.pyo')
                and source.name not in ('turtle.py',)):
            if not within(source.resolve(), home):
                raise ValueError('Runtime symlink escapes Python home: ' + str(source))
            files.append((source, relative))
    for source in sorted((home / 'DLLs').iterdir()):
        name = source.name.lower()
        if (source.is_file() and source.suffix.lower() in ('.pyd', '.dll')
                and not name.startswith(('_test', '_ctypes_test', '_tkinter', 'tcl', 'tk'))):
            files.append((source, source.relative_to(home)))
    return files


def pc_files():
    files = [(ROOT / 'PC' / name, Path('PC') / name) for name in PC_FILES]
    web = ROOT / 'PC/Console/web'
    files.extend((source, Path('PC/Console/web') / source.relative_to(web))
                 for source in sorted(web.rglob('*')) if source.is_file()
                 and '__pycache__' not in source.parts)
    for source, _ in files:
        if not source.is_file():
            raise FileNotFoundError(source)
        if not within(source.resolve(), ROOT / 'PC'):
            raise ValueError('PC path escapes source tree: ' + str(source))
    files.extend((ROOT / 'common' / name, Path('common') / name) for name in
                 ('__init__.py', 'archive_io.py', 'archive_receiver.py', 'archive_restore.py'))
    return files


def write(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding='utf-8', newline='\n')


def launcher(output, name, script):
    (output / name).write_bytes((
        '@echo off\r\nsetlocal\r\ncd /d "%~dp0"\r\n'
        '"%~dp0runtime\\python\\python.exe" -B "%~dp0' + script.replace('/', '\\')
        + '" %*\r\nif errorlevel 1 pause\r\n').encode('ascii'))


SMOKE = '''"""Local imports and path checks only. Never opens network ports."""
import hashlib
import importlib.util
import json
from pathlib import Path
import sys

ROOT = Path(sys.argv[1]).resolve()
assert Path(sys.executable).resolve() == ROOT / 'runtime/python/python.exe'
assert not any('site-packages' in p.lower() for p in sys.path)
assert importlib.util.find_spec('numpy') is None
import asyncio, bz2, ctypes, hashlib, http.server, lzma, socket, sqlite3, ssl, subprocess, urllib.request, webbrowser, zlib
import pc_config, server, protocol, joint_protocol, board_connection, start_archive
from common import archive_io, archive_receiver, archive_restore
sys.path.insert(0, str(ROOT / 'PC/Console/recordings'))
import capture
config = pc_config.load_settings()
assert Path(config['archive_output']).is_relative_to(ROOT / 'PC')
assert 'pc_ip' not in config
print(json.dumps({'runtime':sys.version.split()[0], 'stdlib_only':True,
    'archive_output':Path(config['archive_output']).relative_to(ROOT).as_posix(), 'http_port':config['http_port'],
    'archive_port':config['archive_port'], 'board':config['fudan_ip'],
    'network_ports_opened':False}, ensure_ascii=True))
'''

def build(args):
    output, home = args.output.resolve(), args.python_home.resolve()
    archive = args.zip.resolve() if args.zip else None
    if output.exists():
        raise FileExistsError('Refusing to overwrite output: ' + str(output))
    if archive and archive.exists():
        raise FileExistsError('Refusing to overwrite ZIP: ' + str(archive))
    if within(output, home) or (archive and within(archive, output)):
        raise ValueError('Output cannot be inside Python home, and ZIP must be outside output')
    runtime, pc = runtime_files(home), pc_files()
    plan = dict(output=str(output), archive=str(archive) if archive else None,
                python_source=str(home), python_runtime_files=len(runtime), pc_source_files=len(pc),
                runtime_bytes=sum(p.stat().st_size for p, _ in runtime),
                replay='rebuild from current PS source with static runtime',
                credentials=False, raw_data=False, third_party_python_packages=False)
    if args.plan:
        return plan
    output.mkdir(parents=True, exist_ok=False)
    print('Copying PC files and standard-library Python runtime', flush=True)
    for source, relative in pc:
        target = output / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, target)
    for source, relative in runtime:
        target = output / 'runtime/python' / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, target)
    # _pth ignores registry/PYTHONPATH/site-packages and remains relocatable.
    write(output / 'runtime/python/python313._pth',
          '.\nLib\nDLLs\n../../\n../../PC\n../../PC/Console\n')
    settings = json.loads((ROOT / 'PC/settings.json').read_text(encoding='utf-8-sig'))
    settings.pop('pc_ip', None)
    settings.pop('telemetry_port', None)
    settings.update(listen_address='0.0.0.0', http_listen='127.0.0.1', http_port=8775,
                    archive_port=7012, archive_output='data/hardware_7100/board',
                    adc_save_dir='data/adc_capture', fudan_ip='192.168.10.51',
                    fudan_telemetry_port=9000, board_ip='192.168.10.50', board_port=7)
    write(output / 'PC/settings.json', json.dumps(settings, indent=2) + '\n')
    launcher(output, 'start_pc.cmd', 'PC/start_pc.py')
    for source in sorted((ROOT / 'Documentation').glob('*.md')):
        write(output / 'Documentation' / source.name, source.read_text(encoding='utf-8'))
    print('Building the current offline PS replay executable', flush=True)
    native = output / 'PC/Console/recordings/bin/aoa_capture_replay.exe'
    subprocess.run([sys.executable, str(ROOT / 'PS/tools/recordings/build_replay.py'),
                    '--cxx', args.cxx, '--output', str(native)], cwd=ROOT, check=True)
    # Carry the vendored JSON copyright/license notice with the native tool.
    notice = (ROOT / 'PS/src/console/json.hpp').read_text(encoding='utf-8').splitlines()[:8]
    write(output / 'licenses/nlohmann-json-notice.txt', '\n'.join(notice) + '\n'
          'License text: https://github.com/nlohmann/json/blob/v3.11.2/LICENSE.MIT\n')
    print('Checking the copied runtime without any network operation', flush=True)
    smoke = subprocess.run([str(output / 'runtime/python/python.exe'), '-B', '-c', SMOKE,
                            str(output)], cwd=output, check=True,
                           capture_output=True, text=True, encoding='utf-8')
    smoke_result = json.loads(smoke.stdout)
    files = [path for path in output.rglob('*') if path.is_file() and '__pycache__' not in path.parts]
    if archive:
        print('Writing ZIP', flush=True)
        archive.parent.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(archive, 'x', compression=zipfile.ZIP_DEFLATED, compresslevel=9) as bundle:
            for path in sorted(output.rglob('*')):
                if not path.is_file() or '__pycache__' in path.parts:
                    continue
                item = zipfile.ZipInfo(output.name + '/' + path.relative_to(output).as_posix(),
                                       date_time=(2026, 10, 27, 0, 0, 0))
                item.compress_type = zipfile.ZIP_DEFLATED
                item.external_attr = 0o100644 << 16
                bundle.writestr(item, path.read_bytes(), compresslevel=9)
    return dict(plan, files=len(files), runtime_smoke=smoke_result,
                archive_sha256=sha256(archive) if archive else None)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True, help='New portable-folder path')
    parser.add_argument('--zip', type=Path, help='Optional new ZIP path, outside output folder')
    parser.add_argument('--python-home', type=Path, default=DEFAULT_PYTHON)
    parser.add_argument('--cxx', default=DEFAULT_CXX)
    parser.add_argument('--plan', action='store_true', help='Inspect source inventory without writing/building')
    args = parser.parse_args()
    try:
        print(json.dumps(build(args), indent=2, ensure_ascii=False))
    except (OSError, ValueError, subprocess.SubprocessError) as error:
        print('Field package failed; an incomplete output is preserved: ' + str(error), file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())

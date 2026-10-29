#!/usr/bin/env python3
"""Export installed ARM64 runtime dependencies on the board, not from old SDK debs.

No arguments/--plan only print a plan. Collection requires --collect --output
NEW_DIRECTORY --aoa OWN_TRUSTED_ELF --npu OWN_TRUSTED_ELF. ldd loader tracing is
used only for the specified trusted programs and their installed libraries; no
application main, inference, installation, download, or device operation runs.
The output's python/ and lib/ can be moved into V2 assets/board unchanged.
"""
import sys
sys.dont_write_bytecode = True
import argparse
from collections import deque
import hashlib
import json
import os
from pathlib import Path
import platform
import re
import shutil
import struct
import subprocess
import sysconfig


PLUGIN_PATTERNS = ('libicraft*.so*', 'libaxizg330*.so*', 'libsocketzg330*.so*',
                   'libFireby*.so*', 'libirpc*.so*', 'libcustomop*.so*')
EXCLUDED_DIRS = {'site-packages', 'dist-packages', 'test', 'tests', '__pycache__', '.git'}
EXCLUDED_FILES = {'sitecustomize.py', 'usercustomize.py'}


class DependencyError(RuntimeError):
    pass


def sha256(path):
    digest = hashlib.sha256()
    with Path(path).open('rb') as source:
        for chunk in iter(lambda: source.read(1024*1024), b''):
            digest.update(chunk)
    return digest.hexdigest()


def elf_info(path):
    """Read ELF metadata/interpreter without loading the file as executable code."""
    with Path(path).open('rb') as source:
        header = source.read(64)
        if header[:4] != b'\x7fELF':
            return None
        if len(header) != 64 or header[4] != 2 or header[5] not in (1, 2):
            raise DependencyError('Requires a complete 64-bit ELF: ' + str(path))
        endian = '<' if header[5] == 1 else '>'
        values = struct.unpack(endian+'16sHHIQQQIHHHHHH', header)
        interpreter = None
        if values[10] > 1024 or (values[10] and values[9] < 56):
            raise DependencyError('Invalid ELF program headers: ' + str(path))
        for index in range(values[10]):
            source.seek(values[5]+index*values[9])
            raw = source.read(56)
            if len(raw) != 56:
                raise DependencyError('Truncated ELF program header: ' + str(path))
            segment = struct.unpack(endian+'IIQQQQQQ', raw)
            if segment[0] == 3:
                if not 1 <= segment[5] <= 4096:
                    raise DependencyError('Invalid ELF interpreter length')
                source.seek(segment[2])
                interpreter = source.read(segment[5]).rstrip(b'\0').decode('utf8')
        return {'class': 64, 'machine': values[2], 'architecture': 'aarch64' if values[2] == 183 else 'other',
                'interpreter': interpreter}


def require_arm64(path):
    info = elf_info(path)
    if info is None or info['machine'] != 183:
        raise DependencyError('Not an installed ARM64 ELF: ' + str(path))
    return info


def parse_ldd(text):
    result = []
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith('linux-vdso'):
            continue
        if '=> not found' in line:
            raise DependencyError('Missing shared library: ' + line)
        match = re.match(r'(\S+)\s+=>\s+(/.+?)\s+\(0x[0-9a-fA-F]+\)$', line)
        if match:
            result.append((match[1], Path(match[2])))
        elif line.startswith('/'):
            path = re.sub(r'\s+\(0x[0-9a-fA-F]+\)$', '', line)
            result.append((Path(path).name, Path(path)))
        elif line not in ('statically linked', 'not a dynamic executable'):
            raise DependencyError('Unrecognized ldd output: ' + line[:300])
    return result


def trace_dependencies(path):
    env = dict(os.environ)
    for key in ('LD_PRELOAD', 'LD_AUDIT', 'LD_DEBUG'):
        env.pop(key, None)
    result = subprocess.run(['ldd', str(path)], stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                            text=True, timeout=30, check=False, env=env)
    text = result.stdout + result.stderr
    if result.returncode and 'not a dynamic executable' not in text:
        raise DependencyError('ldd failed for %s: %s' % (path, text[:1500]))
    return parse_ldd(result.stdout)


def runtime_layout():
    version = '%d.%d' % sys.version_info[:2]
    return {'executable': Path(sys.executable), 'stdlib': Path(sysconfig.get_path('stdlib')),
            'platstdlib': Path(sysconfig.get_path('platstdlib')),
            'extensions': Path(sysconfig.get_config_var('DESTSHARED') or
                               str(Path(sysconfig.get_path('stdlib'))/'lib-dynload')),
            'version': version, 'virtual_environment': sys.prefix != sys.base_prefix}


def within(path, root):
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def stdlib_files(root, allowed):
    """Copy runtime stdlib, excluding development config and startup customizations."""
    def walk(directory, relative, ancestors):
        real = directory.resolve(strict=True)
        if not any(within(real, base) for base in allowed):
            raise DependencyError('Standard-library link escapes installed roots: ' + str(directory))
        if real in ancestors:
            raise DependencyError('Standard-library symlink cycle: ' + str(directory))
        for entry in sorted(directory.iterdir()):
            if entry.name in EXCLUDED_DIRS or entry.name in EXCLUDED_FILES or entry.name.startswith(('_test', 'test_')):
                continue
            if entry.is_dir():
                # Ubuntu keeps development Makefiles/libpython links here. Any
                # real libpython dependency is collected by the ELF closure.
                if entry.name.startswith('config-'):
                    continue
                yield from walk(entry, relative/entry.name, ancestors | {real})
            elif entry.is_file() and entry.suffix not in ('.pyc', '.pyo', '.a', '.o'):
                target = entry.resolve(strict=True)
                if not any(within(target, base) for base in allowed):
                    raise DependencyError('Standard-library file link escapes installed roots: ' + str(entry))
                yield entry, relative/entry.name
    yield from walk(root, Path(), set())


def collect(output, aoa, npu, layout=None, plugin_dirs=(), trace=trace_dependencies):
    """Collect only after explicit caller authorization; retain a FAILED manifest on error."""
    output, aoa, npu = Path(output).resolve(), Path(aoa).absolute(), Path(npu).absolute()
    if output.exists():
        raise FileExistsError('Output must be a new directory; nothing will be overwritten: ' + str(output))
    if platform.machine().lower() not in ('aarch64', 'arm64'):
        raise DependencyError('Run collection on the target ARM64 board; use --plan here')
    layout = runtime_layout() if layout is None else layout
    if layout.get('virtual_environment'):
        raise DependencyError('Use the system Python, not a user virtual environment')
    roots = [Path(layout[key]) for key in ('stdlib', 'platstdlib', 'extensions')]
    roots = list(dict.fromkeys(root.resolve(strict=True) for root in roots if root.exists()))
    if not roots or not (Path(layout['stdlib'])/'encodings'/'__init__.py').is_file():
        raise DependencyError('Installed Python standard library is incomplete')
    if any(within(output, root) for root in roots):
        raise DependencyError('Output cannot be inside the installed Python library')
    programs = {'aoa': aoa, 'npu': npu, 'python': Path(layout['executable']).absolute()}
    program_info = {name: require_arm64(path) for name, path in programs.items()}
    if len({Path(info['interpreter']).name for info in program_info.values() if info['interpreter']}) > 1:
        raise DependencyError('Programs use incompatible ELF interpreters')
    manifest = {'format': 1, 'status': 'collecting', 'architecture': 'aarch64',
                'python_version': layout['version'], 'files': [], 'trusted_inputs': {},
                'ldd_trace_inputs': [], 'plugins': [], 'errors': [],
                'network_operations': False, 'application_execution': False,
                'sdk_source': 'currently installed target runtime only; old 3.33.1 debs are not substitutes',
                'scope': 'installed program/Python ELF closure and matching Icraft directory plugins; not an OS image',
                'os_requirements': ['Linux ARM64 kernel and existing device drivers', '/dev, /proc, /sys',
                                    'OS resolver/network configuration and system data are not copied'],
                'runtime_loading_limit': 'unobserved arbitrary dlopen paths outside the discovered Icraft directories need an explicit --plugin-dir and later target validation'}
    copied = {}
    output.mkdir(parents=True, exist_ok=False)

    def copy(source, relative):
        source = Path(source)
        real = source.resolve(strict=True)
        digest = sha256(real)
        name = relative.as_posix()
        if name in copied:
            if copied[name]['sha256'] != digest:
                raise DependencyError('Different runtime files share output name: ' + name)
            if str(source) not in copied[name]['sources']:
                copied[name]['sources'].append(str(source))
            return
        info = elf_info(real)
        if info is not None and info['machine'] != 183:
            raise DependencyError('Mixed architecture in private runtime: ' + str(source))
        destination = output/relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        with real.open('rb') as incoming, destination.open('xb') as outgoing:
            shutil.copyfileobj(incoming, outgoing, 1024*1024)
        if sha256(destination) != digest:
            raise DependencyError('Runtime source changed while copying: ' + str(source))
        destination.chmod(real.stat().st_mode & 0o777)
        record = {'path': name, 'sources': [str(source)], 'resolved_source': str(real),
                  'source_was_symlink': source.is_symlink(), 'size_bytes': real.stat().st_size,
                  'sha256': digest, 'elf': info}
        manifest['files'].append(record)
        copied[name] = record

    try:
        for name, path in programs.items():
            manifest['trusted_inputs'][name] = {'path': str(path), 'resolved': str(path.resolve()),
                                                 'sha256': sha256(path), 'elf': program_info[name]}
        copy(programs['python'], Path('python/bin/python3'))
        copy(programs['python'], Path('python/bin/python'+layout['version']))
        native = []
        traversed = set()
        for key in ('stdlib', 'platstdlib', 'extensions'):
            root = Path(layout[key])
            if not root.exists():
                continue
            prefix = Path('python/lib/python'+layout['version'])
            if key == 'extensions':
                prefix /= 'lib-dynload'
            identity = (root.resolve(), prefix)
            if identity in traversed:
                continue
            traversed.add(identity)
            for source, relative in stdlib_files(root, roots):
                copy(source, prefix/relative)
                if elf_info(source) is not None:
                    native.append(source)
        queue = deque([*programs.values(), *native])
        visited, scanned_plugin_dirs = set(), set()
        libraries = []

        def scan_plugins(directory):
            directory = Path(directory)
            if not directory.is_dir() or directory.resolve() in scanned_plugin_dirs:
                return
            scanned_plugin_dirs.add(directory.resolve())
            for pattern in PLUGIN_PATTERNS:
                for source in sorted(directory.glob(pattern)):
                    if source.is_file():
                        require_arm64(source)
                        copy(source, Path('lib')/source.name)
                        queue.append(source)
                        if str(source) not in manifest['plugins']:
                            manifest['plugins'].append(str(source))

        for directory in plugin_dirs:
            if not Path(directory).is_dir():
                raise DependencyError('Explicit plugin directory does not exist: ' + str(directory))
            scan_plugins(directory)
        while queue:
            source = Path(queue.popleft())
            real = source.resolve(strict=True)
            if real in visited:
                continue
            visited.add(real)
            info = require_arm64(source)
            manifest['ldd_trace_inputs'].append(str(source))
            dependencies = list(trace(source))
            if info['interpreter']:
                dependencies.append((Path(info['interpreter']).name, Path(info['interpreter'])))
            for soname, path in dependencies:
                if Path(soname).name != soname or '/' in soname or '\\' in soname:
                    raise DependencyError('Invalid dependency SONAME: ' + soname)
                require_arm64(path)
                copy(path, Path('lib')/soname)
                queue.append(path)
                libraries.append(str(path))
                if 'icraft' in soname.lower():
                    scan_plugins(Path(path).parent)
        interpreter = program_info['python']['interpreter']
        loader = ('"$root/lib/%s" --library-path "$root/lib" ' % Path(interpreter).name) if interpreter else ''
        scripts = {
            'run_python.sh': '#!/bin/sh\nset -eu\nroot=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)\nunset PYTHONPATH\nexport PYTHONHOME="$root/python" PYTHONNOUSERSITE=1 LD_LIBRARY_PATH="$root/lib"\nexec '+loader+'"$root/python/bin/python3" "$@"\n',
            'run_elf.sh': '#!/bin/sh\nset -eu\nroot=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)\n[ "$#" -ge 1 ] || { echo "Usage: run_elf.sh TRUSTED_PROGRAM [args...]" >&2; exit 2; }\nexport LD_LIBRARY_PATH="$root/lib"\nexec '+loader+'"$@"\n'}
        for name, text in scripts.items():
            destination = output/name
            with destination.open('x', encoding='utf8', newline='\n') as stream:
                stream.write(text)
            destination.chmod(0o755)
            manifest['files'].append({'path': name, 'sources': ['generated launcher'], 'resolved_source': None,
                                      'source_was_symlink': False, 'size_bytes': destination.stat().st_size,
                                      'sha256': sha256(destination), 'elf': None})
        manifest['status'] = 'collected_not_target_smoke_tested'
        manifest['native_extension_count'] = len(set(map(str, native)))
        manifest['plugin_directories'] = sorted(map(str, scanned_plugin_dirs))
    except Exception as error:
        manifest['status'] = 'failed'
        manifest['errors'].append(str(error))
        raise
    finally:
        with (output/'RUNTIME_MANIFEST.json').open('x', encoding='utf8') as stream:
            json.dump(manifest, stream, ensure_ascii=False, indent=2, allow_nan=False)
            stream.write('\n')
    return manifest


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    action = parser.add_mutually_exclusive_group()
    action.add_argument('--plan', action='store_true')
    action.add_argument('--collect', action='store_true')
    parser.add_argument('--output', type=Path)
    parser.add_argument('--aoa', type=Path)
    parser.add_argument('--npu', type=Path)
    parser.add_argument('--plugin-dir', action='append', type=Path, default=[])
    args = parser.parse_args(argv)
    if not args.collect:
        layout = runtime_layout()
        print(json.dumps({'action': 'plan', 'filesystem_changes': False, 'network_operations': False,
                          'external_commands': False, 'application_execution': False,
                          'required_for_collection': '--collect --output NEW_DIRECTORY --aoa OWN_ELF --npu OWN_ELF',
                          'layout': {k: str(v) if isinstance(v, Path) else v for k, v in layout.items()},
                          'output': None if args.output is None else str(args.output),
                          'plugin_patterns': PLUGIN_PATTERNS}, ensure_ascii=False, indent=2))
        return 0
    if args.output is None or args.aoa is None or args.npu is None:
        parser.error('--collect requires --output, --aoa and --npu')
    manifest = collect(args.output, args.aoa, args.npu, plugin_dirs=args.plugin_dir)
    print(json.dumps({'status': manifest['status'], 'files': len(manifest['files']),
                      'output': str(args.output.resolve()), 'target_smoke_test_performed': False}, indent=2))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())

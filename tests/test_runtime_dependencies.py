"""Offline-only tests: fabricated ELF headers and mocked ldd, no device access."""
import contextlib
import importlib.util
import io
import json
from pathlib import Path
import struct
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

SPEC = importlib.util.spec_from_file_location('runtime_collector',
    Path(__file__).resolve().parents[1]/'tools/collect_board_dependencies.py')
tool = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(tool)


def elf(path, machine=183, interpreter=None, payload=b''):
    path.parent.mkdir(parents=True, exist_ok=True)
    ident = b'\x7fELF'+bytes([2, 1, 1])+bytes(9)
    header = struct.pack('<16sHHIQQQIHHHHHH', ident, 3, machine, 1, 0,
                         64 if interpreter else 0, 0, 0, 64, 56, 1 if interpreter else 0, 0, 0, 0)
    if interpreter:
        name = str(interpreter).encode()+b'\0'
        header += struct.pack('<IIQQQQQQ', 3, 0, 120, 0, 0, len(name), len(name), 1)+name
    path.write_bytes(header+payload)
    return path


class Installed:
    def __init__(self, root):
        self.root = root
        self.lib = root/'installed/lib'
        self.loader = elf(self.lib/'ld-linux-aarch64.so.1')
        self.aoa = elf(root/'installed/bin/icraft_aoa_ml3', interpreter=self.loader)
        self.npu = elf(root/'installed/bin/icraft_npu_yolo', interpreter=self.loader)
        self.python = elf(root/'installed/bin/python3.8', interpreter=self.loader)
        self.stdlib = root/'installed/python3.8'
        (self.stdlib/'encodings').mkdir(parents=True)
        (self.stdlib/'encodings/__init__.py').write_text('# installed stdlib', encoding='utf8')
        (self.stdlib/'os.py').write_text('# installed stdlib', encoding='utf8')
        (self.stdlib/'site-packages').mkdir()
        (self.stdlib/'site-packages/not_runtime.py').write_text('user-package', encoding='utf8')
        (self.stdlib/'test').mkdir()
        (self.stdlib/'test/test_demo.py').write_text('test only', encoding='utf8')
        self.extensions = self.stdlib/'lib-dynload'
        self.ssl_extension = elf(self.extensions/'_ssl.cpython-38-aarch64-linux-gnu.so')
        self.libc = elf(self.lib/'libc.so.6')
        self.xrt = elf(self.lib/'libicraft_xrt.so')
        self.plugin = elf(self.lib/'libicraft_zg330backend.so')
        self.ssl = elf(self.lib/'libssl.so.1.1')
        self.crypto = elf(self.lib/'libcrypto.so.1.1')
        self.layout = {'executable': self.python, 'stdlib': self.stdlib,
                       'platstdlib': self.stdlib, 'extensions': self.extensions,
                       'version': '3.8', 'virtual_environment': False}
        self.calls = []

    def trace(self, path):
        self.calls.append(Path(path))
        graph = {self.aoa: [self.xrt, self.libc], self.npu: [self.xrt], self.python: [self.libc],
                 self.ssl_extension: [self.ssl], self.xrt: [self.libc],
                 self.plugin: [self.crypto], self.ssl: [self.crypto]}
        return [(value.name, value) for value in graph.get(Path(path), [])]

    def collect(self, output, **kwargs):
        with patch.object(tool.platform, 'machine', return_value='aarch64'), \
             patch('subprocess.run', side_effect=AssertionError('no actual ldd or application execution')):
            return tool.collect(output, self.aoa, self.npu, layout=self.layout, trace=self.trace, **kwargs)


class RuntimeDependenciesTests(unittest.TestCase):
    def test_default_and_explicit_plan_never_collect_or_start_external_commands(self):
        for args in ([], ['--plan'], ['--output', 'new-runtime', '--aoa', 'own-aoa', '--npu', 'own-npu']):
            output = io.StringIO()
            with patch.object(tool, 'collect', side_effect=AssertionError('plan must not collect')), \
                 patch('subprocess.run', side_effect=AssertionError('plan must not run commands')), \
                 contextlib.redirect_stdout(output):
                self.assertEqual(tool.main(args), 0)
            plan = json.loads(output.getvalue())
            self.assertEqual(plan['action'], 'plan')
            self.assertFalse(plan['filesystem_changes'])
            self.assertFalse(plan['network_operations'])

    def test_explicit_collect_requires_all_three_paths(self):
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            tool.main(['--collect', '--output', 'new-runtime'])

    def test_closure_includes_extensions_interpreter_and_plugins(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            installed = Installed(root)
            output = root/'export'
            manifest = installed.collect(output)
            self.assertEqual(manifest['status'], 'collected_not_target_smoke_tested')
            for name in ('libc.so.6', 'libicraft_xrt.so', 'libicraft_zg330backend.so',
                         'libssl.so.1.1', 'libcrypto.so.1.1', 'ld-linux-aarch64.so.1'):
                self.assertTrue((output/'lib'/name).is_file(), name)
            self.assertTrue((output/'python/lib/python3.8/lib-dynload'/installed.ssl_extension.name).is_file())
            self.assertFalse((output/'python/lib/python3.8/site-packages').exists())
            self.assertFalse((output/'python/lib/python3.8/test').exists())
            self.assertIn(installed.plugin, installed.calls)
            for record in manifest['files']:
                target = output/record['path']
                self.assertEqual(tool.sha256(target), record['sha256'])
                self.assertFalse(target.is_symlink())
                if record['elf']:
                    self.assertEqual(record['elf']['machine'], 183)
            launcher = (output/'run_python.sh').read_text(encoding='utf8')
            self.assertIn('PYTHONHOME="$root/python"', launcher)
            self.assertIn('unset PYTHONPATH', launcher)
            self.assertIn('"$root/lib/ld-linux-aarch64.so.1" --library-path "$root/lib"', launcher)
            self.assertEqual(json.loads((output/'RUNTIME_MANIFEST.json').read_text(encoding='utf8')), manifest)

    def test_existing_output_is_never_deleted_or_overwritten(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            installed = Installed(root)
            output = root/'export'
            output.mkdir()
            marker = output/'keep.txt'
            marker.write_text('keep', encoding='utf8')
            with self.assertRaises(FileExistsError):
                installed.collect(output)
            self.assertEqual(marker.read_text(encoding='utf8'), 'keep')
            self.assertEqual(installed.calls, [])

    def test_development_config_and_startup_customizations_are_excluded(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            installed = Installed(root)
            development = installed.stdlib/'config-3.8-aarch64-linux-gnu'
            development.mkdir()
            # A real Ubuntu development link may escape stdlib. Do not even
            # traverse this directory; the host need not support symlinks.
            (development/'libpython3.8.so').write_bytes(b'development link placeholder')
            for name in ('sitecustomize.py', 'usercustomize.py'):
                (installed.stdlib/name).write_text('raise RuntimeError("host customization")', encoding='utf8')
            (installed.stdlib/'config-runtime.py').write_text('# ordinary runtime module', encoding='utf8')
            original_iterdir = Path.iterdir
            def iterdir(path):
                if path == development:
                    raise AssertionError('development configuration must not be traversed')
                return original_iterdir(path)
            with patch.object(Path, 'iterdir', iterdir):
                installed.collect(root/'export')
            output = root/'export/python/lib/python3.8'
            self.assertFalse((output/development.name).exists())
            self.assertFalse((output/'sitecustomize.py').exists())
            self.assertFalse((output/'usercustomize.py').exists())
            self.assertTrue((output/'config-runtime.py').is_file())
            self.assertTrue((output/'os.py').is_file())
            self.assertTrue((output/'lib-dynload'/installed.ssl_extension.name).is_file())

    def test_real_elf_libpython_dependency_is_collected_outside_stdlib(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            installed = Installed(root)
            libpython = elf(installed.lib/'libpython3.8.so.1.0')
            original_trace = installed.trace
            installed.trace = lambda path: original_trace(path)+(
                [(libpython.name, libpython)] if Path(path)==installed.python else [])
            manifest = installed.collect(root/'export')
            target = root/'export/lib'/libpython.name
            self.assertEqual(target.read_bytes(), libpython.read_bytes())
            self.assertIn(libpython, installed.calls)
            self.assertIn('lib/'+libpython.name, [item['path'] for item in manifest['files']])

    def test_ordinary_stdlib_links_still_cannot_escape_to_user_paths(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            installed = Installed(root)
            module = installed.stdlib/'runtime_link.py'
            module.write_text('# simulated symlink', encoding='utf8')
            outside = root/'user-environment/runtime.py'
            original_resolve = Path.resolve
            def resolve(path, *args, **kwargs):
                return outside if path == module else original_resolve(path, *args, **kwargs)
            with patch.object(Path, 'resolve', resolve):
                with self.assertRaisesRegex(tool.DependencyError, 'escapes installed roots'):
                    installed.collect(root/'export')

    def test_missing_dependency_records_failed_manifest(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            installed = Installed(root)
            installed.trace = lambda path: tool.parse_ldd('libmissing.so => not found')
            with self.assertRaisesRegex(tool.DependencyError, 'Missing shared library'):
                installed.collect(root/'export')
            report = json.loads((root/'export/RUNTIME_MANIFEST.json').read_text(encoding='utf8'))
            self.assertEqual(report['status'], 'failed')
            self.assertTrue(report['errors'])

    def test_wrong_architecture_and_deb_inputs_are_rejected_before_creation(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            installed = Installed(root)
            elf(installed.aoa, machine=62)
            with self.assertRaisesRegex(tool.DependencyError, 'Not an installed ARM64 ELF'):
                installed.collect(root/'export')
            self.assertFalse((root/'export').exists())
            installed.aoa.write_bytes(b'!<arch>\nold-3.33.1-deb')
            with self.assertRaises(tool.DependencyError):
                installed.collect(root/'export')

    def test_soname_file_is_dereferenced(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            installed = Installed(root)
            actual = installed.ssl.with_name('libssl.so.1.1.real')
            installed.ssl.rename(actual)
            try:
                installed.ssl.symlink_to(actual)
            except OSError as error:
                self.skipTest('Host cannot create test symlinks: '+str(error))
            manifest = installed.collect(root/'export')
            target = root/'export/lib/libssl.so.1.1'
            self.assertFalse(target.is_symlink())
            self.assertEqual(target.read_bytes(), actual.read_bytes())
            record = next(r for r in manifest['files'] if r['path']=='lib/libssl.so.1.1')
            self.assertTrue(record['source_was_symlink'])

    def test_library_name_collision_is_not_silently_overwritten(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            installed = Installed(root)
            other = elf(root/'different/libc.so.6', payload=b'different ABI')
            original_trace = installed.trace
            installed.trace = lambda path: [('libc.so.6', other)] if Path(path)==installed.npu else original_trace(path)
            with self.assertRaisesRegex(tool.DependencyError, 'share output name'):
                installed.collect(root/'export')
            self.assertEqual((root/'export/lib/libc.so.6').read_bytes(), installed.libc.read_bytes())

    def test_ldd_parser_ignores_vdso_but_preserves_soname_and_interpreter(self):
        result = tool.parse_ldd('linux-vdso.so.1 (0x0001)\n libx.so.1 => /lib/aarch64-linux-gnu/libx.so.1 (0xabc)\n /lib/ld-linux-aarch64.so.1 (0x0002)\n')
        self.assertEqual([row[0] for row in result], ['libx.so.1', 'ld-linux-aarch64.so.1'])
        with self.assertRaises(tool.DependencyError):
            tool.parse_ldd('libbad.so => not found')

    def test_ldd_uses_trace_tool_only_and_tolerates_permission_warning(self):
        result = SimpleNamespace(returncode=0, stdout='libx.so.1 => /lib/libx.so.1 (0xabc)\n',
                                 stderr='ldd: warning: no execution permission for trusted library\n')
        with patch('subprocess.run', return_value=result) as run:
            dependencies = tool.trace_dependencies(Path('/own/trusted-app'))
        self.assertEqual(run.call_args.args[0], ['ldd', str(Path('/own/trusted-app'))])
        self.assertEqual(dependencies, [('libx.so.1', Path('/lib/libx.so.1'))])


if __name__ == '__main__':
    unittest.main()

"""The board payload stays small and incomplete dependencies cannot look ready."""
import importlib.util
import io
import json
from pathlib import Path
import tarfile
import tempfile
import unittest
from unittest.mock import patch

SPEC = importlib.util.spec_from_file_location('board_package', Path(__file__).resolve().parents[1] / 'tools/prepare_board.py')
tool = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(tool)


class BoardPackageTests(unittest.TestCase):
    def test_active_job_uses_current_project_relative_paths(self):
        root = Path(__file__).resolve().parents[1]
        job = json.loads((root / 'Board/config/job.json').read_text(encoding='utf-8'))
        for name in ('project_root', 'runtime_base', 'bin_dir', 'npu_model_json',
                     'npu_model_raw', 'compensation_file'):
            self.assertFalse(Path(job[name]).is_absolute(), name)
            self.assertNotIn('V1.', job[name])
        self.assertEqual(tool.TARGET, '/root/aoa_system/V2.0')

    def make_source(self, root):
        files = {
            'Board/config/job.json': '{}', 'Board/runtime/run_board.py': '# board',
            'Board/runtime/helper.sh': '#!/bin/sh\n',
            'Board/systemd/aoa-pl.service': '[Unit]\n',
            'Board/systemd/aoa-v2.service': '[Unit]\n',
            'PS/calibration/calibrate.py': '# wizard',
            'PS/calibration/calibration_math.py': '# calibration',
            'PS/calibration/delay_math.py': '# delay',
            'PC/Console/web/index.html': '<html>PC only</html>',
            'PS/npu/training/model.py': '# training only',
            'common/__init__.py': '', 'common/archive_io.py': '# format',
            'common/frequency_compensation.py': '# frequency table selector',
            'common/archive_receiver.py': '# PC only',
            'assets/board/bin/icraft_aoa_ml3': 'reference',
            'assets/board/python/bin/python3': 'ARM64 Python',
            'assets/board/python/lib/python3.9/os.py': '# stdlib',
            'assets/board/lib/ld-linux-aarch64.so.1': 'loader',
            'assets/board/lib/libpython3.9.so.1.0': 'library',
        }
        files.update({'Board/' + name: '#!/bin/sh\n' for name in ('start.sh', 'stop.sh', 'status.sh', 'python.sh', 'install_service.sh')})
        for name, text in files.items():
            path = root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text)

    def test_missing_private_runtime_blocks_formal_package(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with patch.object(tool, 'ROOT', root):
                state = tool.readiness()
                self.assertFalse(state['ready'])
                self.assertIn('python/bin/python3', state['missing'])
                with self.assertRaisesRegex(RuntimeError, 'incomplete'):
                    tool.package(root / 'release')
            self.assertFalse((root / 'release').exists())

    def test_inspection_stage_contains_runtime_only(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.make_source(root)
            with patch.object(tool, 'ROOT', root):
                state = tool.package(root / 'stage', allow_incomplete=True)
            self.assertFalse(state['ready'])
            self.assertTrue((root / 'stage/runtime/run_board.py').is_file())
            for name in ('systemd/aoa-pl.service', 'systemd/aoa-v2.service',
                         'calibration/calibrate.py', 'calibration/calibration_math.py',
                         'calibration/delay_math.py', 'install_service.sh'):
                self.assertTrue((root / 'stage' / name).is_file(), name)
            for excluded in ('PC', 'PS', 'common/archive_receiver.py', 'training'):
                self.assertFalse((root / 'stage' / excluded).exists())

    def test_tar_has_explicit_unix_permissions_and_folder_contents(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.make_source(root)
            archive_path = root / 'release.tar.gz'
            with patch.object(tool, 'ROOT', root):
                tool.package(root / 'stage', allow_incomplete=True, tar_path=archive_path)
            with tarfile.open(archive_path, 'r:gz') as archive:
                members = {Path(item.name).as_posix(): item for item in archive.getmembers()}
                for item in members.values():
                    if item.isdir():
                        self.assertEqual(item.mode, 0o755, item.name)
                for name in ('start.sh', 'stop.sh', 'status.sh', 'python.sh',
                             'runtime/helper.sh', 'bin/icraft_aoa_ml3',
                             'python/bin/python3', 'lib/ld-linux-aarch64.so.1'):
                    self.assertEqual(members[name].mode, 0o755, name)
                for name in ('config/job.json', 'runtime/run_board.py',
                             'python/lib/python3.9/os.py', 'lib/libpython3.9.so.1.0'):
                    self.assertEqual(members[name].mode, 0o644, name)
                    self.assertEqual(archive.extractfile(members[name]).read(),
                                     (root / 'stage' / name).read_bytes())
                self.assertIn('.', members)
                self.assertNotIn('stage', members)

    def test_tar_existing_target_is_never_overwritten(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.make_source(root)
            archive_path = root / 'release.tar.gz'
            archive_path.write_bytes(b'keep original')
            with patch.object(tool, 'ROOT', root):
                with self.assertRaises(FileExistsError):
                    tool.package(root / 'stage', allow_incomplete=True, tar_path=archive_path)
            self.assertEqual(archive_path.read_bytes(), b'keep original')
            self.assertFalse((root / 'stage').exists())

    def test_tar_cannot_be_inside_output(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.make_source(root)
            output = root / 'stage'
            with patch.object(tool, 'ROOT', root):
                for archive_path in (output, output / 'release.tar.gz', output / 'nested/release.tar.gz'):
                    with self.subTest(path=archive_path), self.assertRaisesRegex(ValueError, 'inside'):
                        tool.package(output, allow_incomplete=True, tar_path=archive_path)
                    self.assertFalse(output.exists())

    def test_tar_cli_cannot_write_back_into_any_package_input(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.make_source(root)
            output = root / 'stage'
            before = {p.relative_to(root): p.read_bytes() for p in root.rglob('*') if p.is_file()}
            with patch.object(tool, 'ROOT', root):
                for source in ('Board', 'assets/board', 'common'):
                    for suffix in ('release.tar.gz', 'new-export/release.tar.gz'):
                        archive = root / source / suffix
                        with self.subTest(path=archive), self.assertRaisesRegex(ValueError, 'Tar archive.*input'):
                            tool.main(['--output', str(output), '--tar', str(archive), '--allow-incomplete'])
                        self.assertFalse(output.exists())
                        self.assertFalse(archive.exists())
            self.assertEqual(before, {p.relative_to(root): p.read_bytes()
                                      for p in root.rglob('*') if p.is_file()})

    def test_tar_requires_output(self):
        with patch('sys.stderr', new_callable=io.StringIO) as error:
            with self.assertRaises(SystemExit) as raised:
                tool.main(['--tar', 'release.tar.gz'])
        self.assertEqual(raised.exception.code, 2)
        self.assertIn('--tar requires --output', error.getvalue())


if __name__ == '__main__':
    unittest.main()

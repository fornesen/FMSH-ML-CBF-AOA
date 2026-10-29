"""Exercise relocated PC entry points from an unrelated working directory."""
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import unittest
import urllib.request

PC = Path(__file__).resolve().parents[2]


class PortablePathTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='AOA portable 中文 ')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.pc = self.root / '移动后的工程' / '01_PC'
        self.cwd = self.root / 'unrelated working directory'
        self.cwd.mkdir()
        for relative in (
            'settings.json', 'pc_config.py', 'start_pc.py',
            'start_adc_viewer.py', 'Console/start_archive.py',
            'adc_monitor/adc_monitor.py', 'adc_monitor/index.html',
        ):
            target = self.pc / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(PC / relative, target)
        self.env = {key: value for key, value in os.environ.items()
                    if not key.startswith('AOA_ARCHIVE_')}
        self.env.update(PYTHONDONTWRITEBYTECODE='1', PYTHONIOENCODING='utf-8')

    def run_copy(self, code, **environment):
        bootstrap = (
            'import sys,json; from pathlib import Path; '
            'pc=Path(sys.argv[1]); '
            'sys.path[:0]=[str(pc),str(pc/"Console"),str(pc/"adc_monitor")];\n'
        )
        result = subprocess.run(
            [sys.executable, '-B', '-c', bootstrap + code, str(self.pc)],
            cwd=self.cwd, env=dict(self.env, **environment),
            capture_output=True, text=True, encoding='utf-8', check=True, timeout=20,
        )
        return json.loads(result.stdout.splitlines()[-1])

    def test_defaults_follow_moved_pc_directory(self):
        result = self.run_copy(
            'import pc_config,start_archive,adc_monitor\n'
            'a=start_archive.parse_args([]); d=adc_monitor.parse_args([])\n'
            'print(json.dumps([str(a.output),str(start_archive.LOGS),str(d.save_dir)]))'
        )
        self.assertEqual(result, [str(self.pc / path) for path in (
            'data/hardware_7100/board', 'local_config/logs', 'data/adc_capture',
        )])
        self.assertEqual(list(self.cwd.iterdir()), [])

    def test_relative_cli_and_environment_use_pc_directory(self):
        result = self.run_copy(
            'import start_archive,adc_monitor\n'
            'env=start_archive.parse_args([])\n'
            'cli=start_archive.parse_args(["--output","custom archive"])\n'
            'adc=adc_monitor.parse_args(["--save-dir","capture data"])\n'
            'print(json.dumps([str(env.output),str(cli.output),str(adc.save_dir)]))',
            AOA_ARCHIVE_OUTPUT='../shared 保存',
        )
        self.assertEqual(result, [
            str((self.pc / '../shared 保存').resolve()),
            str(self.pc / 'custom archive'),
            str(self.pc / 'capture data'),
        ])

    def test_explicit_absolute_output_is_preserved(self):
        external = self.root / 'external archive'
        result = self.run_copy(
            'import start_archive,adc_monitor\n'
            'a=start_archive.parse_args([])\n'
            'print(json.dumps(str(a.output)))',
            AOA_ARCHIVE_OUTPUT=str(external),
        )
        self.assertEqual(result, str(external))

    def test_main_launcher_subscribes_without_token_or_7100_control(self):
        result = self.run_copy(
            'from unittest.mock import patch\n'
            'import start_pc\n'
            'with patch.object(start_pc.start_archive,"ensure_receiver") as receiver, patch.object(start_pc.subprocess,"call",return_value=0) as child:\n'
            '    assert start_pc.main(["--http-port","18888"])==0\n'
            '    a=receiver.call_args.args[0]; command=child.call_args.args[0]\n'
            '    assert a.bind=="0.0.0.0"\n'
            '    assert command[command.index("--board-host")+1]=="192.168.10.51"\n'
            '    assert "--tcp-port" not in command\n'
            '    assert not (pc/"local_config/receiver.token").exists()\n'
            '    print(json.dumps([str(a.output),str(child.call_args.kwargs["cwd"]),command[-2:]]))'
        )
        self.assertEqual(result, [str(self.pc / 'data/hardware_7100/board'),
                                  str(self.pc / 'Console'), ['--http-port', '18888']])

    def test_legacy_config_with_unavailable_pc_ip_uses_wildcard(self):
        config_file = self.pc / 'settings.json'
        config = json.loads(config_file.read_text(encoding='utf-8'))
        config.pop('listen_address', None)
        config['pc_ip'] = '203.0.113.77'
        config_file.write_text(json.dumps(config), encoding='utf-8')
        result = self.run_copy(
            'from unittest.mock import patch\n'
            'import start_pc\n'
            'with patch.object(start_pc.start_archive,"ensure_receiver") as receiver, patch.object(start_pc.subprocess,"call",return_value=0) as child:\n'
            '    start_pc.main([]); command=child.call_args.args[0]\n'
            '    print(json.dumps([receiver.call_args.args[0].bind,command[command.index("--board-host")+1]]))'
        )
        self.assertEqual(result, ['0.0.0.0', '192.168.10.51'])

    def test_main_launcher_respects_archive_environment(self):
        result = self.run_copy(
            'from unittest.mock import patch\n'
            'import start_pc\n'
            'with patch.object(start_pc.start_archive,"ensure_receiver") as receiver, patch.object(start_pc.subprocess,"call",return_value=0):\n'
            '    start_pc.main([]); a=receiver.call_args.args[0]\n'
            '    assert not hasattr(a,"token_file")\n'
            '    print(json.dumps([str(a.output),a.bind,a.port]))',
            AOA_ARCHIVE_OUTPUT='custom archive', AOA_ARCHIVE_TOKEN_FILE='custom token',
            AOA_ARCHIVE_BIND='127.0.0.1', AOA_ARCHIVE_PORT='17012',
        )
        self.assertEqual(result, [str(self.pc / 'custom archive'), '127.0.0.1', 17012])

    def test_adc_launcher_and_raw_save_use_moved_directory(self):
        result = self.run_copy(
            'from unittest.mock import patch\n'
            'import start_adc_viewer,adc_monitor\n'
            'with patch.object(start_adc_viewer.subprocess,"call",return_value=0) as child:\n'
            '    start_adc_viewer.main([]); command=child.call_args.args[0]\n'
            '    assert "--paused" in command\n'
            '    assert Path(command[command.index("--save-dir")+1])==pc/"data/adc_capture"\n'
            'capture=adc_monitor.Capture(adc_monitor.parse_args(["--paused"]))\n'
            'capture.raw=bytes(adc_monitor.FRAME_BYTES)\n'
            'saved=capture.save(); path=Path(saved["path"])\n'
            'assert path.read_bytes()==capture.raw and path.with_suffix(".json").exists()\n'
            'print(json.dumps([str(path.parent),len(path.read_bytes())]))'
        )
        self.assertEqual(result, [str(self.pc / 'data/adc_capture'), 196608])
        self.assertEqual(list(self.cwd.iterdir()), [])

    def test_paused_adc_http_starts_after_relocation(self):
        with socket.socket() as sock:
            sock.bind(('127.0.0.1', 0))
            port = sock.getsockname()[1]
        process = subprocess.Popen(
            [sys.executable, '-B', str(self.pc / 'adc_monitor/adc_monitor.py'),
             '--paused', '--http-port', str(port)],
            cwd=self.cwd, env=self.env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        try:
            deadline = time.monotonic() + 10
            while True:
                self.assertIsNone(process.poll(), 'relocated ADC server exited')
                try:
                    with urllib.request.urlopen(f'http://127.0.0.1:{port}/api/frame', timeout=.5) as response:
                        state = json.load(response)
                    break
                except OSError:
                    if time.monotonic() >= deadline:
                        self.fail('relocated ADC server did not become ready')
                    time.sleep(.05)
            self.assertEqual(state['status'], 'paused')
            self.assertFalse(state['acquisition_enabled'])
            with urllib.request.urlopen(f'http://127.0.0.1:{port}/', timeout=2) as response:
                self.assertEqual(response.read(), (self.pc / 'adc_monitor/index.html').read_bytes())
        finally:
            process.terminate()
            process.wait(timeout=5)


if __name__ == '__main__':
    unittest.main(verbosity=2)

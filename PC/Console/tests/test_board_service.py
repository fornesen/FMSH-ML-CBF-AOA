"""The boot entry point must supervise the manager, not just its ready marker."""
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch


PROJECT = Path(__file__).resolve().parents[3]
spec = importlib.util.spec_from_file_location('board_service', PROJECT / 'Board/runtime/service.py')
service = importlib.util.module_from_spec(spec)
spec.loader.exec_module(service)


class BoardServiceTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.root = Path(self.folder.name)
        for name in ('config', 'runs', 'logs'):
            (self.root / name).mkdir()
        (self.root / 'config/job.json').write_text('{}')
        self.child = Mock()
        self.child.wait.return_value = 7
        self.child.poll.return_value = None

    def tearDown(self):
        self.folder.cleanup()

    def invoke(self, action):
        def launch(*args, **kwargs):
            run = self.root / 'runs/moving_session_001'
            run.mkdir()
            (run / 'session.json').write_text(json.dumps(
                {'manager_pid': 123, 'state': 'waiting_for_source'}))
            return self.child

        with patch.object(service, 'ROOT', self.root), \
             patch.object(service, 'RUNS', self.root / 'runs'), \
             patch.object(service, 'MANAGER', self.root / 'runtime/run_board.py'), \
             patch.object(service.subprocess, 'Popen', side_effect=launch) as popen, \
             patch.object(Path, 'symlink_to'), patch.object(service.os, 'replace'), \
             patch.object(sys, 'argv', ['service.py', action]):
            result = service.main()
        return result, popen.call_args.kwargs

    def test_systemd_run_waits_after_ready_and_propagates_manager_failure(self):
        result, options = self.invoke('run')
        self.assertEqual(result, 7)
        self.child.wait.assert_called_once_with()
        self.assertFalse(options['start_new_session'])

    def test_manual_start_returns_when_ready_without_waiting_for_capture(self):
        result, options = self.invoke('start')
        self.assertEqual(result, 0)
        self.child.wait.assert_not_called()
        self.assertTrue(options['start_new_session'])

    def test_stop_waits_for_manager_exit_after_request(self):
        run = self.root / 'runs/moving_session_001'
        run.mkdir()
        with patch.object(service, 'current', return_value=(run, {'manager_pid': 123})), \
             patch.object(service, 'running', side_effect=[True, True, False]), \
             patch.object(service.time, 'sleep') as sleep, \
             patch.object(sys, 'argv', ['service.py', 'stop']):
            self.assertEqual(service.main(), 0)
        self.assertTrue((run / 'STOP').is_file())
        sleep.assert_called_once_with(0.2)

    def test_stop_reports_a_manager_that_does_not_exit(self):
        run = self.root / 'runs/moving_session_001'
        run.mkdir()
        with patch.object(service, 'current', return_value=(run, {'manager_pid': 123})), \
             patch.object(service, 'running', return_value=True), \
             patch.object(service.time, 'sleep') as sleep, \
             patch.object(sys, 'argv', ['service.py', 'stop']):
            with self.assertRaisesRegex(RuntimeError, 'Stop timed out'):
                service.main()
        self.assertTrue((run / 'STOP').is_file())
        self.assertEqual(sleep.call_count, 600)


if __name__ == '__main__':
    unittest.main()

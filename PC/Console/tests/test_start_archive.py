"""Receiver reuse requires the same project, output folder and listening address."""
import importlib.util
import argparse
import http.client
import errno
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

spec = importlib.util.spec_from_file_location('start_archive', Path(__file__).parents[1]/'start_archive.py')
launcher = importlib.util.module_from_spec(spec)
spec.loader.exec_module(launcher)


class ArchiveOwnershipTests(unittest.TestCase):
    def identity(self, output, port=7012, pid=12345):
        return {'application': 'fpga-aoa-archive',
                'root': str(launcher.RECEIVER.resolve().parents[1]),
                'output': str(output.resolve()), 'bind': '0.0.0.0',
                'port': port, 'pid': pid}

    def test_configuration_identity_requires_same_root_output_and_endpoint(self):
        output = Path('test output/board')
        identity = self.identity(output)
        self.assertTrue(launcher.matching_receiver(identity, output, '0.0.0.0', 7012))
        for name, value in [('root', str(output.resolve())),
                            ('output', str((output/'other').resolve())),
                            ('application', 'another-app'), ('bind', '127.0.0.1'),
                            ('port', 7013), ('pid', 0)]:
            with self.subTest(field=name):
                changed = dict(identity, **{name: value})
                self.assertFalse(launcher.matching_receiver(changed, output, '0.0.0.0', 7012))
        self.assertFalse(launcher.matching_receiver([], output, '0.0.0.0', 7012))

    def test_unavailable_bind_has_actionable_error(self):
        with patch.object(launcher.socket, 'socket') as socket_factory:
            socket_factory.return_value.__enter__.return_value.bind.side_effect = OSError(10049, 'address unavailable')
            with self.assertRaisesRegex(RuntimeError, 'listen_address=0.0.0.0'):
                launcher.check_bind_address('203.0.113.77')

    def test_available_port_uses_exclusive_bind_without_process_lookup(self):
        with patch.object(launcher.socket, 'socket') as factory, patch.object(
                launcher.socket, 'SO_EXCLUSIVEADDRUSE', 12345, create=True):
            self.assertTrue(launcher.port_available('0.0.0.0', 7012))
            probe=factory.return_value.__enter__.return_value
            probe.setsockopt.assert_called_once_with(socket.SOL_SOCKET,12345,1)
            probe.bind.assert_called_once_with(('0.0.0.0',7012))
            for code in (errno.EADDRINUSE,errno.EACCES):
                probe.bind.side_effect=OSError(code,'occupied or reserved')
                self.assertFalse(launcher.port_available('0.0.0.0',7012))
            probe.bind.side_effect=OSError(errno.EADDRNOTAVAIL,'not local')
            with self.assertRaises(OSError):launcher.port_available('203.0.113.77',7012)

    def test_free_port_check_does_not_query_or_launch_a_process(self):
        args=argparse.Namespace(bind='0.0.0.0',port=7012)
        with patch.object(launcher,'port_available',return_value=True),patch.object(
                launcher,'receiver_identity') as lookup,patch.object(launcher.subprocess,'Popen') as start:
            with self.assertRaisesRegex(RuntimeError,'not running'):
                launcher._ensure_port(args,Path('unused'),check=True)
            lookup.assert_not_called();start.assert_not_called()

    def test_occupied_port_identity_failure_is_explicit_in_strict_and_check_modes(self):
        args=argparse.Namespace(bind='0.0.0.0',port=7012)
        with patch.object(launcher,'port_available',return_value=False),patch.object(
                launcher,'receiver_identity',side_effect=OSError('owner unavailable')),patch.object(
                launcher.subprocess,'Popen') as start:
            for check in (False,True):
                with self.assertRaisesRegex(launcher.ArchivePortConflict,'7012.*another service'):
                    launcher._ensure_port(args,Path('unused'),check=check)
            start.assert_not_called()

    def test_auto_port_avoids_unknown_owner_and_confirms_only_created_receiver(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);args=argparse.Namespace(bind='0.0.0.0',port=7012,output=root/'data')
            child=Mock(pid=12345);child.poll.return_value=None
            def start(command,**kwargs):
                self.assertEqual(command[command.index('--port')+1],'7013')
                kwargs['stdout'].write(b'ARCHIVE_RECEIVER_READY test output\n');kwargs['stdout'].flush()
                return child
            def identity(host, port):
                if port == 7012:
                    raise OSError('unknown listener')
                return self.identity(args.output, port=port, pid=child.pid)
            with patch.object(launcher,'LOGS',root/'logs'),patch.object(
                    launcher,'port_available',side_effect=lambda bind,port:port==7013),patch.object(
                    launcher,'receiver_identity',side_effect=identity) as lookup,patch.object(
                    launcher.subprocess,'Popen',side_effect=start):
                self.assertEqual(launcher.ensure_receiver(args,auto_port=True),child.pid)
            self.assertEqual(args.port,7013)
            self.assertEqual(lookup.call_args_list[0].args,('127.0.0.1',7012))
            self.assertEqual(lookup.call_args_list[1].args,('127.0.0.1',7013))
            child.terminate.assert_not_called();child.wait.assert_not_called()

    def test_unready_child_cannot_reuse_another_listener(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);args=argparse.Namespace(bind='0.0.0.0',port=7012)
            child=Mock(pid=12345);child.poll.return_value=None
            with patch.object(launcher,'LOGS',root/'logs'),patch.object(
                    launcher,'port_available',return_value=True),patch.object(launcher,'receiver_identity') as lookup,patch.object(
                    launcher.subprocess,'Popen',return_value=child),patch.object(launcher.time,'sleep'):
                with self.assertRaisesRegex(RuntimeError,'startup not confirmed'):
                    launcher._ensure_port(args,root/'data',check=False)
            lookup.assert_not_called()
            child.terminate.assert_called_once();child.wait.assert_called_once()

    def test_ready_log_cannot_confirm_a_different_process(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            args = argparse.Namespace(bind='0.0.0.0', port=7012)
            child = Mock(pid=12345)
            child.poll.return_value = None
            def start(command, **kwargs):
                kwargs['stdout'].write(b'ARCHIVE_RECEIVER_READY output\n')
                kwargs['stdout'].flush()
                return child
            wrong_process = self.identity(root/'data', pid=child.pid + 1)
            with patch.object(launcher, 'LOGS', root/'logs'), patch.object(
                    launcher, 'port_available', return_value=True), patch.object(
                    launcher.subprocess, 'Popen', side_effect=start), patch.object(
                    launcher, 'receiver_identity', return_value=wrong_process):
                with self.assertRaisesRegex(RuntimeError, 'identity differs'):
                    launcher._ensure_port(args, root/'data', check=False)
            child.terminate.assert_called_once()
            child.wait.assert_called_once()

    def test_real_wildcard_receiver_start_and_reuse(self):
        # Exercise real binding/readiness and read-only identity without process inspection.
        with tempfile.TemporaryDirectory(prefix='AOA archive 启动 ') as folder:
            root = Path(folder)
            with socket.socket() as reservation:
                reservation.bind(('0.0.0.0', 0))
                port = reservation.getsockname()[1]
            args = argparse.Namespace(output=root/'data', bind='0.0.0.0', port=port)
            original_popen = subprocess.Popen
            children = []

            def start_process(command, *positional, **kwargs):
                child = original_popen(command, *positional, **kwargs)
                if isinstance(command, list) and str(launcher.RECEIVER) in command:
                    children.append(child)
                return child

            try:
                with patch.object(launcher, 'LOGS', root/'logs'), patch.object(
                        launcher.subprocess, 'Popen', side_effect=start_process):
                    pid = launcher.ensure_receiver(args)
                    self.assertEqual(launcher.ensure_receiver(args, check=True), pid)
                    identity = launcher.receiver_identity('127.0.0.1', port)
                    self.assertEqual(identity['pid'], pid)
                    self.assertEqual(identity['output'], str(args.output.resolve()))
                    self.assertFalse(list(args.output.iterdir()))  # Health checks do not create archives.
                self.assertEqual(len(children), 1)
                self.assertEqual(children[0].pid, pid)
                connection = http.client.HTTPConnection('127.0.0.1', port, timeout=3)
                try:
                    connection.request('POST', '/archive', body=b'',
                                       headers={'Authorization': 'Bearer wrong-token'})
                    response = connection.getresponse()
                    self.assertEqual(response.status, 503)  # Empty body, not an authentication failure.
                    response.read()
                finally:
                    connection.close()
            finally:
                # Only terminate receiver processes created by this test.
                for child in children:
                    if child.poll() is None:
                        child.terminate()
                    child.wait(timeout=5)


if __name__ == '__main__':
    unittest.main()

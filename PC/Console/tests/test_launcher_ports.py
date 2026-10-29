"""Multiple copied consoles may coexist without taking each other's receiver."""
import argparse
import importlib.util
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

PC=Path(__file__).parents[2]
sys.path.insert(0,str(PC))
spec=importlib.util.spec_from_file_location('field_start_pc',PC/'start_pc.py')
entry=importlib.util.module_from_spec(spec);spec.loader.exec_module(entry)
archive=entry.start_archive


class LauncherPortTests(unittest.TestCase):
    def test_actual_archive_port_is_registered_and_not_overridden(self):
        def choose(args,auto_port=False):
            self.assertTrue(auto_port)
            self.assertEqual(args.port,18012)
            args.port=18013
        with patch.object(archive,'ensure_receiver',side_effect=choose),patch.object(
                entry.subprocess,'call',return_value=0) as child:
            self.assertEqual(entry.main(['--archive-port','18012','--http-port','18888']),0)
        command=child.call_args.args[0]
        self.assertEqual(command.count('--archive-port'),1)
        self.assertEqual(command[command.index('--archive-port')+1],'18013')
        self.assertIn('--auto-http-port',command)
        self.assertEqual(command[-2:],['--http-port','18888'])

    def test_receiver_failure_does_not_block_passive_console(self):
        with patch.object(archive,'ensure_receiver',side_effect=RuntimeError('drive full')),patch.object(
                entry.subprocess,'call',return_value=0) as child:
            self.assertEqual(entry.main([]),0)
        command=child.call_args.args[0]
        self.assertEqual(command[command.index('--archive-port')+1],'0')

    def test_explicit_viewer_only_does_not_start_receiver(self):
        with patch.object(archive,'ensure_receiver') as receiver,patch.object(
                entry.subprocess,'call',return_value=0):
            self.assertEqual(entry.main(['--archive-port=0']),0)
        receiver.assert_not_called()

    def test_server_failure_only_cleans_this_attempt(self):
        with patch.object(archive,'ensure_receiver'),patch.object(
                entry.subprocess,'call',return_value=1),patch.object(archive,'stop_created_receiver') as cleanup:
            self.assertEqual(entry.main([]),1)
        cleanup.assert_called_once()

    def test_real_conflicting_receiver_is_kept_and_alternate_is_reused(self):
        with tempfile.TemporaryDirectory(prefix='AOA copied folder ') as directory:
            root=Path(directory)
            with socket.socket() as probe:
                probe.bind(('0.0.0.0',0));preferred=probe.getsockname()[1]
            if preferred>65500:self.skipTest('No consecutive port range')
            children=[]
            popen=subprocess.Popen
            def track(command,*args,**kwargs):
                child=popen(command,*args,**kwargs)
                if isinstance(command,list) and str(archive.RECEIVER) in command:children.append(child)
                return child
            try:
                with patch.object(archive,'LOGS',root/'logs'),patch.object(
                        archive.subprocess,'Popen',side_effect=track):
                    first=argparse.Namespace(bind='0.0.0.0',port=preferred,output=root/'old')
                    first_pid=archive.ensure_receiver(first)
                    second=argparse.Namespace(bind='0.0.0.0',port=preferred,output=root/'new')
                    second_pid=archive.ensure_receiver(second,auto_port=True)
                    self.assertNotEqual(first_pid,second_pid)
                    self.assertGreater(second.port,preferred)
                    self.assertIsNone(children[0].poll())
                    again=argparse.Namespace(bind='0.0.0.0',port=preferred,output=root/'new')
                    self.assertEqual(archive.ensure_receiver(again,auto_port=True),second_pid)
                    self.assertEqual(again.port,second.port)
                    self.assertEqual(len(children),2)
                    strict=argparse.Namespace(bind='0.0.0.0',port=preferred,output=root/'new')
                    with self.assertRaises(archive.ArchivePortConflict):archive.ensure_receiver(strict,check=True,auto_port=True)
                    self.assertEqual(len(children),2)
            finally:
                for child in children:
                    if child.poll() is None:child.terminate()
                    child.wait(timeout=5)


if __name__=='__main__':unittest.main()

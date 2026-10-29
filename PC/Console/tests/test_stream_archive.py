"""Current PC-first receiver contract, lost ACKs and final marker discovery."""
import json
import os
from pathlib import Path
import sys
import tempfile
import threading
import unittest
import urllib.request
from unittest.mock import patch
from http.server import HTTPServer

PROJECT=Path(__file__).resolve().parents[3]
sys.path[:0]=[str(PROJECT),str(PROJECT/'Board/runtime')]
from archive_delivery import PcFirstArchive
from common.archive_receiver import Receiver
from common.archive_io import verify_archive


class StreamArchiveTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name)
        for name in ('spool','pc'):(self.root/name).mkdir()
        self.server=HTTPServer(('127.0.0.1',0),Receiver);self.server.output=self.root/'pc'
        self.worker=threading.Thread(target=self.server.serve_forever,daemon=True);self.worker.start()
        self.url='http://127.0.0.1:%d/archive'%self.server.server_port

    def tearDown(self):
        self.server.shutdown();self.server.server_close();self.worker.join();self.temp.cleanup()

    def writer(self):
        writer=PcFirstArchive(self.root/'spool',self.root/'pending/aoa','aoa')
        writer.configure_pc_delivery(lambda:(self.url,),threading.Event())
        return writer

    def fixture(self):
        folder=self.root/'spool';raw=folder/'aoa_123_raw.bin';raw.write_bytes(os.urandom(4096))
        os.link(raw,folder/'aoa_123_link.bin')
        marker=folder/'aoa_123_metadata.json';marker.write_text('{"run_id":"123","processing_complete":true}')
        return marker

    def test_lost_ack_retries_idempotently_before_raw_cleanup(self):
        marker=self.fixture();writer=self.writer();urlopen=urllib.request.urlopen;attempts=[]
        def lose_first_ack(*args,**kwargs):
            response=urlopen(*args,**kwargs);attempts.append(1)
            if len(attempts)==1:
                response.read();response.close()
                self.assertTrue(marker.exists());self.assertTrue((self.root/'spool/aoa_123_raw.bin').exists())
                raise OSError('simulated lost PC ACK')
            return response
        with patch('archive_delivery.urllib.request.urlopen',side_effect=lose_first_ack):writer.archive(marker)
        self.assertEqual(len(attempts),2);self.assertEqual(list((self.root/'spool').iterdir()),[])
        self.assertEqual(list(writer.output.iterdir()),[])
        target=self.root/'pc/aoa/aoa_123.tar.gz'
        verify_archive(target,json.loads(target.with_name('aoa_123.archive.json').read_text()))

    def test_zero_compression_keeps_wire_and_member_integrity(self):
        writer=self.writer();writer.compresslevel=0;writer.archive(self.fixture())
        target=self.root/'pc/aoa/aoa_123.tar.gz'
        summary=json.loads(target.with_name('aoa_123.archive.json').read_text())
        self.assertEqual(summary['gzip_level'],0);verify_archive(target,summary)

    def test_pha4_and_per_round_iq_remain_in_aoa_archive(self):
        marker=self.fixture();folder=self.root/'spool'
        for name in ('aoa_123_pha4.raw','aoa_123_round1_pha.raw'):
            (folder/name).write_bytes(b'beamformed iq')
        writer=self.writer();writer.archive(marker)
        target=self.root/'pc/aoa/aoa_123.tar.gz'
        summary=json.loads(target.with_name('aoa_123.archive.json').read_text())
        names={member['name'] for member in summary['members']}
        self.assertTrue({'aoa_123_pha4.raw','aoa_123_round1_pha.raw'} <= names)
        verify_archive(target,summary)

    def test_final_drain_keeps_orphan_adc(self):
        folder=self.root/'spool';(folder/'aoa_123_1_round1_adc.raw').write_bytes(b'partial adc')
        writer=self.writer();writer.drain(partial=True);self.assertEqual(writer.committed,1)
        self.assertEqual(list(folder.iterdir()),[])


if __name__=='__main__':unittest.main()

"""PC-first delivery holds RAM originals until an exact PC save ACK."""
import json
from pathlib import Path
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
from http.server import HTTPServer

PROJECT=Path(__file__).resolve().parents[3]
sys.path[:0]=[str(PROJECT),str(PROJECT/'Board/runtime')]
from archive_delivery import ArchiveDelivery,PcFirstArchive
from common.archive_receiver import Receiver
from common.archive_io import verify_archive


class ArchiveDeliveryTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name)
        self.spool=self.root/'ram';self.spool.mkdir()
        self.output=self.root/'pending/aoa';self.pc=self.root/'pc';self.pc.mkdir()
        self.server=HTTPServer(('127.0.0.1',0),Receiver);self.server.output=self.pc
        self.worker=threading.Thread(target=self.server.serve_forever,daemon=True);self.worker.start()
        self.url='http://127.0.0.1:%d/archive'%self.server.server_port

    def tearDown(self):
        self.server.shutdown();self.server.server_close();self.worker.join();self.temp.cleanup()

    def writer(self,endpoints=None,cancel=None,enabled=lambda:True):
        writer=PcFirstArchive(self.spool,self.output,'aoa')
        writer.configure_pc_delivery(endpoints or (lambda:(self.url,)),cancel or threading.Event(),enabled)
        return writer

    def fixture(self):
        marker=self.spool/'aoa_123_metadata.json'
        marker.write_text('{"run_id":"123","processing_complete":true}')
        (self.spool/'aoa_123_adc.raw').write_bytes(bytes(range(256))*16)
        return marker

    def pending(self):
        marker=self.fixture();cancel=threading.Event();cancel.set()
        with self.assertRaisesRegex(RuntimeError,'PC did not acknowledge'):
            self.writer(cancel=cancel).archive(marker)
        manifest=self.output/'aoa_123.archive.json'
        verify_archive(manifest.with_name('aoa_123.tar.gz'),json.loads(manifest.read_text()))
        return marker,manifest

    def test_waiting_for_pc_keeps_metadata_backpressure_then_ack_releases_ram(self):
        marker=self.fixture();endpoints=[];cancel=threading.Event();errors=[]
        writer=self.writer(lambda:tuple(endpoints),cancel)
        def run():
            try:writer.archive(marker)
            except Exception as error:errors.append(error)
        worker=threading.Thread(target=run);worker.start()
        try:
            deadline=time.monotonic()+3
            while not (self.output/'aoa_123.archive.json').exists() and time.monotonic()<deadline:time.sleep(.01)
            self.assertTrue((self.output/'aoa_123.archive.json').exists())
            self.assertTrue(marker.exists());self.assertTrue((self.spool/'aoa_123_adc.raw').exists())
            self.assertTrue(worker.is_alive())
            endpoints.append(self.url);worker.join(timeout=5)
            self.assertFalse(worker.is_alive());self.assertEqual(errors,[])
            self.assertEqual(list(self.spool.iterdir()),[]);self.assertEqual(list(self.output.iterdir()),[])
            self.assertTrue((self.pc/'aoa/aoa_123.tar.gz').exists())
        finally:cancel.set();worker.join(timeout=5)

    def test_cancel_retains_all_unacknowledged_evidence(self):
        marker,manifest=self.pending()
        self.assertTrue(marker.exists());self.assertTrue((self.spool/'aoa_123_adc.raw').exists())
        self.assertEqual(ArchiveDelivery(self.output.parent).pending_manifests(),[manifest])
        self.assertEqual(list(self.pc.iterdir()),[])

    def test_receiver_rejection_keeps_originals_for_a_different_receiver(self):
        marker,manifest=self.pending();sender=ArchiveDelivery(self.output.parent)
        with self.assertRaises(OSError):sender.deliver(manifest,self.url+'/wrong')
        self.assertTrue(marker.exists());self.assertTrue(manifest.exists())
        self.assertTrue(manifest.with_name('aoa_123.tar.gz').exists())
        sender.deliver(manifest,self.url)
        self.assertFalse(manifest.exists());self.assertTrue(marker.exists())
        # Only archive() owns raw cleanup; a standalone upload cannot release it.
        target=self.pc/'aoa/aoa_123.tar.gz'
        verify_archive(target,json.loads(target.with_name('aoa_123.archive.json').read_text()))

    def test_same_length_corruption_is_rejected_before_network(self):
        marker,manifest=self.pending();archive=manifest.with_name('aoa_123.tar.gz')
        payload=bytearray(archive.read_bytes());payload[-1]^=1;archive.write_bytes(payload)
        with patch('archive_delivery.urllib.request.urlopen') as request:
            with self.assertRaisesRegex(ValueError,'checksum'):
                ArchiveDelivery(self.output.parent).deliver(manifest,self.url)
            request.assert_not_called()
        self.assertTrue(marker.exists());self.assertTrue(manifest.exists())

    def test_receiver_checks_members_before_ack(self):
        marker,manifest=self.pending();summary=json.loads(manifest.read_text())
        summary['members'][0]['sha256']='0'*64;manifest.write_text(json.dumps(summary))
        with self.assertRaises(OSError):ArchiveDelivery(self.output.parent).deliver(manifest,self.url)
        self.assertTrue(marker.exists());self.assertTrue(manifest.exists())

    def test_failed_receiver_does_not_prevent_trying_another_registered_pc(self):
        writer=self.writer(lambda:(self.url+'/wrong',self.url));writer.archive(self.fixture())
        self.assertEqual(writer.delivery_error,'')
        self.assertEqual(list(self.spool.iterdir()),[]);self.assertEqual(list(self.output.iterdir()),[])

    def test_empty_disabled_spool_never_compresses_or_uploads(self):
        writer=self.writer()
        with patch('archive_spool.tarfile.open') as compress,patch('archive_delivery.urllib.request.urlopen') as request:
            for _ in range(3):writer.drain()
            compress.assert_not_called();request.assert_not_called()
        self.assertEqual(list(self.spool.iterdir()),[]);self.assertEqual(list(self.output.iterdir()),[])

    def test_disabled_pending_batch_does_no_archive_work_then_resumes(self):
        marker=self.fixture();enabled=threading.Event();paused=threading.Event();cancel=threading.Event();errors=[]
        def is_enabled():
            if not enabled.is_set():paused.set()
            return enabled.is_set()
        writer=self.writer(cancel=cancel,enabled=is_enabled)
        def run():
            try:writer.archive(marker)
            except Exception as error:errors.append(error)
        worker=threading.Thread(target=run)
        try:
            with patch('archive_spool.digest',wraps=__import__('archive_spool').digest) as digest, \
                 patch('archive_spool.tarfile.open',wraps=__import__('tarfile').open) as compress, \
                 patch('archive_delivery.urllib.request.urlopen',wraps=__import__('urllib.request',fromlist=['urlopen']).urlopen) as request:
                worker.start();self.assertTrue(paused.wait(2))
                digest.assert_not_called();compress.assert_not_called();request.assert_not_called()
                self.assertTrue(marker.exists());self.assertTrue((self.spool/'aoa_123_adc.raw').exists())
                enabled.set();worker.join(timeout=5)
            self.assertFalse(worker.is_alive());self.assertEqual(errors,[])
            self.assertEqual(list(self.spool.iterdir()),[])
            self.assertTrue((self.pc/'aoa/aoa_123.tar.gz').exists())
        finally:cancel.set();worker.join(timeout=5)

    def test_off_during_hashing_pauses_before_packing(self):
        import archive_spool
        marker=self.fixture();enabled=threading.Event();enabled.set();paused=threading.Event();cancel=threading.Event();errors=[]
        def is_enabled():
            if not enabled.is_set():paused.set()
            return enabled.is_set()
        original_digest=archive_spool.digest
        def disable_after_hash(path):
            result=original_digest(path);enabled.clear();return result
        writer=self.writer(cancel=cancel,enabled=is_enabled)
        def run():
            try:writer.archive(marker)
            except Exception as error:errors.append(error)
        worker=threading.Thread(target=run)
        try:
            with patch('archive_spool.digest',side_effect=disable_after_hash), \
                 patch('archive_spool.tarfile.open') as compress, \
                 patch('archive_delivery.urllib.request.urlopen') as request:
                worker.start();self.assertTrue(paused.wait(2))
                compress.assert_not_called();request.assert_not_called()
                self.assertTrue(marker.exists())
                cancel.set();worker.join(timeout=3)
            self.assertFalse(worker.is_alive());self.assertEqual(len(errors),1)
            self.assertTrue((self.spool/'aoa_123_adc.raw').exists())
            self.assertEqual(list(self.pc.iterdir()),[])
        finally:cancel.set();worker.join(timeout=5)

    def test_off_after_inflight_request_prevents_retry_until_reenabled(self):
        import urllib.request
        marker=self.fixture();enabled=threading.Event();enabled.set();paused=threading.Event();cancel=threading.Event();errors=[];attempts=[]
        def is_enabled():
            if not enabled.is_set():paused.set()
            return enabled.is_set()
        writer=self.writer(cancel=cancel,enabled=is_enabled);urlopen=urllib.request.urlopen
        def lose_first_ack(*args,**kwargs):
            attempts.append(1)
            response=urlopen(*args,**kwargs)
            if len(attempts)==1:
                response.read();response.close();enabled.clear();raise OSError('lost in-flight ACK')
            return response
        def run():
            try:writer.archive(marker)
            except Exception as error:errors.append(error)
        worker=threading.Thread(target=run)
        try:
            with patch('archive_delivery.urllib.request.urlopen',side_effect=lose_first_ack):
                worker.start();self.assertTrue(paused.wait(3));time.sleep(.35)
                self.assertEqual(len(attempts),1);self.assertTrue(marker.exists())
                enabled.set();worker.join(timeout=5)
            self.assertFalse(worker.is_alive());self.assertEqual(errors,[]);self.assertEqual(len(attempts),2)
            self.assertFalse(marker.exists())
        finally:cancel.set();worker.join(timeout=5)

    def test_cancel_while_disabled_keeps_unpacked_originals(self):
        marker=self.fixture();cancel=threading.Event();cancel.set()
        with patch('archive_spool.digest') as digest,patch('archive_spool.tarfile.open') as compress, \
             patch('archive_delivery.urllib.request.urlopen') as request:
            with self.assertRaisesRegex(RuntimeError,'originals retained'):
                self.writer(cancel=cancel,enabled=lambda:False).archive(marker)
            digest.assert_not_called();compress.assert_not_called();request.assert_not_called()
        self.assertTrue(marker.exists());self.assertTrue((self.spool/'aoa_123_adc.raw').exists())
        self.assertEqual(list(self.output.iterdir()),[])


if __name__=='__main__':unittest.main()

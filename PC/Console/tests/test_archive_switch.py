"""Archive controls use the real board ACK and do not permit a disk fallback."""
import asyncio
import ast
import copy
from io import BytesIO
import json
from pathlib import Path
import sys
import os
import tempfile
import threading
import unittest
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
sys.path.insert(0,str(ROOT.parents[1]))
sys.path.insert(0,str(ROOT.parents[1]/'Board/runtime'))
import test_telemetry_hub as hub_tests
from telemetry_hub import encode
from protocol import validate
from server import State

# Compile the actual portable class without importing Linux-only load_pl/fcntl.
manager=ROOT.parents[1]/'Board/runtime/run_board.py'
node=next(n for n in ast.parse(manager.read_text(encoding='utf-8')).body
          if isinstance(n,ast.ClassDef) and n.name=='LogPump')
namespace={'threading':threading,'os':os}
exec(compile(ast.Module(body=[node],type_ignores=[]),str(manager),'exec'),namespace)
LogPump=namespace['LogPump']

node=next(n for n in ast.parse(manager.read_text(encoding='utf-8')).body
          if isinstance(n,ast.FunctionDef) and n.name=='archive_enabled')
namespace={'json':json}
exec(compile(ast.Module(body=[node],type_ignores=[]),str(manager),'exec'),namespace)
archive_enabled=namespace['archive_enabled']


class ArchiveFileTests(unittest.TestCase):
    def test_missing_control_defaults_off_and_follows_real_enabled_field(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'archive_control.json'
            self.assertFalse(archive_enabled(path))
            path.write_text('{"enabled":true}')
            self.assertTrue(archive_enabled(path))
            path.write_text('{"enabled":false}')
            self.assertFalse(archive_enabled(path))


class ArchiveHubTests(hub_tests.HubTests):
    async def test_multiple_viewers_only_change_archive_after_explicit_control(self):
        first, control_pc=await self.connection(self.hub.client_port)
        control_pc.write(encode(dict(schema=1,type='subscribe',client_id='control',archive_port=7012)))
        await control_pc.drain();await self.read(first)
        second, _=await self.subscriber()
        producer, writer=await self.connection(self.hub.ingress_port)
        archive={'available':True,'enabled':False,'command_id':'','command_status':'idle'}
        await self.publish(writer,self.packet(1,beam={'archive':archive}),[first,second])
        await self.no_frame(producer)
        command=self.command('debug-enable');command.update(mode='archive',archive_enabled=True)
        control_pc.write(encode(command));await control_pc.drain()
        self.assertEqual((await self.read(producer))[1],command)
        archive.update(enabled=True,command_id=command['id'],command_status='applied')
        await self.publish(writer,self.packet(2,beam={'archive':archive}),[first,second])
        self.assertIsNone(self.hub.pending)
        third, _=await self.subscriber()
        await self.no_frame(producer)
        archive.update(enabled=False,command_id='debug-off',command_status='applied')
        await self.publish(writer,self.packet(3,beam={'archive':archive}),[first,second,third])
        await self.no_frame(producer)

    async def test_enable_requires_registered_pc_but_off_does_not(self):
        reader,subscriber=await self.subscriber()
        producer,writer=await self.connection(self.hub.ingress_port)
        await self.publish(writer,self.packet(1),[reader])
        command=self.command('archive-on');command.update(mode='archive',archive_enabled=True)
        subscriber.write(encode(command));await subscriber.drain()
        reply=(await self.read(reader))[1]
        self.assertEqual(reply['status'],'rejected')
        self.assertIn('归档接收端',reply['error'])
        await self.no_frame(producer)
        command.update(id='archive-off',archive_enabled=False)
        subscriber.write(encode(command));await subscriber.drain()
        self.assertEqual((await self.read(producer))[1],command)
        await self.publish(writer,self.packet(2,beam={'archive':{'command_id':'archive-off','command_status':'applied'}}),[reader])
        self.assertIsNone(self.hub.pending)

    async def test_registered_enable_then_independent_archive_ack(self):
        reader,subscriber=await self.connection(self.hub.client_port)
        subscriber.write(encode(dict(schema=1,type='subscribe',client_id='archive-pc',archive_port=7012)))
        await subscriber.drain();await self.read(reader)
        producer,writer=await self.connection(self.hub.ingress_port)
        await self.publish(writer,self.packet(1),[reader])
        command=self.command('archive-on');command.update(mode='archive',archive_enabled=True)
        subscriber.write(encode(command));await subscriber.drain()
        self.assertEqual((await self.read(producer))[1],command)
        await self.publish(writer,self.packet(2,beam={'observation':{'command_id':'archive-on','command_status':'applied'}}),[reader])
        self.assertIsNotNone(self.hub.pending)
        await self.publish(writer,self.packet(3,beam={'archive':{'command_id':'archive-on','command_status':'applied'}}),[reader])
        self.assertIsNone(self.hub.pending)


class ArchiveStorageTests(unittest.TestCase):
    def test_idle_off_spool_does_not_compress_or_transmit(self):
        from archive_delivery import PcFirstArchive
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);spool=root/'spool';spool.mkdir()
            archive=PcFirstArchive(spool,root/'pending'/'aoa','aoa')
            archive.configure_pc_delivery(lambda: ('http://127.0.0.1:7012/archive',),threading.Event())
            with patch('archive_spool.tarfile.open') as tar, \
                 patch('archive_delivery.urllib.request.urlopen') as upload:
                for _ in range(4):archive.drain()
                tar.assert_not_called();upload.assert_not_called()
            self.assertEqual(list(spool.iterdir()),[])
            self.assertEqual(list((root/'pending'/'aoa').iterdir()),[])

    def test_manager_has_no_durable_fallback_or_startup_pc_wait(self):
        text=(ROOT.parents[1]/'Board/runtime/run_board.py').read_text(encoding='utf-8')
        self.assertNotIn('BoardArchive',text)
        self.assertNotIn('BoardNpuArchive',text)
        self.assertNotIn("root/'data'/'archive_pending'",text)
        self.assertNotIn('WAITING_FOR_PC_ARCHIVE_RECEIVER',text)
        self.assertNotIn("aoa.append('--record-raw')",text)
        self.assertIn("AOA_ARCHIVE_SWITCH='1'",text)

    def test_log_pump_is_bounded_and_not_archivable(self):
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder);failed=threading.Event()
            pump=LogPump('aoa',BytesIO(),path,failed)
            for _ in range(10):
                pump.buffer=bytearray(b'x'*(128*1024));pump.flush()
            files=list(path.iterdir())
            self.assertEqual(len(files),2)
            self.assertEqual(sum(f.stat().st_size for f in files),256*1024)
            self.assertFalse(list(path.glob('*metadata.json')))


class ArchiveControlTests(unittest.IsolatedAsyncioTestCase):
    class Writer:
        def __init__(self):self.frames=[]
        def is_closing(self):return False
        def write(self,value):self.frames.append(value)
        async def drain(self):pass

    def packet(self,seq=1):
        p=json.loads((ROOT/'tests/fixtures/v12/packet_position.json').read_text(encoding='utf-8'))
        p['seq']=seq
        p['beam']={'available':True,'mode':'equal','tracking':False,'status':'idle','error':'',
                   'pl_version':0x10001,'required_pl_version':0x10001,
                   'command_id':'','applied_run_id':'','angle_deg':0,
                   'command_status':'idle','command_error':'','registers_verified':False,
                   'hardware_response_verified':False,'phase_rad':[0]*6,'paths':[0,0],
                   'archive':{'available':True,'enabled':False,'command_id':'','command_status':'idle','error':''}}
        return p

    async def test_http_send_is_not_applied_without_board_archive_ack(self):
        state=State();packet=self.packet();validate(packet)
        state.ingest(packet,connection='board')
        writer=self.Writer();state.writers['board']=writer
        result=await state.send_control({'mode':'archive','archive_enabled':True})
        self.assertEqual(result['status'],'sent')
        self.assertFalse(state.beam['archive']['enabled'])
        sent=json.loads(writer.frames[0][8:])
        self.assertEqual(sent['mode'],'archive');self.assertTrue(sent['archive_enabled'])
        packet=self.packet(2)
        packet['beam']['archive'].update(enabled=True,command_id=sent['id'],command_status='applied')
        validate(packet);state.ingest(packet,connection='board')
        self.assertEqual(state.control['status'],'applied')
        self.assertTrue(state.beam['archive']['enabled'])

    async def test_new_connection_clears_old_archive_control_without_enabling(self):
        state=State();state.ingest(self.packet(),connection='old')
        writer=self.Writer();state.writers['old']=writer
        result=await state.send_control({'mode':'archive','archive_enabled':True})
        self.assertEqual(result['session'],state.sources['aoa']['session'])
        state.disconnected('old')
        self.assertEqual(state.control['status'],'unknown')
        state.ingest(self.packet(2),connection='new')
        self.assertIsNone(state.snapshot()['control'])
        self.assertFalse(state.beam['archive']['enabled'])
        self.assertEqual(len(writer.frames),1)

    async def test_new_session_on_same_connection_clears_old_archive_control(self):
        state=State();state.ingest(self.packet(),connection='board')
        writer=self.Writer();state.writers['board']=writer
        await state.send_control({'mode':'archive','archive_enabled':True})
        state.sources['aoa']['last_seen']-=7
        packet=self.packet();packet['session']='new-producer'
        state.ingest(packet,connection='board')
        self.assertIsNone(state.snapshot()['control'])
        self.assertFalse(state.beam['archive']['enabled'])
        self.assertEqual(len(writer.frames),1)

    async def test_restart_during_send_does_not_restore_old_control(self):
        state=State();state.ingest(self.packet(),connection='old')
        original_session=state.sources['aoa']['session']
        writer=self.Writer()
        async def restart():
            state.disconnected('old')
            packet=self.packet();packet['session']='new-producer'
            state.ingest(packet,connection='new')
        writer.drain=restart;state.writers['old']=writer
        result=await state.send_control({'mode':'archive','archive_enabled':True})
        self.assertEqual(result['session'],original_session)
        self.assertIsNone(state.control)
        self.assertFalse(state.beam['archive']['enabled'])
        self.assertEqual(len(writer.frames),1)

    async def test_archive_state_and_request_require_boolean(self):
        packet=self.packet()
        for key in ['available','enabled']:
            bad=copy.deepcopy(packet);bad['beam']['archive'][key]=1
            with self.assertRaises(ValueError):validate(bad)
        for invalid in [-1,True,1.5]:
            bad=copy.deepcopy(packet);bad['beam']['archive']['pending_batches']=invalid
            with self.assertRaises(ValueError):validate(bad)
        state=State();state.ingest(packet,connection='board')
        with self.assertRaises(ValueError):await state.send_control({'mode':'archive','archive_enabled':1})


if __name__=='__main__':unittest.main()

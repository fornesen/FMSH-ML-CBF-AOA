import asyncio
import copy
import json
import math
import os
import pathlib
import struct
import sys
import unittest

sys.path.insert(0,str(pathlib.Path(__file__).resolve().parents[1]))
from protocol import MAGIC,MAX_PACKET,candidates,best_candidate,decode,encode,validate,blob
from demo import packets
from server import State,receive_client


class ContractTests(unittest.TestCase):
    def setUp(self):
        self.a,self.n=packets()

    def test_auto_display_changes_with_batch_and_preserves_track_gaps(self):
        s=State()
        a=copy.deepcopy(self.a)
        for g in a['groups']:
            for p in g['paths']: p['relative_power_db']=0 if p['id']==0 else -20
        s.ingest(a)
        first=s.snapshot()['auto_candidate_id']
        self.assertEqual(first,best_candidate(candidates(a))['id'])
        a['run_id']='next-batch';a['seq']+=1
        for g in a['groups']:
            for p in g['paths']:p['relative_power_db']=0 if p['id']==1 else -20
        s.ingest(a)
        self.assertNotEqual(s.snapshot()['auto_candidate_id'],first)
        a['run_id']='invalid-batch';a['seq']+=1;a['groups'][0]['ambiguous']=True
        s.ingest(a)
        snapshot=s.snapshot()
        self.assertIsNone(snapshot['auto_candidate_id'])
        self.assertEqual([p['valid'] for p in snapshot['trajectory']],[True,True,False])
        # 同批心跳不增加位置点。
        s.ingest(dict(schema=1,source='aoa',session=a['session'],seq=a['seq']+1,type='heartbeat',run_id=''))
        self.assertEqual(len(s.snapshot()['trajectory']),3)

    def test_wire_roundtrip(self):
        for p in (self.a,self.n):
            frame=encode(p)
            self.assertEqual(frame[:4],MAGIC)
            self.assertEqual(struct.unpack('!I',frame[4:8])[0],len(frame)-8)
            self.assertEqual(decode(frame[8:]),p)

    def test_nonfinite_rejected(self):
        with self.assertRaises(ValueError): decode(b'{"value":NaN}')
        self.a['groups'][0]['paths'][0]['angle_deg']=float('inf')
        with self.assertRaises(ValueError):validate(self.a)

    def test_grid_corruption(self):
        self.a['groups'][0]['grid']['score_u8']='AAAA'
        with self.assertRaises(ValueError):validate(self.a)

    def test_signal_shape(self):
        self.n['signal']['rows']=65
        with self.assertRaises(ValueError):validate(self.n)

    def test_malformed_nested_values_and_huge_integer(self):
        for key,value in [('search',None),('groups',[1]),('transport',None)]:
            p=copy.deepcopy(self.a);p[key]=value
            with self.assertRaises(ValueError):validate(p)
        for key,value in [('classification',[]),('signal',[])]:
            p=copy.deepcopy(self.n);p[key]=value
            with self.assertRaises(ValueError):validate(p)
        self.a['rf_hz']=10**1000
        with self.assertRaises(ValueError):validate(self.a)
        self.n['signal']['wave_iq']=[[0,0]]
        with self.assertRaises(ValueError):validate(self.n)

    def test_duplicate_notice_preserves_result_but_failure_does_not(self):
        s=State();s.ingest(self.a);s.ingest(self.n)
        repeat=dict(self.n,status='already_consumed',seq=self.n['seq']+1)
        repeat.pop('classification');s.ingest(repeat)
        self.assertEqual(s.snapshot()['npu']['status'],'inferred')
        repeat.update(status='error',seq=repeat['seq']+1);s.ingest(repeat)
        self.assertEqual(s.snapshot()['npu']['status'],'error')

    def test_candidate_intersection_on_both_rays(self):
        cs=candidates(self.a)
        self.assertEqual(len(cs),4)
        for c in cs:
            self.assertIsNotNone(c['x'])
            for g in range(2):
                x,y=self.a['centres'][g]
                angle=self.a['groups'][g]['paths'][c['paths'][g]]['angle_deg']
                self.assertAlmostEqual(math.degrees(math.atan2(c['x']-x,c['y']-y)),angle)

    def test_parallel_behind_and_incomplete(self):
        for g in self.a['groups']:
            for p in g['paths']:p['angle_deg']=10
        self.assertTrue(all(c['status']=='near_parallel' and c['x'] is None for c in candidates(self.a)))
        for p in self.a['groups'][0]['paths']:p['angle_deg']=-30
        for p in self.a['groups'][1]['paths']:p['angle_deg']=30
        self.assertTrue(all(c['status']=='behind_station' for c in candidates(self.a)))
        self.a['complete']=False
        self.assertEqual(candidates(self.a),[])

    def test_energy_shares_use_power_not_amplitude(self):
        # 组内能量比 3:1 和 4:1，组合应为 60%,15%,20%,5%。
        for group, ratio in zip(self.a['groups'], (3, 4)):
            group['paths'][0]['relative_power_db'] = 0
            group['paths'][1]['relative_power_db'] = -10 * math.log10(ratio)
        scores = [c['energy_share'] for c in candidates(self.a)]
        for actual, expected in zip(scores, (.6, .15, .2, .05)):
            self.assertAlmostEqual(actual, expected)
        self.assertAlmostEqual(sum(scores), 1)

    def test_energy_equal_extreme_and_missing(self):
        for group in self.a['groups']:
            for path in group['paths']: path['relative_power_db'] = -100000
        self.assertEqual([c['energy_share'] for c in candidates(self.a)], [.25] * 4)
        self.a['groups'][0]['paths'][0]['relative_power_db'] = 0
        self.assertEqual([c['energy_share'] for c in candidates(self.a)], [.5, .5, 0, 0])
        self.a['groups'][1]['paths'][1]['relative_power_db'] = None
        self.assertTrue(all(c['energy_share'] is None for c in candidates(self.a)))

    def test_energy_not_renormalized_by_geometry_or_quality(self):
        baseline = [c['energy_share'] for c in candidates(self.a)]
        self.a['groups'][0]['paths'][0]['angle_deg'] = self.a['groups'][1]['paths'][0]['angle_deg']
        self.a['groups'][0]['paths'][1]['active'] = False
        after = candidates(self.a)
        self.assertEqual(after[0]['status'], 'near_parallel')
        self.assertEqual(after[2]['status'], 'weak_path')
        self.assertEqual([c['energy_share'] for c in after], baseline)

    def test_run_join_out_of_order(self):
        s=State();s.ingest(self.n);s.ingest(self.a)
        self.assertEqual(s.snapshot()['npu']['run_id'],self.a['run_id'])
        a=copy.deepcopy(self.a);a['run_id']='next';a['seq']+=1;s.ingest(a)
        self.assertIsNone(s.snapshot()['npu'])
        self.assertEqual(s.snapshot()['signal_npu']['run_id'],self.n['run_id'])

    def test_classification_history_without_preview(self):
        n=copy.deepcopy(self.n);n.pop('signal')
        s=State();s.ingest(self.a);s.ingest(n)
        a=copy.deepcopy(self.a);a['run_id']='next-locate';a['seq']+=1;s.ingest(a)
        self.assertIsNone(s.snapshot()['npu'])
        previous=s.snapshot()['signal_npu']
        self.assertEqual(previous['classification'],n['classification'])
        self.assertNotIn('signal',previous)

    def test_reject_duplicate_and_second_board(self):
        s=State();s.ingest(self.a)
        with self.assertRaises(ValueError):s.ingest(self.a)
        self.a['seq']+=1;self.a['session']='another-board'
        with self.assertRaises(ValueError):s.ingest(self.a)

    def test_cpp_packet_fixture(self):
        path=pathlib.Path(__file__).parent/'build'/'board-packets.json'
        if not path.exists():self.skipTest('compile board_contract.cpp first')
        a,n=json.loads(path.read_text())
        for p in (a,n):
            p.update(schema=1,source=p['type'],session='cpp',seq=1)
            validate(p)
        self.assertEqual([p['angle_deg'] for p in a['groups'][0]['paths']],[12,35])
        self.assertEqual([p['angle_deg'] for p in a['groups'][1]['paths']],[-28,-8])
        self.assertEqual(len(candidates(a)),4)
        spec=blob(n['signal']['spectrum_u8'],640)
        peak=max(range(640),key=lambda i:spec[i])
        self.assertLess(abs((-2e6+(peak+.5)*4e6/640)-200e3),10000)


class TcpTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.state=State();self.clients=set()
        self.server=await asyncio.start_server(lambda r,w:receive_client(r,w,self.state,self.clients),'127.0.0.1',0)
        self.port=self.server.sockets[0].getsockname()[1]

    async def asyncTearDown(self):
        self.server.close();await self.server.wait_closed()

    async def test_fragment_and_coalescing(self):
        _,writer=await asyncio.open_connection('127.0.0.1',self.port)
        a,_=packets();frame=encode(a)
        for i in range(0,41,3):writer.write(frame[i:i+3]);await writer.drain()
        writer.write(frame[42:])
        heartbeat=dict(schema=1,source='aoa',session=a['session'],seq=a['seq']+1,type='heartbeat',run_id='')
        writer.write(encode(heartbeat));await writer.drain();await asyncio.sleep(.1)
        self.assertEqual(self.state.snapshot()['aoa']['run_id'],a['run_id'])
        self.assertEqual(self.state.snapshot()['sources']['aoa']['seq'],a['seq']+1)
        writer.close();await writer.wait_closed();await asyncio.sleep(.05)
        self.assertFalse(self.state.snapshot()['sources']['aoa']['connected'])

    async def test_oversize_and_truncation(self):
        for raw in (MAGIC+struct.pack('!I',MAX_PACKET+1),MAGIC+struct.pack('!I',20)+b'abc'):
            _,writer=await asyncio.open_connection('127.0.0.1',self.port)
            writer.write(raw);await writer.drain();writer.close();await writer.wait_closed()
        await asyncio.sleep(.1)
        self.assertEqual(self.state.rejected,2)
        self.assertIsNone(self.state.aoa)

    async def test_actual_cpp_board_publishers(self):
        build=pathlib.Path(__file__).parent/'build'
        executable=build/'board_contract.exe'
        if not executable.exists():self.skipTest('Windows board contract executable not built')
        env=dict(os.environ,AOA_CONSOLE_HOST='127.0.0.1',AOA_CONSOLE_PORT=str(self.port))
        process=await asyncio.create_subprocess_exec(str(executable),env=env,stdout=asyncio.subprocess.PIPE,stderr=asyncio.subprocess.PIPE)
        stdout,stderr=await asyncio.wait_for(process.communicate(),15)
        self.assertEqual(process.returncode,0,(stdout+stderr).decode(errors='replace'))
        snapshot=self.state.snapshot()
        self.assertEqual(snapshot['aoa']['run_id'],'contract-001')
        self.assertEqual(snapshot['npu']['run_id'],'contract-001')
        self.assertEqual(len(snapshot['candidates']),4)
        self.assertEqual(snapshot['rejected'],0)
        evidence={key:snapshot[key] for key in ('candidates','received_bytes','sources','rejected')}
        evidence['matched_run_id']=snapshot['npu']['run_id']
        (build/'tcp-integration.json').write_text(json.dumps(evidence,indent=2),encoding='utf-8')


if __name__=='__main__':unittest.main(verbosity=2)

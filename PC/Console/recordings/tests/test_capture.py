import json
import hashlib
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT));sys.path.insert(0,str(ROOT.parents[1]/'Board/runtime'))
from recordings.capture import read_capture,replay,numeric_diff,export_npz
from archive_spool import ArchiveSpool

class RecordingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        fixture=os.environ.get('AOA_RECORDING_FIXTURE')
        if not fixture:raise RuntimeError('Set AOA_RECORDING_FIXTURE to the compiled PS test_raw_recording executable')
        cls.temp=tempfile.TemporaryDirectory();cls.root=Path(cls.temp.name)
        source=cls.root/'fixtures';subprocess.run([fixture,str(source)],check=True)
        cls.manifests={}
        for mode in ['angle6','position','position2','legacy','no_signal','headless']:
            spool=cls.root/(mode+'_spool');spool.mkdir()
            for path in source.glob(mode+'_*'):shutil.copyfile(path,spool/path.name)
            target=cls.root/mode/'aoa';writer=ArchiveSpool(spool,target,prefix='')
            writer.verbose=False;writer.archive(spool/(mode+'_metadata.json'),stem=mode)
            cls.manifests[mode]=target/(mode+'.archive.json')

    @classmethod
    def tearDownClass(cls):cls.temp.cleanup()

    def test_angle6_and_position_same_numeric_results(self):
        for mode,manifest in self.manifests.items():
            comparison=replay(manifest,self.root/(mode+'_replayed'))
            self.assertTrue(comparison['math_matches_recorded_result'],comparison['differences'])
            self.assertEqual(len(comparison['adc_input_checks']),4)
            self.assertTrue(all(r['byte_exact'] for r in comparison['adc_input_checks']))

    def test_same_length_corruption_is_detected(self):
        source=self.manifests['angle6'];folder=self.root/'corrupt';folder.mkdir()
        summary=json.loads(source.read_text());archive=source.parent/summary['archive']
        data=bytearray(archive.read_bytes());data[-1]^=1
        (folder/archive.name).write_bytes(data);manifest=folder/source.name;manifest.write_text(source.read_text())
        with self.assertRaises(ValueError):read_capture(manifest)

    def test_partial_batch_does_not_replay(self):
        manifest=self.manifests['position'];summary=json.loads(manifest.read_text());summary['partial']=True
        other=manifest.with_name('partial.archive.json');other.write_text(json.dumps(summary))
        with self.assertRaisesRegex(ValueError,'Partial batch'):replay(other,self.root/'partial_output')
        self.assertFalse((self.root/'partial_output').exists())

    def test_round_identity_and_missing_round_rejected_by_native(self):
        from common.archive_restore import restore
        manifest=self.manifests['angle6'];summary,capture=read_capture(manifest)
        folder=self.root/'mixed';restore(manifest,folder)
        capture['rounds'][2]['run_id']='some_other_batch'
        path=folder/'broken.json';path.write_text(json.dumps(capture))
        exe=ROOT/'recordings/bin/aoa_capture_replay.exe'
        outcome=subprocess.run([str(exe),str(path),str(folder/'output.json')],capture_output=True)
        self.assertNotEqual(outcome.returncode,0);self.assertFalse((folder/'output.json').exists())
        capture['rounds'].pop();path.write_text(json.dumps(capture))
        self.assertNotEqual(subprocess.run([str(exe),str(path),str(folder/'output2.json')],capture_output=True).returncode,0)

    def test_recorded_pl_version_contract_mismatch_is_rejected(self):
        from common.archive_restore import restore
        manifest=self.manifests['angle6'];summary,capture=read_capture(manifest)
        folder=self.root/'wrong_contract';restore(manifest,folder)
        capture['rounds'][0]['fft_preprocessing']='raw'
        source=folder/'wrong.json';source.write_text(json.dumps(capture))
        result=subprocess.run([str(ROOT/'recordings/bin/aoa_capture_replay.exe'),str(source),str(folder/'out.json')],capture_output=True)
        self.assertNotEqual(result.returncode,0)
        self.assertIn(b'contract disagree',result.stderr)

    def test_export_shapes_signs_and_original_startup_preserved(self):
        try:import numpy as np
        except ImportError:self.skipTest('Optional NumPy analysis export is not installed')
        from common.archive_restore import restore
        manifest=self.manifests['angle6'];summary,capture=read_capture(manifest)
        folder=self.root/'arrays';restore(manifest,folder);export_npz(capture,folder)
        arrays=np.load(folder/'signals.npz')
        self.assertEqual(arrays['adc_signed_codes'].shape,(4,6,16384))
        self.assertEqual(int(arrays['adc_signed_codes'].min()),-2048)
        self.assertEqual(int(arrays['adc_signed_codes'].max()),2047)
        self.assertEqual(arrays['pl_fft_unscaled_iq_s39'].shape,(4,6,4096,2))
        self.assertTrue(np.any(arrays['pl_fft_unscaled_iq_s39']<0))
        self.assertEqual(arrays['pl_pha_iq_s32'].shape,(4,4096,2))
        self.assertTrue(np.any(arrays['pl_pha_iq_s32'][:,:64]!=0))
        self.assertFalse(bool(arrays['rounds_continuous']))

    def test_numeric_difference_is_not_hidden(self):
        self.assertEqual(numeric_diff({'angle':57},{'angle':-73}),['/angle'])

    def test_unicode_and_spaces_in_replay_directory(self):
        result=replay(self.manifests['headless'],self.root/'回放 结果')
        self.assertTrue(result['math_matches_recorded_result'])

    def make_npu_archive(self,mode,wrong=False):
        manifest=self.manifests[mode];summary,capture=read_capture(manifest)
        from common.archive_restore import restore
        raw=self.root/(mode+'_npu_source');restore(manifest,raw)
        h=hashlib.sha256()
        for row in capture['rounds']:h.update((raw/row['files']['pha']['name']).read_bytes())
        spool=self.root/(mode+'_npu_spool');spool.mkdir()
        marker=spool/'result_npu_result.json'
        marker.write_text(json.dumps({'run_id':capture['run_id'],'processing_complete':True,
                                     'source_raw_sha256':'0'*64 if wrong else h.hexdigest(),'status':'inferred'}))
        archive=ArchiveSpool(spool,manifest.parent.parent/'npu',prefix='');archive.verbose=False
        archive.archive(marker,stem='result')

    def test_npu_result_matches_same_batch_and_pha(self):
        self.make_npu_archive('angle6')
        result=replay(self.manifests['angle6'],self.root/'npu_linked')
        self.assertEqual(result['npu_results'][0]['run_id'],'fixture_angle6')

    def test_npu_result_with_different_pha_is_rejected(self):
        self.make_npu_archive('position',wrong=True)
        with self.assertRaisesRegex(ValueError,'NPU consumed different PHA'):
            replay(self.manifests['position'],self.root/'npu_mixed')

if __name__=='__main__':unittest.main()

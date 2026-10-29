"""Production C++ packet fixtures: raw quality, experimental candidates and legacy compatibility."""
import copy
import json
from pathlib import Path
import sys
import unittest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from joint_protocol import validate_joint, automatic_path
from protocol import validate, encode, decode, candidates, best_candidate
from demo import packets


def packet(name):
    p=json.loads((Path(__file__).parent/'fixtures/cov4'/f'packet_{name}.json').read_text(encoding='utf-8'))
    p.update(schema=1,source='aoa',session='cov4-test',seq=1)
    return p


class FourFrameProtocolTests(unittest.TestCase):
    def test_position_candidates_respect_original_snapshot_quality(self):
        p=packets()[0]
        self.assertIsNotNone(best_candidate(candidates(p)))
        for g in p['groups']:
            g['raw_validation']=dict(available=True,passed=False,raw_residual_ratio=.5,
                                     latest_residual_ratio=.2,basis='original_snapshots_fixed_angles')
        validate(p)
        self.assertIsNone(best_candidate(candidates(p)))

    def test_production_packets_and_control(self):
        for name in ('gated','quality','fallback'):
            p=packet(name)
            validate_joint(p)
            validate(p)
            self.assertEqual(decode(encode(p)[8:]),p)
            self.assertEqual(automatic_path(p) is None,name=='fallback')

    def test_raw_gate_cannot_be_bypassed_by_fused_quality(self):
        for name in ('gated','quality'):
            p=packet(name);p['joint']['raw_validation']['latest_residual_ratio']=.2
            self.assertIsNone(automatic_path(p))
            p['joint']['raw_validation']['latest_residual_ratio']=.01
            p['joint']['raw_validation']['passed']=False
            self.assertIsNone(automatic_path(p))

    def test_invalid_snapshot_contract(self):
        for change in ({'direction_frames':3}, {'direction_rounds':[0,1,3,3]},
                       {'covariance_concentration':float('nan')}, {'direction_frames':True}):
            p=packet('quality');p.update(change)
            with self.assertRaises(ValueError):validate_joint(p)

    def test_invalid_quality_candidates(self):
        for key,value in [('path_index',7),('accepted',True),('selected_score',float('nan'))]:
            p=packet('fallback');p['joint']['selection'][key]=value
            with self.assertRaises(ValueError):validate_joint(p)
        p=packet('quality');p['joint']['selection']['models'][2]['raw_residual_ratio']=float('nan')
        with self.assertRaises(ValueError):validate_joint(p)

    def test_raw_validation_missing_or_nan_rejected(self):
        p=packet('gated');p['joint'].pop('raw_validation')
        with self.assertRaises(ValueError):validate_joint(p)
        p=packet('quality');p['joint']['selection']['models'][2]['raw_residual_ratio']=.2
        with self.assertRaises(ValueError):validate_joint(p)
        p=packet('gated');p['joint']['raw_validation']['raw_residual_ratio']=float('nan')
        with self.assertRaises(ValueError):validate_joint(p)

    def test_legacy_packet_without_new_fields(self):
        p=packet('gated');p['joint'].pop('raw_validation')
        for key in ('raw_validation','direction_frames','direction_rounds','direction_method','covariance_concentration'):
            p.pop(key)
        validate_joint(p)
        self.assertIsNotNone(automatic_path(p))


if __name__=='__main__':unittest.main(verbosity=2)

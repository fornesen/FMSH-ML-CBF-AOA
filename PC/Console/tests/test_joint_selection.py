import copy
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from joint_protocol import automatic_path, validate_selection


class JointSelectionTests(unittest.TestCase):
    def setUp(self):
        self.paths = [dict(id=0, angle_deg=-40, amplitude_rms=10, active=True, energy_share=.9),
                      dict(id=1, angle_deg=4, amplitude_rms=2, active=True, energy_share=.06),
                      dict(id=2, angle_deg=18, amplitude_rms=1, active=True, energy_share=.04)]
        self.selection = dict(method='single_dual_gated_triple', accepted=True, status='ok',
            path_index=2, single_angle_deg=10, range_deg=[0,20], half_width_deg=10,
            dual_angles_deg=[3,18], dual_amplitudes=[10,1], dual_tolerance_deg=3,
            amplitude_ratio=.1, residual_budget=.001, global_residual_ratio=.01,
            residual_gap_ratio=.0005, dual_distance_deg=0)

    def packet(self):
        return dict(processing_mode='angle6', complete=True, status='ok',
                    joint=dict(has_best=True, ambiguous=False, residual_ratio=.0105,
                               paths=self.paths, selection=self.selection))

    def test_passes_without_modification_and_uses_ps_choice(self):
        before=copy.deepcopy(self.selection)
        validate_selection(self.selection,self.paths,3,-80,80)
        self.assertEqual(self.selection,before)
        self.assertEqual(automatic_path(self.packet())['id'],2)

    def test_reject_has_no_fallback(self):
        self.selection.update(accepted=False,status='residual_gap',path_index=None)
        validate_selection(self.selection,self.paths,3,-80,80)
        self.assertIsNone(automatic_path(self.packet()))

    def test_tampered_decision_rejected(self):
        for change in (dict(path_index=0),dict(residual_gap_ratio=.001001),
                       dict(range_deg=[-80,80]),dict(dual_amplitudes=[10,.999]),
                       dict(dual_distance_deg=1),dict(accepted=False)):
            with self.subTest(change=change):
                s=dict(self.selection,**change)
                with self.assertRaises(ValueError):
                    validate_selection(s,self.paths,3,-80,80)

    def test_legacy_and_quality_gates_remain(self):
        packet=self.packet()
        packet['joint']['ambiguous']=True
        self.assertIsNone(automatic_path(packet))
        packet['joint']['ambiguous']=False
        packet['joint'].pop('selection')
        self.assertEqual(automatic_path(packet)['id'],0)

    def test_stricter_control_threshold_does_not_invalidate_packet(self):
        self.paths[2]['active']=False
        validate_selection(self.selection,self.paths,3,-80,80)
        self.assertIsNone(automatic_path(self.packet()))

    def test_candidate_below_selection_amplitude_threshold_rejected(self):
        self.paths[2]['amplitude_rms']=.999
        with self.assertRaises(ValueError):
            validate_selection(self.selection,self.paths,3,-80,80)

    def test_selection_acceptance_does_not_bypass_absolute_fit_quality(self):
        packet=self.packet()
        packet['joint']['residual_ratio']=.12916132081807274
        validate_selection(self.selection,self.paths,3,-80,80)
        self.assertIsNone(automatic_path(packet))
        packet['joint']['residual_ratio']=.1
        self.assertEqual(automatic_path(packet)['id'],2)

    def test_cascade_five_degree_inclusive_boundary(self):
        self.selection.update(method='single_dual_triple_cascade', stages=['ok'],
                              dual_tolerance_deg=5, dual_angles_deg=[3,13], dual_distance_deg=5)
        validate_selection(self.selection,self.paths,3,-80,80)
        self.selection['dual_angles_deg'][1]=12.999
        self.selection['dual_distance_deg']=5.001
        with self.assertRaises(ValueError):
            validate_selection(self.selection,self.paths,3,-80,80)

    def test_dual_fallback_does_not_require_triple_agreement(self):
        self.paths=self.paths[:2]
        self.selection.update(method='single_dual_triple_cascade', stages=['residual_gap','ok'],
                              dual_tolerance_deg=5, path_index=1, dual_distance_deg=None,
                              dual_angles_deg=[-60,60])
        validate_selection(self.selection,self.paths,2,-80,80)
        self.assertEqual(automatic_path(self.packet())['id'],1)
        self.selection['stages']=['ok','ok']
        with self.assertRaises(ValueError):
            validate_selection(self.selection,self.paths,2,-80,80)

    def test_single_fallback_and_invalid_zero_input(self):
        self.paths=[dict(id=0,angle_deg=10,amplitude_rms=1,active=True,energy_share=1)]
        self.selection.update(method='single_dual_triple_cascade',
                              stages=['residual_gap','no_constrained_candidate','ok'],
                              dual_tolerance_deg=5,path_index=0,dual_distance_deg=None,residual_gap_ratio=0)
        validate_selection(self.selection,self.paths,1,-80,80)
        self.assertEqual(automatic_path(self.packet())['id'],0)
        self.selection.update(accepted=False,status='no_reference',path_index=None,
                              stages=['no_reference']*3,dual_angles_deg=[],dual_amplitudes=[])
        validate_selection(self.selection,[],1,-80,80)
        self.assertIsNone(automatic_path(self.packet()))

    def test_lower_order_fallback_keeps_quality_gates(self):
        self.paths=self.paths[:2]
        self.selection.update(method='single_dual_triple_cascade',stages=['residual_gap','ok'],
                              dual_tolerance_deg=5,path_index=1,dual_distance_deg=None)
        packet=self.packet()
        packet['joint']['ambiguous']=True
        self.assertIsNone(automatic_path(packet))
        packet['joint']['ambiguous']=False
        packet['joint']['residual_ratio']=.10001
        self.assertIsNone(automatic_path(packet))


if __name__ == '__main__':
    unittest.main()

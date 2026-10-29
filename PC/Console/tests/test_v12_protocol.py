"""V1.2 board-result compatibility; PC must not recompute ML or choose a point."""
import copy
import json
import pathlib
import struct
import sys
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from demo import packets
from joint_protocol import automatic_path, eligible_paths
from protocol import MAGIC, MAX_PACKET, automatic_candidate, candidates, decode, encode, validate
from server import State

FIXTURES = pathlib.Path(__file__).parent / 'fixtures' / 'v12'


def packet(name='position'):
    return json.loads((FIXTURES / ('packet_'+name+'.json')).read_text(encoding='utf-8'))


def energy_packet():
    p = packet('position_no_reference')
    loc = p['v12']['localization']
    loc.update(selection_method='triad_ml_energy', selected_id='C1')
    for c, share in zip(loc['candidates'], (.42, .28, .18, .12)):
        c['energy_share'] = share
    return p


class V12ProtocolTests(unittest.TestCase):
    def test_source_emitted_packets_and_original_framing(self):
        for name in ('position', 'angle6', 'scan6', 'k1', 'invalid', 'position_no_reference',
                     'position_ambiguous', 'position_raw_bad'):
            with self.subTest(name=name):
                p = packet(name)
                validate(p)
                framed = encode(p)
                self.assertEqual(framed[:4], MAGIC)
                self.assertEqual(struct.unpack('!I', framed[4:8])[0], len(framed)-8)
                self.assertLessEqual(len(framed)-8, MAX_PACKET)
                self.assertEqual(decode(framed[8:]), p)

    def test_board_candidates_forwarded_without_pc_geometry_or_energy_ranking(self):
        p = packet()
        board = copy.deepcopy(p['v12']['localization'])
        # Deliberately inconsistent legacy angles/energies cannot alter the
        # authoritative V1.2 candidate coordinates or selected ID on the PC.
        for g in p['groups']:
            for path in g['paths']:
                path.update(angle_deg=0, relative_power_db=0)
        self.assertEqual(candidates(p), board['candidates'])
        self.assertEqual(automatic_candidate(p)['id'], board['selected_id'])
        self.assertEqual(automatic_candidate(p)['x'],
                         next(c['x'] for c in board['candidates'] if c['id'] == board['selected_id']))
        state = State()
        state.ingest(p)
        snap = state.snapshot()
        self.assertEqual(snap['auto_candidate_id'], board['selected_id'])
        self.assertEqual(snap['candidates'], board['candidates'])
        self.assertEqual(snap['trajectory'][-1]['candidate_id'], board['selected_id'])

    def test_no_selected_id_does_not_fall_back_to_another_candidate(self):
        p = packet()
        p['v12']['localization']['selected_id'] = None
        self.assertIsNone(automatic_candidate(p))
        state = State()
        state.ingest(p)
        self.assertIsNone(state.snapshot()['auto_candidate_id'])
        self.assertFalse(state.snapshot()['trajectory'][-1]['valid'])

    def test_direction_residual_scores_are_forwarded_without_pc_selection(self):
        p = packet()
        loc = p['v12']['localization']
        loc.update(selection_method='direction_residual_angle', residual_weight=.5, angle_sigma_deg=30)
        for c in loc['candidates']:
            c.update(direction_residuals=[.1, .2], direction_residual_ratio=.15,
                     angle_cost=.04, selection_cost=.095)
        # Even inconsistent diagnostic costs cannot make the PC select a
        # different point: it forwards the authoritative board selected_id.
        selected = loc['selected_id']
        next(c for c in loc['candidates'] if c['id'] == selected)['selection_cost'] = .9
        before = copy.deepcopy(loc)
        validate(p)
        self.assertEqual(decode(encode(p)[8:]), p)
        self.assertEqual(candidates(p), before['candidates'])
        self.assertEqual(automatic_candidate(p)['id'], selected)
        state = State()
        state.ingest(p)
        self.assertEqual(state.snapshot()['candidates'], before['candidates'])
        self.assertEqual(state.snapshot()['auto_candidate_id'], selected)
        self.assertEqual(loc, before)
        loc['selected_id'] = None
        self.assertIsNone(automatic_candidate(p), 'scores must not supply a PC fallback')

    def test_nullable_direction_scores_and_malformed_fields(self):
        p = packet()
        loc = p['v12']['localization']
        loc.update(selection_method='direction_residual_angle', residual_weight=.5, angle_sigma_deg=30)
        for c in loc['candidates']:
            c.update(direction_residuals=[None, None], direction_residual_ratio=None,
                     angle_cost=None, selection_cost=None)
        validate(p)
        changes = [
            lambda loc: loc.update(residual_weight=-.1),
            lambda loc: loc.update(residual_weight=True),
            lambda loc: loc.update(angle_sigma_deg=0),
            lambda loc: loc.update(angle_sigma_deg=None),
            lambda loc: loc.update(selection_method='pc_guess'),
            lambda loc: loc['candidates'][0].update(direction_residual_ratio=float('nan')),
            lambda loc: loc['candidates'][0].update(selection_cost=1.1),
            lambda loc: loc['candidates'][0].update(angle_cost=-.1),
            lambda loc: loc['candidates'][0].update(direction_residuals=[.1]),
            lambda loc: loc['candidates'][0].update(direction_residuals=[.1, 'bad']),
        ]
        for index, change in enumerate(changes):
            with self.subTest(index=index):
                bad = copy.deepcopy(p)
                change(bad['v12']['localization'])
                with self.assertRaises(ValueError):
                    validate(bad)

    def test_geometry_stays_visible_without_a_joint_reference(self):
        p = packet('position_no_reference')
        validate(p)
        self.assertEqual(len(candidates(p)), 4)
        self.assertTrue(any(c['valid'] for c in candidates(p)))
        self.assertIsNone(automatic_candidate(p))
        self.assertTrue(all(c['angle_error_deg'] is None for c in candidates(p)))

    def test_triad_ml_energy_position_is_independent_of_full_array_joint(self):
        p = energy_packet()
        loc = p['v12']['localization']
        self.assertFalse(p['v12']['joint']['valid'])
        self.assertIsNone(loc['reference_angle_deg'])
        self.assertTrue(all(c['angle_error_deg'] is None for c in loc['candidates']))
        validate(p)
        self.assertEqual(decode(encode(p)[8:]), p)
        state = State()
        state.ingest(p)
        snap = state.snapshot()
        self.assertEqual(snap['auto_candidate_id'], 'C1')
        self.assertTrue(snap['trajectory'][-1]['valid'])
        self.assertEqual(snap['trajectory'][-1]['energy_share'], .42)
        # Legacy group displays and joint fields cannot change the board fix.
        for g in p['groups']:
            for path in g['paths']:
                path.update(angle_deg=0, relative_power_db=0)
        validate(p)
        self.assertEqual(automatic_candidate(p)['id'], 'C1')
        loc['selected_id'] = None
        validate(p)
        self.assertIsNone(automatic_candidate(p), 'new scores do not supply a PC fallback')

    def test_triad_ml_energy_excludes_invalid_geometry_and_keeps_raw_shares(self):
        p = energy_packet()
        loc = p['v12']['localization']
        loc['candidates'][0].update(valid=False, status='near_parallel')
        loc['selected_id'] = 'C2'
        validate(p)
        self.assertEqual(automatic_candidate(p)['id'], 'C2')
        self.assertEqual(sum(c['energy_share'] for c in candidates(p)), 1)
        self.assertEqual(candidates(p)[0]['energy_share'], .42)
        loc['selected_id'] = 'C1'
        with self.assertRaises(ValueError):
            validate(p)

    def test_triad_ml_energy_selection_and_ties_follow_declared_board_rule(self):
        p = energy_packet()
        loc = p['v12']['localization']
        loc['selected_id'] = 'C3'
        with self.assertRaises(ValueError):
            validate(p)
        for c in loc['candidates']:
            c['energy_share'] = .25
        loc['candidates'].reverse()
        loc['selected_id'] = 'C1'
        validate(p)
        self.assertEqual(automatic_candidate(p)['id'], 'C1')
        loc['selected_id'] = 'C4'
        with self.assertRaises(ValueError):
            validate(p)

    def test_triad_ml_energy_unknown_power_is_not_a_valid_fix(self):
        p = energy_packet()
        loc = p['v12']['localization']
        loc['selected_id'] = None
        for c in loc['candidates']:
            c.update(valid=False, status='unknown_power', energy_share=None)
        validate(p)
        self.assertIsNone(automatic_candidate(p))
        loc['candidates'][0]['valid'] = True
        with self.assertRaises(ValueError):
            validate(p)

    def test_triad_ml_energy_raw_validation_belongs_to_both_triads(self):
        p = energy_packet()
        p['raw_validation'] = True
        raw = dict(available=True, basis='original_snapshots_fixed_angles', passed=True,
                   raw_residual_ratio=.01, latest_residual_ratio=.02)
        p['joint'].pop('raw_validation', None)
        for g in p['groups']:
            g['raw_validation'] = dict(raw)
        validate(p)
        for index in (0, 1):
            bad = copy.deepcopy(p)
            bad['groups'][index].pop('raw_validation')
            with self.assertRaisesRegex(ValueError, 'missing triad raw snapshot validation'):
                validate(bad)
        bad = copy.deepcopy(p)
        bad['groups'][0]['raw_validation']['raw_residual_ratio'] = float('nan')
        with self.assertRaises(ValueError):
            validate(bad)
        # The special case cannot relax the existing ANGLE or weighted contract.
        for name in ('angle6', 'position'):
            old = packet(name)
            old['raw_validation'] = True
            old['joint'].pop('raw_validation', None)
            with self.assertRaisesRegex(ValueError, 'missing raw snapshot validation'):
                validate(old)

    def test_triad_ml_energy_cannot_be_used_by_angle_or_scan_mode(self):
        for mode in ('angle6', 'scan6', None):
            with self.subTest(mode=mode):
                p = energy_packet()
                p['processing_mode'] = mode
                with self.assertRaisesRegex(ValueError, 'triad energy requires position mode'):
                    validate(p)

    def test_triad_ml_energy_rejects_invalid_shares(self):
        changes = [
            lambda loc: loc['candidates'][0].pop('energy_share'),
            lambda loc: loc['candidates'][0].update(energy_share=None),
            lambda loc: loc['candidates'][0].update(energy_share=True),
            lambda loc: loc['candidates'][0].update(energy_share=float('nan')),
            lambda loc: loc['candidates'][0].update(energy_share=-.1),
            lambda loc: loc['candidates'][0].update(energy_share=1.1),
            lambda loc: loc['candidates'][0].update(energy_share=.3),
            lambda loc: loc['candidates'][0].update(valid=False, energy_share=float('inf')),
        ]
        for i, change in enumerate(changes):
            with self.subTest(index=i):
                p = energy_packet()
                change(p['v12']['localization'])
                with self.assertRaises(ValueError):
                    validate(p)

    def test_quality_diagnostic_points_cannot_become_final_positions(self):
        for name in ('position_ambiguous', 'position_raw_bad'):
            with self.subTest(name=name):
                p = packet(name)
                validate(p)
                cs = candidates(p)
                self.assertEqual(len(cs), 4)
                self.assertTrue(any(c['x'] is not None and c['y'] is not None for c in cs))
                self.assertFalse(any(c['valid'] for c in cs))
                self.assertIsNone(automatic_candidate(p))
                state = State()
                state.ingest(p)
                self.assertIsNone(state.snapshot()['auto_candidate_id'])
                self.assertFalse(state.snapshot()['trajectory'][-1]['valid'])

    def test_v12_direction_has_no_legacy_residual_amplitude_or_ambiguity_gate(self):
        p = packet('angle6')
        p['joint'].update(ambiguous=True, residual_ratio=.5)
        p['v12']['joint']['residual_ratio'] = .5
        for path in p['joint']['paths']:
            path['active'] = False
        validate(p)
        selected = automatic_path(p)
        self.assertIsNotNone(selected)
        self.assertEqual(selected['id'], p['v12']['joint']['path_index'])
        self.assertEqual([v['id'] for v in eligible_paths(p)], [selected['id']])

    def test_single_path_secondary_may_be_absent_or_null_diagnostics(self):
        p = packet('k1')
        validate(p)
        p['v12']['joint'].pop('secondary')
        validate(p)
        p = packet('invalid')
        validate(p)
        self.assertIsNone(p['v12']['joint']['angle_deg'])

    def test_position_may_omit_hidden_full_array_display_payload(self):
        p = packet()
        p['joint'].pop('score_grid', None)
        p['v12']['scan'].update(angles_deg=[], relative_db=[])
        validate(p)
        self.assertEqual(automatic_candidate(p)['id'], p['v12']['localization']['selected_id'])
        angle = packet('angle6')
        angle['v12']['scan'].update(angles_deg=[], relative_db=[])
        with self.assertRaises(ValueError):
            validate(angle)

    def test_rejects_malformed_optional_extension(self):
        changes = [
            lambda v: v.update(version='1.3'),
            lambda v: v['joint'].update(path_index=True),
            lambda v: v['joint'].update(path_index=8),
            lambda v: v['joint'].update(angle_deg=89),
            lambda v: v['joint'].update(pool_counts=[30, 31, 30]),
            lambda v: v['joint'].update(top30_rank=0),
            lambda v: v['joint'].update(pool_counts=[0, 0, 0]),
            lambda v: v['joint']['secondary'].update(display_only=False),
            lambda v: v['scan'].update(relative_db=[-1]),
            lambda v: v['scan'].update(angles_deg=[0], relative_db=[float('nan')]),
            lambda v: v['localization'].update(selected_id='C5'),
            lambda v: next(c for c in v['localization']['candidates']
                           if c['id'] == v['localization']['selected_id']).update(valid=False),
            lambda v: next(c for c in v['localization']['candidates']
                           if c['id'] == v['localization']['selected_id']).update(x=None),
        ]
        for change in changes:
            with self.subTest(change=changes.index(change)):
                p = packet()
                change(p['v12'])
                with self.assertRaises(ValueError):
                    validate(p)

    def test_single_path_cannot_claim_a_remaining_ml_path(self):
        p = packet('k1')
        p['v12']['joint']['secondary']['ml_angle_deg'] = 0
        with self.assertRaises(ValueError):
            validate(p)

    def test_legacy_packets_keep_the_old_contract(self):
        aoa, npu = packets()
        validate(aoa)
        validate(npu)
        self.assertEqual(len(candidates(aoa)), 4)
        self.assertIsNotNone(automatic_candidate(aoa))


if __name__ == '__main__':
    unittest.main()

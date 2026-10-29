"""Dual-tone regression tests with independent RF/mixer/IF signal models."""
import cmath
import copy
import hashlib
import json
import math
from pathlib import Path
import random
import tempfile
import unittest
from unittest.mock import patch

import calibrate
import delay_math as core
from test_calibration import GEOMETRY, SETTINGS, encode_slots, valid_metadata

RF_DELAYS_NS = [0, 1.5, -2.0, 4.0, 7.0, -6.0]
IF_DELAYS_NS = [0, .5, 1.0, -1.0, 2.0, 3.0]
IF_PHASE_DEG = [0, 3, -4, 8, -6, 5]


def synthetic(if_phase=False, delay_shift=0.0):
    """Real-mixer positive IF has opposite input phase below the fixed LO."""
    rng = random.Random(20261025)
    session = {'format_version': 2, 'settings': dict(SETTINGS), 'geometry': GEOMETRY,
               'old_correction': [[x, 0] for x in [1, .8, 1.1, .9, 1.2, 1.3]],
               'frequencies_hz': [425e6, 445e6], 'lo_hz': 433e6, 'points': []}
    for name, xyz in zip('LOR', [[GEOMETRY[0][0] - .75, 0, 0], [0, 0, 0], [GEOMETRY[5][0] + .75, 0, 0]]):
        point = {'name': name, 'xyz_m': xyz, 'tones': []}
        distances = [math.dist(xyz, p) for p in GEOMETRY]
        for rf in session['frequencies_hz']:
            settings = core.tone_settings(SETTINGS, rf, 433e6)
            sign = 1 if rf > 433e6 else -1
            tone = {'settings': settings, 'frames': []}
            for batch in range(4):
                for ri in range(4):
                    source = rng.uniform(-math.pi, math.pi)
                    common = rng.uniform(.5, 2)
                    values = []
                    for ch, distance in enumerate(distances):
                        rf_delay = RF_DELAYS_NS[ch] + (delay_shift if ch == 5 else 0)
                        incident = source - 2 * math.pi * rf * (distance / 299792458 + rf_delay * 1e-9)
                        # Different LO phase per channel, common across RF settings.
                        local_lo = ch * .21
                        electronics = -2 * math.pi * abs(rf - 433e6) * IF_DELAYS_NS[ch] * 1e-9
                        if if_phase:
                            electronics += math.radians(IF_PHASE_DEG[ch])
                        phase = sign * (incident - local_lo) + electronics
                        # Strong location-dependent amplitudes must not bias phase calibration.
                        amplitude = common * (1 + ch * .15) / distance
                        value = cmath.rect(amplitude, phase + rng.gauss(0, .0001))
                        values.append([value.real, value.imag])
                    bb = abs(rf - 433e6) - SETTINGS['dds_hz']
                    tone['frames'].append({'batch': batch, 'round': ri,
                                          'bin': round(bb / (25e6 / 4096)) % 4096,
                                          'values': values, 'peak_to_local_floor_db': [45.] * 6,
                                          'tone_band_fraction': [.999] * 6})
            point['tones'].append(tone)
        session['points'].append(point)
    return session


class DelayTests(unittest.TestCase):
    def test_mixer_conjugation_geometry_and_delay_recovery(self):
        report = core.solve_delay(synthetic())
        self.assertTrue(report['accepted'], report['reasons'])
        for actual, a, b in zip(report['delay_principal_ns'], RF_DELAYS_NS, IF_DELAYS_NS):
            self.assertAlmostEqual(actual, a + b, delta=.002)
        self.assertEqual(report['delay_alias_period_ns'], 50.)
        self.assertFalse(report['delay_unwrapped'])
        self.assertFalse(report['physical_delay_identified'])
        self.assertFalse(report['delay_used_for_runtime_compensation'])

    def test_unknown_if_phase_is_reported_as_effective_not_physical_delay(self):
        report = core.solve_delay(synthetic(if_phase=True))
        self.assertTrue(report['accepted'], report['reasons'])
        for ch, actual in enumerate(report['delay_principal_ns']):
            expected = RF_DELAYS_NS[ch] + IF_DELAYS_NS[ch] - 2 * IF_PHASE_DEG[ch] / 360 / 20e6 * 1e9
            self.assertAlmostEqual(actual, expected, delta=.002)
        self.assertFalse(report['physical_delay_identified'])

    def test_delay_ambiguity_not_silently_unwrapped(self):
        a = core.solve_delay(synthetic())
        b = core.solve_delay(synthetic(delay_shift=50.0))
        self.assertTrue(b['accepted'], b['reasons'])
        self.assertAlmostEqual(a['delay_principal_ns'][5], b['delay_principal_ns'][5], places=6)
        self.assertFalse(b['delay_unwrapped'])
        # Phase at the operating tone still changes; never substitute tau for phase.
        self.assertGreater(abs(complex(*a['correction'][5]) - complex(*b['correction'][5])), 1)

    def test_target_phase_is_direct_measured_and_amplitude_preserved(self):
        session = synthetic(if_phase=True)
        report = core.solve_delay(session)
        corrections = [complex(*v) for v in report['correction']]
        for ch, correction in enumerate(corrections):
            self.assertAlmostEqual(abs(correction), abs(complex(*session['old_correction'][ch])), places=12)
        # Independently remove known geometric phase from an unused frame.
        for point in session['points']:
            values = [complex(*v) for v in point['tones'][1]['frames'][-1]['values']]
            distances = [math.dist(point['xyz_m'], p) for p in GEOMETRY]
            for ch in range(6):
                expected = -2 * math.pi * 445e6 * (distances[ch] - distances[0]) / 299792458
                actual = cmath.phase(values[ch] / values[0] * corrections[ch] / corrections[0])
                self.assertLess(abs(cmath.phase(cmath.exp(1j * (actual - expected)))), .001)

    def test_reject_position_specific_phase_error_with_diagnostics(self):
        session = synthetic()
        for tone in session['points'][1]['tones']:
            for frame in tone['frames']:
                v = complex(*frame['values'][3]) * cmath.exp(1j * .8)
                frame['values'][3] = [v.real, v.imag]
        report = core.solve_delay(session)
        self.assertFalse(report['accepted'])
        self.assertEqual(len(report['points']), 3)
        self.assertTrue(report['points'][1]['tones'][0]['position_phase_error_deg'])

    def test_heldout_noise_cannot_train_coefficients(self):
        session = synthetic()
        before = core.solve_delay(session)
        for point in session['points']:
            for tone in point['tones']:
                for frame in tone['frames']:
                    if frame['batch'] % 2:
                        v = complex(*frame['values'][4]) * cmath.exp(.6j)
                        frame['values'][4] = [v.real, v.imag]
        after = core.solve_delay(session)
        self.assertFalse(after['accepted'])
        self.assertEqual(before['correction'], after['correction'])
        self.assertEqual(before['delay_principal_ns'], after['delay_principal_ns'])

    def test_tone_configuration_has_distinct_bands_and_only_low_rf_conjugated(self):
        a, b = [core.tone_settings(SETTINGS, f, 433e6) for f in (425e6, 445e6)]
        self.assertEqual((a['if_hz'], b['if_hz']), (8e6, 12e6))
        self.assertEqual((a['band_min_hz'], b['band_min_hz']), (-4.55e6, -.55e6))
        self.assertEqual((a['rf_phase_conjugated'], b['rf_phase_conjugated']), (True, False))
        for lo in (425e6, float('nan'), 100e6):
            with self.assertRaises(ValueError):
                core.tone_settings(SETTINGS, 425e6, lo)

    def test_coordinate_defaults_use_endpoints_and_exclude_nearby_channels(self):
        with patch('builtins.input', side_effect=['', '', '']):
            points = calibrate.prompt_points(GEOMETRY, SETTINGS)
        self.assertEqual([p['name'] for p in points], ['L', 'O', 'R'])
        self.assertEqual(points[1]['xyz_m'], [0, 0, 0])
        self.assertEqual(points[0]['xyz_m'], list(GEOMETRY[0]))
        self.assertEqual(points[2]['xyz_m'], list(GEOMETRY[5]))
        self.assertNotIn(0, core.usable_channels(GEOMETRY, points[0]['xyz_m']))
        self.assertNotIn(5, core.usable_channels(GEOMETRY, points[2]['xyz_m']))
        self.assertTrue(all(len(p['tones']) == 2 for p in points))

    def test_invalid_capture_metadata_and_missing_tone_are_rejected(self):
        session = synthetic()
        session['points'][0]['tones'].pop()
        with self.assertRaises(ValueError):
            core.solve_delay(session)
        session = synthetic()
        session['points'][2]['tones'][0]['settings']['rf_phase_conjugated'] = False
        with self.assertRaises(ValueError):
            core.solve_delay(session)
        session = synthetic()
        session['points'][2]['tones'][1]['frames'].pop()
        with self.assertRaises(ValueError):
            core.solve_delay(session)

    def test_new_collector_does_not_capture_after_eof_or_continue_after_bad_signal(self):
        for failure in (EOFError(), ValueError('CH0 单音太弱')):
            session = synthetic()
            for point in session['points']:
                for tone in point['tones']:
                    tone['frames'] = []
            with tempfile.TemporaryDirectory() as tmp, \
                    patch('calibrate.wait_for_enter', side_effect=failure if isinstance(failure, EOFError) else None) as wait, \
                    patch('calibrate.run_capture', side_effect=failure) as capture:
                with self.assertRaises(type(failure)):
                    calibrate.collect(Path(tmp), Path(tmp), session, 4)
                self.assertEqual(wait.call_count, 1)
                self.assertEqual(capture.call_count, 0 if isinstance(failure, EOFError) else 1)

    def test_phase_drift_rejects_instead_of_establishing_delay(self):
        session = synthetic()
        for tone in session['points'][0]['tones']:
            for n, frame in enumerate(tone['frames']):
                v = complex(*frame['values'][1]) * cmath.exp(1j * (.6 if n % 2 else -.6))
                frame['values'][1] = [v.real, v.imag]
        report = core.solve_delay(session)
        self.assertFalse(report['accepted'])
        self.assertTrue(any('相干度' in reason for reason in report['reasons']))

    def test_installs_measurement_and_restores_both_files(self):
        session = synthetic()
        report = core.solve_delay(session)
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / 'config').mkdir()
            out = root / 'result'
            out.mkdir()
            original = calibrate.array_text(GEOMETRY, [complex(*v) for v in session['old_correction']]).encode()
            (root / 'config/array.csv').write_bytes(original)
            (out / 'array_before.csv').write_bytes(original)
            (root / 'config/job.json').write_bytes(b'{}')
            (root / 'config/channel_delay.json').write_bytes(b'{"previous":true}')
            session.update(array_sha256=hashlib.sha256(original).hexdigest(), job_sha256=hashlib.sha256(b'{}').hexdigest())
            calibrate.install_result(root, out, session, report)
            measured = json.loads((root / 'config/channel_delay.json').read_text())
            self.assertEqual(measured['applies_to_array_sha256'], hashlib.sha256((root / 'config/array.csv').read_bytes()).hexdigest())
            self.assertFalse(measured['measurement']['delay_unwrapped'])
            with patch('calibrate.exclusive_session'):
                calibrate.restore(root, out)
            self.assertEqual((root / 'config/array.csv').read_bytes(), original)
            self.assertEqual((root / 'config/channel_delay.json').read_bytes(), b'{"previous":true}')

    def test_failed_result_cannot_install_delay_or_phase(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(ValueError):
                calibrate.install_result(Path(tmp), Path(tmp), synthetic(), {'accepted': False})
            self.assertFalse(list(Path(tmp).iterdir()))

    def test_every_tone_waits_and_uses_its_own_search_band_and_four_rounds(self):
        session = synthetic()
        for p in session['points']:
            for t in p['tones']:
                t['frames'] = []
        order = []
        def capture(command, *unused):
            prefix = command[command.index('--output-prefix') + 1]
            rf = float(command[command.index('--rf-hz') + 1])
            expected = core.tone_settings(SETTINGS, rf, 433e6)
            self.assertEqual(float(command[command.index('--band-min-hz') + 1]), expected['band_min_hz'])
            order.append(rf)
            fft = [[complex(5, -3)] * 4096 for _ in range(6)]
            bin_index = round((expected['if_hz'] - expected['dds_hz']) / (25e6 / 4096)) % 4096
            for row in fft:
                row[bin_index] = 10000 + 5000j
            raw = encode_slots(fft)
            meta = valid_metadata()
            meta.update(rf_carrier_hz=rf, run_id='mock_' + str(rf))
            Path(prefix + '_metadata.json').write_text(json.dumps(meta))
            for i in range(4):
                Path(prefix + '_round%d_fft_slots.raw' % (i + 1)).write_bytes(raw)
                Path(prefix + '_round%d_adc.raw' % (i + 1)).write_bytes(bytes(196608))
        with tempfile.TemporaryDirectory() as tmp, patch('calibrate.run_capture', side_effect=capture), \
                patch('calibrate.wait_for_enter') as wait:
            calibrate.collect(Path(tmp), Path(tmp), session, 1)
            self.assertEqual(wait.call_count, 6)
        self.assertEqual(order, [425e6, 445e6] * 3)
        for p in session['points']:
            for t in p['tones']:
                self.assertEqual([f['round'] for f in t['frames']], [0, 1, 2, 3])


if __name__ == '__main__':
    unittest.main()

"""Numerical and data-contract tests; never connect to a board."""
import cmath
import copy
import hashlib
import json
import math
import os
from pathlib import Path
import random
import select
import sys
import tempfile
import time
import types
import unittest
from unittest.mock import Mock, patch

import calibration_math as core
import calibrate


GEOMETRY = [[x, 0.0, 0.0] for x in [-1.282361334873, -.936180667436, -.59, .59, .936180667436, 1.282361334873]]
SETTINGS = {'rf_hz': 445e6, 'phase_sign': 1, 'sample_rate_hz': 100e6,
            'dds_hz': 12.5e6, 'band_min_hz': -550e3, 'band_max_hz': -450e3,
            'tcp_host': '192.168.10.50', 'tcp_port': 7, 'adc_bits': 12, 'adc_encoding': 'twos-complement'}
GAINS = [cmath.rect(a, math.radians(p)) for a, p in zip([1, .7, 1.4, .9, 1.2, .6], [0, 45, -95, 170, -179, 75])]


def synthetic(noise=0.001, seed=20261025):
    rng = random.Random(seed)
    session = {'settings': dict(SETTINGS), 'geometry': GEOMETRY,
               'old_correction': [[1, 0]] * 6, 'points': []}
    for name, xyz in zip('ABC', [[GEOMETRY[0][0], 3, 0], [0, 3, 0], [GEOMETRY[-1][0], 3, 0]]):
        steering = core.response(GEOMETRY, xyz, 445e6)
        point = {'name': name, 'xyz_m': xyz, 'frames': []}
        for batch in range(8):
            for index in range(4):
                common = cmath.rect(10**rng.uniform(-1, 1), rng.uniform(-math.pi, math.pi))
                values = [common * (g * a + complex(rng.gauss(0, noise), rng.gauss(0, noise)))
                          for g, a in zip(GAINS, steering)]
                point['frames'].append({'batch': batch, 'round': index, 'bin': 4014,
                                        'values': [[v.real, v.imag] for v in values],
                                        'peak_to_local_floor_db': [35] * 6, 'tone_band_fraction': [.99] * 6})
        session['points'].append(point)
    return session


def encode_slots(fft):
    payload = bytearray(6 * 4096 * 64)
    for ch in range(6):
        for b, value in enumerate(fft[ch]):
            offset = (ch * 4096 + b) * 64
            for part, number in enumerate((value.real, value.imag)):
                bits = round(number) & ((1 << 39) - 1)
                payload[offset + part * 5:offset + part * 5 + 5] = bits.to_bytes(5, 'little')
    return payload


def valid_metadata():
    return {'capture_mode': 'hardware', 'capture_complete': True, 'batch_capture_complete': True,
            'completed_capture_rounds': 4, 'capture_round_count': 4, 'pl_version': 65537,
            'npu_handoff_status': 'disabled', 'error': '', 'rf_carrier_hz': 445e6,
            'sample_rate_hz': 100e6, 'dds_requested_hz': 12.5e6, 'phase_sign': 1,
            'adc_bits': 12, 'adc_encoding': 'twos-complement', 'adc_endian': 'little',
            'positions_x_m': [a[0] for a in GEOMETRY], 'positions_y_m': [0] * 6,
            'preprocessing': {'fft_payload_preprocessing': 'pl-hann64', 'ml_backend': 'pl',
                              'ml_startup_samples': 64, 'ml_retained_samples': 4032,
                              'ml_window': 'periodic_hann', 'adc_dc_applied': True},
            'captures': [{'index': i, 'complete': True, 'error': '', 'guard_checked': True,
                          'guard_ok': True, 'written_slots': 24576, 'adc_received_bytes': 196608,
                          'adc_dc_applied': True} for i in range(4)]}


class CalibrationTests(unittest.TestCase):
    def test_recovers_relative_gain_phase_with_random_common_phase_and_power(self):
        report = core.solve(synthetic())
        self.assertTrue(report['accepted'], report['reasons'])
        for result, gain in zip(report['correction'], GAINS):
            ratio = complex(*result) * gain
            self.assertLess(abs(math.degrees(cmath.phase(ratio))), .1)
            self.assertLess(abs(20 * math.log10(abs(ratio))), .02)
        for point in report['points']:
            self.assertLess(point['heldout_after_residual'], 1e-4)
            self.assertGreater(point['heldout_before_residual'], .1)

    def test_spherical_phase_far_field_limit_matches_positive_ps_sign(self):
        angle = math.radians(32)
        source = [1e6 * math.sin(angle), 1e6 * math.cos(angle), 0]
        spherical = core.response(GEOMETRY, source, 445e6)
        for ch, actual in enumerate(spherical):
            plane = cmath.exp(1j * 2 * math.pi * 445e6 / core.LIGHT_SPEED *
                              (GEOMETRY[ch][0] - GEOMETRY[0][0]) * math.sin(angle))
            self.assertLess(abs(cmath.phase(actual / plane)), 1e-5)

    def test_multipath_specific_to_one_position_is_rejected(self):
        session = synthetic()
        for frame in session['points'][2]['frames']:
            value = complex(*frame['values'][3]) * cmath.rect(1.5, .7)
            frame['values'][3] = [value.real, value.imag]
        self.assertFalse(core.solve(session)['accepted'])

    def test_unstable_lo_is_rejected(self):
        session = synthetic()
        for point in session['points']:
            for i, frame in enumerate(point['frames']):
                value = complex(*frame['values'][4]) * cmath.exp(1j * (-.6 if i % 2 else .6))
                frame['values'][4] = [value.real, value.imag]
        self.assertFalse(core.solve(session)['accepted'])

    def test_heldout_batches_do_not_train_coefficients(self):
        session = synthetic()
        original = core.solve(session)
        for point in session['points']:
            for frame in point['frames']:
                if frame['batch'] % 2:
                    value = complex(*frame['values'][2]) * cmath.exp(1j)
                    frame['values'][2] = [value.real, value.imag]
        report = core.solve(session)
        self.assertEqual(original['correction'], report['correction'])
        self.assertFalse(report['accepted'])

    def test_existing_correction_does_not_accumulate_into_new_calibration(self):
        session = synthetic()
        a = core.solve(session)['correction']
        session['old_correction'] = [[(1 / g).real, (1 / g).imag] for g in GAINS]
        self.assertEqual(a, core.solve(session)['correction'])

    def test_wrong_geometry_and_missing_round_are_rejected(self):
        session = synthetic()
        session['points'][0]['xyz_m'][0] += 1.0
        self.assertFalse(core.solve(session)['accepted'])
        session = synthetic()
        session['points'][0]['frames'].pop()
        with self.assertRaises(ValueError):
            core.solve(session)

    def test_invalid_coordinates(self):
        for point in ([0, float('nan'), 0], [0, 3], GEOMETRY[0]):
            with self.subTest(point=point), self.assertRaises(ValueError):
                core.response(GEOMETRY, point, 445e6)

    def test_signed39_boundaries_and_padding(self):
        fft = [[0j] * 4096 for _ in range(6)]
        fft[5][4014] = complex(-(1 << 38), (1 << 38) - 1)
        raw = encode_slots(fft)
        self.assertEqual(core.decode_slots(raw)[5][4014], fft[5][4014])
        raw[24] = 1
        with self.assertRaises(ValueError):
            core.decode_slots(raw)
        with self.assertRaises(ValueError):
            core.decode_slots(raw[:-1])

    def test_tone_extraction_and_weak_channel_rejection(self):
        fft = [[complex(5, -3)] * 4096 for _ in range(6)]
        for ch in range(6):
            fft[ch][4014] = 10000 * GAINS[ch]
        frame = core.extract_frame(encode_slots(fft), SETTINGS)
        self.assertEqual(frame['bin'], 4014)
        self.assertLess(frame['frequency_hz'], 0)
        fft[5][4014] = 0j
        with self.assertRaises(ValueError):
            core.extract_frame(encode_slots(fft), SETTINGS)

    def test_adc_clipping_and_padding(self):
        import struct
        clean = struct.pack('<6H', 3990, 3, 8, 4000, 100, 0) * 16384
        self.assertEqual(core.check_adc(clean, SETTINGS), [0] * 6)
        clipped = struct.pack('<6H', 2047, 0, 0, 0, 0, 0) * 16384
        with self.assertRaises(ValueError):
            core.check_adc(clipped, SETTINGS)
        with self.assertRaises(ValueError):
            core.check_adc(b'\xff\xff' + clean[2:], SETTINGS)

    def test_rejects_old_fft_or_partial_metadata(self):
        calibrate.validate_metadata(valid_metadata(), SETTINGS, GEOMETRY)
        for key, bad in [('completed_capture_rounds', 3), ('pl_version', 0x20261020),
                         ('npu_handoff_status', 'sent'), ('phase_sign', -1)]:
            meta = valid_metadata()
            meta[key] = bad
            with self.subTest(key=key), self.assertRaises(ValueError):
                calibrate.validate_metadata(meta, SETTINGS, GEOMETRY)
        meta = valid_metadata()
        meta['preprocessing']['fft_payload_preprocessing'] = 'raw'
        with self.assertRaises(ValueError):
            calibrate.validate_metadata(meta, SETTINGS, GEOMETRY)

    def test_capture_is_four_round_no_ipc_not_pha_fft(self):
        command = calibrate.capture_command(Path('/runtime'), Path('/out/A'), Path('/out/array.csv'), SETTINGS)
        root = Path('/runtime')
        self.assertEqual(command[:4], [str(root / 'lib/ld-linux-aarch64.so.1'),
                                     '--library-path', str(root / 'lib'),
                                     str(root / 'bin/icraft_aoa_ml3')])
        self.assertIn('--no-ipc', command)
        self.assertIn('--once', command)
        self.assertEqual(command[command.index('--capture-rounds') + 1], '4')
        self.assertNotIn('--observe-spectrum', command)
        self.assertNotIn('--fft-raw', command)

    def test_service_control_uses_managed_entry_points_and_private_python(self):
        root = Path('/runtime')
        with patch('calibrate.subprocess.run') as run:
            run.return_value.stdout = 'Stopped session'
            self.assertEqual(calibrate.service(root, 'stop'), 'Stopped session')
            self.assertEqual(run.call_args.args[0], [str(root / 'stop.sh')])
            self.assertGreater(run.call_args.kwargs['timeout'], 125)
            calibrate.service(root, 'start')
            self.assertEqual(run.call_args.args[0], [str(root / 'start.sh')])
            run.return_value.stdout = '{"running":true}'
            self.assertEqual(calibrate.service(root, 'status'), {'running': True})
            self.assertEqual(run.call_args.args[0],
                             [str(root / 'python.sh'), str(root / 'runtime/service.py'), 'status'])

    def test_prompt_default_and_field_override(self):
        with patch('builtins.input', side_effect=['', '0 4', '1.28 4 0.1']):
            points = calibrate.prompt_legacy_points(GEOMETRY, SETTINGS)
        self.assertEqual(points[0]['xyz_m'], [GEOMETRY[0][0], 3, 0])
        self.assertEqual(points[1]['xyz_m'], [0, 4, 0])
        self.assertEqual(points[2]['xyz_m'], [1.28, 4, .1])

    def test_atomic_install_keeps_geometry_and_rejects_stale_or_failed_result(self):
        session = synthetic()
        report = core.solve(session)
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / 'config').mkdir()
            directory = root / 'evidence'
            directory.mkdir()
            target = root / 'config/array.csv'
            original = core.array_text(GEOMETRY, [1 + 0j] * 6).encode('ascii')
            target.write_bytes(original)
            (root / 'config/job.json').write_bytes(b'{}')
            session['job_sha256'] = hashlib.sha256(b'{}').hexdigest()
            session['array_sha256'] = hashlib.sha256(original).hexdigest()
            bad = dict(report, accepted=False)
            with self.assertRaises(ValueError):
                calibrate.install_result(root, directory, session, bad)
            self.assertEqual(original, target.read_bytes())
            calibrate.install_result(root, directory, session, report)
            geometry, gains = core.read_array(target)
            self.assertEqual(geometry, GEOMETRY)
            self.assertEqual(gains, [complex(*c) for c in report['correction']])
            with self.assertRaises(RuntimeError):
                calibrate.install_result(root, directory, session, report)

    def test_session_exception_releases_lease_and_restores_previous_running_state(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            loader = types.SimpleNamespace(lock_file=Mock(), reject_active_dma=Mock(),
                                           image_path=Mock(), read_version=lambda: 65537)
            lease = loader.lock_file.return_value
            spec = types.SimpleNamespace(loader=types.SimpleNamespace(exec_module=Mock()))
            fcntl = types.SimpleNamespace(LOCK_EX=2, LOCK_NB=4, flock=Mock())
            with patch.dict('sys.modules', {'fcntl': fcntl}), \
                    patch('calibrate.importlib.util.spec_from_file_location', return_value=spec), \
                    patch('calibrate.importlib.util.module_from_spec', return_value=loader), \
                    patch('calibrate.stopped_service') as stop, \
                    patch('calibrate.service', side_effect=[{'running': True}, {'running': False}, 'RUNNING']) as service:
                with self.assertRaisesRegex(RuntimeError, 'capture failed'):
                    with calibrate.exclusive_session(root):
                        raise RuntimeError('capture failed')
                stop.assert_called_once_with(root)
                lease.close.assert_called_once()
                self.assertEqual(service.call_args_list[-1].args, (root, 'start'))

    def test_collection_reads_four_distinct_round_files(self):
        fft = [[complex(5, -3)] * 4096 for _ in range(6)]
        for ch in range(6):
            fft[ch][4014] = 10000 * GAINS[ch]
        payload = encode_slots(fft)
        meta = valid_metadata()
        meta['run_id'] = 'test_batch'
        session = synthetic()
        session['points'] = [dict(session['points'][0], frames=[])]
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            def fake_capture(command, *unused):
                prefix = command[command.index('--output-prefix') + 1]
                Path(prefix + '_metadata.json').write_text(json.dumps(meta))
                for index in range(4):
                    raw = bytearray(payload)
                    # Change a noise bin so byte hashes certify distinct round files.
                    raw[0] = index
                    Path(prefix + '_round%d_fft_slots.raw' % (index + 1)).write_bytes(raw)
                    Path(prefix + '_round%d_adc.raw' % (index + 1)).write_bytes(bytes(196608))
            with patch('calibrate.run_capture', side_effect=fake_capture), patch('calibrate.wait_for_enter') as wait:
                calibrate.collect(root, root, session, batches=1)
            wait.assert_called_once()
            frames = session['points'][0]['frames']
            self.assertEqual([f['round'] for f in frames], [0, 1, 2, 3])
            self.assertEqual(len({f['raw_sha256'] for f in frames}), 4)
            self.assertTrue((root / 'session.json').exists())

    def test_restore_refuses_to_overwrite_subsequent_calibration(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / 'config').mkdir()
            directory = root / 'result'
            directory.mkdir()
            original = core.array_text(GEOMETRY, [1 + 0j] * 6).encode()
            changed = core.array_text(GEOMETRY, [1 + .1j] * 6).encode()
            (directory / 'array_before.csv').write_bytes(original)
            (root / 'config/array.csv').write_bytes(changed)
            (directory / 'applied.json').write_text(json.dumps({
                'array_sha256': hashlib.sha256(changed).hexdigest(),
                'previous_sha256': hashlib.sha256(original).hexdigest()}))
            with patch('calibrate.exclusive_session'):
                calibrate.restore(root, directory)
                self.assertEqual((root / 'config/array.csv').read_bytes(), original)
                with self.assertRaises(ValueError):
                    calibrate.restore(root, directory)

    @unittest.skipUnless(sys.platform.startswith('linux'), 'Requires Linux controlling PTY')
    def test_queued_newlines_cannot_start_capture(self):
        import pty
        ready_read, ready_write = os.pipe()
        child, terminal = pty.fork()
        if child == 0:
            try:
                os.close(ready_write)
                os.write(1, b'BOOT\n')
                os.read(ready_read, 1)
                os.close(ready_read)
                calibrate.wait_for_enter('FRESH_ENTER_REQUIRED:')
                os.write(1, b'CAPTURE_STARTED\n')
                os._exit(0)
            except BaseException:
                os._exit(1)
        os.close(ready_read)
        seen = bytearray()
        def until(marker):
            deadline = time.monotonic() + 5
            while marker not in seen:
                if time.monotonic() > deadline:
                    self.fail('PTY timeout: ' + repr(bytes(seen)))
                if select.select([terminal], [], [], .1)[0]:
                    seen.extend(os.read(terminal, 4096))
        try:
            until(b'BOOT')
            # Simulate extra Enters from earlier coordinate entry/paste.
            os.write(terminal, b'\n\n\n')
            os.write(ready_write, b'1')
            until(b'FRESH_ENTER_REQUIRED:')
            deadline = time.monotonic() + .25
            while time.monotonic() < deadline:
                if select.select([terminal], [], [], .05)[0]:
                    seen.extend(os.read(terminal, 4096))
            self.assertNotIn(b'CAPTURE_STARTED', seen)
            os.write(terminal, b'\n')
            until(b'CAPTURE_STARTED')
            _, status = os.waitpid(child, 0)
            self.assertEqual(status, 0)
            child = None
        finally:
            os.close(ready_write)
            os.close(terminal)
            if child is not None:
                try:
                    os.kill(child, 9)
                except ProcessLookupError:
                    pass
                os.waitpid(child, 0)

    def test_no_capture_after_terminal_eof(self):
        session = synthetic()
        with tempfile.TemporaryDirectory() as tmp, \
                patch('calibrate.wait_for_enter', side_effect=EOFError), \
                patch('calibrate.run_capture') as capture:
            root = Path(tmp)
            with self.assertRaises(EOFError):
                calibrate.collect(root, root, session, batches=1)
            capture.assert_not_called()

    def test_bad_signal_stops_at_first_batch_without_advancing_points(self):
        session = synthetic()
        for point in session['points']:
            point['frames'] = []
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            meta = valid_metadata()
            meta['run_id'] = 'weak_signal'
            def fake_capture(command, *unused):
                prefix = command[command.index('--output-prefix') + 1]
                Path(prefix + '_metadata.json').write_text(json.dumps(meta))
                Path(prefix + '_round1_fft_slots.raw').write_bytes(bytes(6 * 4096 * 64))
            with patch('calibrate.run_capture', side_effect=fake_capture) as capture, \
                    patch('calibrate.wait_for_enter') as wait:
                with self.assertRaisesRegex(ValueError, 'CH0'):
                    calibrate.collect(root, root, session, batches=8)
                capture.assert_called_once()
                wait.assert_called_once()
            self.assertEqual([len(p['frames']) for p in session['points']], [0, 0, 0])


if __name__ == '__main__':
    unittest.main()

"""Independent compensation paths reach the board launcher without hardware access."""
import importlib.util
import csv
import io
import json
from pathlib import Path
import sys
import tempfile
import types
import unittest
from unittest.mock import patch

BOARD = Path(__file__).resolve().parents[3] / 'Board/runtime'
sys.path.insert(0, str(BOARD))
loader = types.ModuleType('load_pl')
loader.prepare_runtime = lambda: (_ for _ in ()).throw(AssertionError('No hardware in path tests'))
with patch.dict(sys.modules, {'load_pl': loader}):
    spec = importlib.util.spec_from_file_location('board_compensation_config', BOARD / 'run_board.py')
    runtime = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(runtime)


class CompensationConfigTests(unittest.TestCase):
    def test_omitted_or_empty_setting_keeps_previous_launch_behavior(self):
        with tempfile.TemporaryDirectory() as directory:
            for value in ({}, {'compensation_file': ''}, {'compensation_file': None}):
                self.assertIsNone(runtime.compensation_path(Path(directory), value))

    def test_relative_path_resolves_from_runtime_not_current_working_directory(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            target = base / 'config' / 'channel compensation.csv'
            target.parent.mkdir()
            target.write_text('supplied independently; the C++ loader validates coefficients')
            self.assertEqual(runtime.compensation_path(base, {
                'compensation_file': 'config/channel compensation.csv'}), target.resolve())

    def test_absolute_path_inside_runtime_is_kept(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / 'coeff.csv'
            target.touch()
            self.assertEqual(runtime.compensation_path(Path(directory), {
                'compensation_file': str(target.resolve())}), target.resolve())

    def test_missing_path_or_directory_is_rejected_before_hardware_start(self):
        with tempfile.TemporaryDirectory() as directory:
            for name in ('missing.csv',):
                with self.assertRaisesRegex(ValueError, 'not found'):
                    runtime.compensation_path(Path(directory), {'compensation_file': name})

    def test_external_compensation_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory); (root/'inside').mkdir(); outside=root/'coeff.csv'; outside.touch()
            with self.assertRaisesRegex(ValueError, 'inside'):
                runtime.compensation_path(root/'inside', {'compensation_file':str(outside)})

    def test_non_path_setting_is_rejected(self):
        for value in (False, 1, [], {}):
            with self.assertRaisesRegex(ValueError, 'path string'):
                runtime.compensation_path(BOARD, {'compensation_file': value})

    def test_bank_uses_operating_rf_and_keeps_legacy_file_unchanged(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            legacy = base / 'legacy.csv'
            legacy.write_text('previous compensation')
            bank = {'nodes': [{'rf_hz': rf, 'accepted': True,
                              'correction': [[gain, 0]] * 6,
                              'phase_center_dx_m': [0] * 6}
                             for rf, gain in ((445e6, 1), (445.5e6, 4))]}
            payload = json.dumps(bank).encode()
            (base / 'bank.json').write_bytes(payload)
            job = {'compensation_file': 'legacy.csv', 'compensation_bank': 'bank.json',
                   'rf_hz': 445.25e6}
            path, selection = runtime.compensation_config(base, job)
            self.assertIsNone(path)
            self.assertEqual(selection['source'], 'interpolated')
            self.assertEqual(selection['bracket_frequencies_hz'], [445e6, 445.5e6])
            rows = list(csv.DictReader(io.StringIO(runtime.csv_text(selection))))
            self.assertEqual(len(rows), 6)
            self.assertTrue(all(float(row['rf_hz']) == 445.25e6 for row in rows))
            self.assertTrue(all(float(row['correction_real']) == 2 for row in rows))
            self.assertEqual(legacy.read_text(), 'previous compensation')
            job['rf_hz'] = 446e6
            with self.assertRaisesRegex(ValueError, 'outside'):
                runtime.compensation_config(base, job)
            self.assertEqual((base / 'bank.json').read_bytes(), payload)

    def test_without_bank_uses_existing_single_frequency_csv(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            path = base / 'active.csv'
            path.touch()
            self.assertEqual(runtime.compensation_config(base, {
                'compensation_file': 'active.csv'}), (path.resolve(), None))


if __name__ == '__main__':
    unittest.main()

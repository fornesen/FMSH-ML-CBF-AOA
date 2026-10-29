"""Distinguish the PS startup-filtered spectrum from legacy full-window packets."""
import base64
import copy
from pathlib import Path
import struct
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from protocol import validate_observation


class ObservationSpectrumTests(unittest.TestCase):
    def setUp(self):
        self.packet = dict(run_id='spectrum-test', baseband_sample_rate_hz=25e6)
        self.observation = dict(source='PL_PHA_beamformed_IQ', run_id='spectrum-test',
            valid=True, waveform_enabled=False, spectrum_enabled=True, error='',
            sample_count=4096, iq_scale=1048576, sample_rate_hz=25e6, round_index=3,
            spectrum=dict(bins=4096, reference='per_frame_peak', window='periodic_Hann',
                dc_removed=False, frequency_start_hz=-12.5e6, frequency_step_hz=25e6/4096,
                db_min=-120, db_max=0, peak_frequency_hz=None,
                db_f32le=base64.b64encode(struct.pack('<4096f', *([-120]*4096))).decode()))

    def test_legacy_payload_keeps_its_original_semantics(self):
        before = copy.deepcopy(self.observation)
        validate_observation(self.observation, self.packet)
        self.assertEqual(self.observation, before)
        self.assertNotIn('preprocessing', self.observation['spectrum'])

    def test_valid_startup_contract_preserves_all_frequency_bins(self):
        self.observation['spectrum'].update(preprocessing='zero64_hann4032',
                                             startup_samples=64, retained_samples=4032)
        before = copy.deepcopy(self.observation)
        validate_observation(self.observation, self.packet)
        self.assertEqual(self.observation, before)

    def test_rejects_inconsistent_partial_or_shortened_contracts(self):
        contract = dict(preprocessing='zero64_hann4032', startup_samples=64, retained_samples=4032)
        for missing in contract:
            with self.subTest(missing=missing):
                value = copy.deepcopy(self.observation)
                value['spectrum'].update(contract)
                value['spectrum'].pop(missing)
                with self.assertRaises(ValueError):
                    validate_observation(value, self.packet)
        for change in (dict(startup_samples=0), dict(retained_samples=4096),
                       dict(startup_samples=64.0), dict(retained_samples=True), dict(bins=4032)):
            with self.subTest(change=change):
                value = copy.deepcopy(self.observation)
                value['spectrum'].update(contract)
                value['spectrum'].update(change)
                with self.assertRaises(ValueError):
                    validate_observation(value, self.packet)


if __name__ == '__main__':
    unittest.main()

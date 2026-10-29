import copy
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from demo import packets
from protocol import validate


class PreprocessingTests(unittest.TestCase):
    def packet(self):
        packet, _ = packets()
        packet.update(capture_mode='hardware', pl_version=0x00010001)
        packet['preprocessing'] = dict(adc_dc_applied=True, adc_dc_removed_codes=[0]*6,
            ml_applied=True, ml_startup_samples=64, ml_retained_samples=4032,
            ml_window='periodic_hann', ml_processing_ms=.5,
            ml_backend='pl', fft_payload_preprocessing='pl-hann64')
        return packet

    def test_windowed_pl_passes_without_modifying_data(self):
        packet = self.packet()
        before = copy.deepcopy(packet)
        validate(packet)
        self.assertEqual(packet, before)

    def test_legacy_software_backend(self):
        packet = self.packet()
        packet['pl_version'] = 0x20261020
        packet['preprocessing'].update(ml_backend='ps', fft_payload_preprocessing='raw')
        validate(packet)

    def test_old_packets_do_not_gain_inferred_metadata(self):
        packet, _ = packets()
        packet.pop('preprocessing', None)
        validate(packet)
        self.assertNotIn('preprocessing', packet)

    def test_rejects_double_window_and_wrong_effective_length(self):
        for changes in (dict(ml_backend='ps'), dict(ml_retained_samples=4096),
                        dict(ml_startup_samples=0, ml_retained_samples=4096),
                        dict(fft_payload_preprocessing='raw'), dict(ml_backend='none')):
            with self.subTest(changes=changes):
                packet = self.packet()
                packet['preprocessing'].update(changes)
                with self.assertRaises(ValueError):
                    validate(packet)

    def test_failed_frame_can_report_not_processed(self):
        packet = self.packet()
        packet.update(complete=False, status='error')
        packet['preprocessing'].update(ml_applied=False, ml_backend='none',
                                       fft_payload_preprocessing='unknown')
        validate(packet)

    def test_new_hardware_cannot_omit_the_processing_contract(self):
        packet = self.packet()
        packet.pop('preprocessing')
        with self.assertRaises(ValueError):
            validate(packet)
        packet = self.packet()
        packet['status'] = 'ok'
        packet['preprocessing']['ml_applied'] = False
        with self.assertRaises(ValueError):
            validate(packet)


if __name__ == '__main__':
    unittest.main()

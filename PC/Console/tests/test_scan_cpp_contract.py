"""Interface coverage based on a genuine C++ four-frame scan replay packet."""
import copy
import json
from pathlib import Path
import sys
import unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from protocol import validate

class CppScanTests(unittest.TestCase):
    def packet(self):
        return json.loads((Path(__file__).parent/'fixtures/direct_scan_cpp.json').read_text(encoding='utf-8'))

    def test_genuine_cpp_packet_with_not_run_hardware_checks(self):
        validate(self.packet())

    def test_empty_no_signal_is_not_a_transport_error(self):
        p=self.packet();p['status']='no_signal';s=p['beam_scan']
        s.update(status='no_signal',valid=False,angles_deg=[],normalized_power=[],relative_db=[],candidates=[])
        validate(p)

    def test_partial_capture_does_not_disconnect_receiver(self):
        p=self.packet();p.update(status='error',complete=False,direction_frames=0,direction_rounds=[])
        s=p['beam_scan'];s.update(status='not_run',valid=False,frame_count=0,
            angles_deg=[],normalized_power=[],relative_db=[],candidates=[])
        s['verification'].update(enabled=False,status='not_run',checks=[])
        validate(p)

    def test_check_data_cannot_be_relabelled_as_npu_input(self):
        p=self.packet();s=p['beam_scan'];candidate=s['candidates'][0]
        s['verification'].update(status='matched',restore_verified=True,checks=[dict(candidate_id=0,
            angle_deg=candidate['angle_deg'],capture_id=p['run_id']+'_scancheck0',expected_power=1,
            observed_power=1,relative_error=0,input_shape_error=0,registers_verified=True,valid=True,npu_eligible=True)])
        with self.assertRaises(ValueError):validate(p)

if __name__=='__main__':unittest.main()

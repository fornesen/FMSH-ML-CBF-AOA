"""Five/six-class wire compatibility for the PM consumer."""
import copy
from pathlib import Path
import sys
import unittest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from demo import packets
from protocol import decode,encode,validate


class PMProtocolTests(unittest.TestCase):
    def test_existing_five_class_packet(self):
        _,packet=packets()
        self.assertEqual(len(packet['classification']['classes']),5)
        self.assertEqual(decode(encode(packet)[8:]),packet)

    def test_six_class_pm_round_trip(self):
        _,packet=packets()
        c=packet['classification']
        c.update(classes=c['classes']+['PM'],probabilities=[0,0,0,0,0,1],predicted_class='PM',predicted_class_index=5)
        self.assertEqual(decode(encode(packet)[8:]),packet)

    def test_order_count_and_probability_validation(self):
        _,packet=packets()
        classes=['FM','AM','CW','noise','OOK','PM']
        invalid=[(list(reversed(classes)),[1,0,0,0,0,0]),
                 (classes,[1,0,0,0,0]),(classes,[1,0,0,0,0,0,0]),
                 (classes,[0,0,0,0,0,.5]),(classes,[0,0,0,0,0,float('inf')]),
                 (classes[:-1]+['PSK'],[0,0,0,0,0,1])]
        for names,probabilities in invalid:
            with self.subTest(names=names,probabilities=probabilities):
                bad=copy.deepcopy(packet)
                bad['classification'].update(classes=names,probabilities=probabilities)
                with self.assertRaises(ValueError):validate(bad)


if __name__=='__main__':unittest.main()

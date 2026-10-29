"""Validate PC stimulus batch association across locate / identify switches."""
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'stimulus'))
from moving_replay import TaggedCursor


class FrameScheduleTests(unittest.TestCase):
    def test_four_frame_batches_in_both_modes(self):
        c = TaggedCursor(2)
        for batch in range(3):
            for i in range(4):
                self.assertEqual(c.request(f'START 4 {i} {100+batch}_1\n'.encode()), (batch%2, i, batch//2))
                c.sent()
        self.assertEqual(c.completed, 3)

    def test_locate_identify_locate_and_wrap(self):
        c = TaggedCursor(2)
        self.assertEqual(c.request(b'START 1 0 100_1\n'), (0, 0, 0))
        c.sent()
        for i in range(4):
            self.assertEqual(c.request(f'START 4 {i} 101_1\n'.encode()), (1, i, 0))
            c.sent()
        self.assertEqual(c.request(b'START 1 0 102_1\n'), (0, 0, 1))
        c.sent()
        self.assertEqual(c.completed, 3)

    def test_reject_mixed_position_batch(self):
        c = TaggedCursor(16)
        c.request(b'START 4 0 100_1\n');c.sent()
        for request in (b'START 1 0 101_1\n', b'START 4 2 100_1\n',
                        b'START 4 1 101_1\n', b'START 4 0 100_1\n'):
            with self.assertRaises(ValueError):c.request(request)
        self.assertEqual(c.completed, 0)
        self.assertEqual(c.request(b'START 4 1 100_1\n'), (0, 1, 0))

    def test_reject_malformed_request(self):
        for request in (b'START', b'START 2 0 100\n', b'START 1 1 100\n',
                        b'START 4 1 100\n', b'START 4 0 bad\n'):
            with self.assertRaises(ValueError):TaggedCursor(16).request(request)


if __name__ == '__main__':
    unittest.main(verbosity=2)

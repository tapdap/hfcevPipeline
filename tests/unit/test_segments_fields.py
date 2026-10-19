import unittest

import numpy as np

from hfcev.core import field_quality, segments

S = 1_000_000


class Segments(unittest.TestCase):
    def test_split_on_gaps_longer_than_threshold(self):
        t = np.array([0, 5, 65, 126, 126, 400]) * S
        self.assertEqual(segments.split(t, 60).tolist(), [0, 0, 0, 1, 1, 2])
        self.assertEqual(segments.count(t, 300), 1)

    def test_hours_covered(self):
        h0 = 500_000
        self.assertEqual(segments.hours_covered(h0 * 3600 * S, (h0 + 2) * 3600 * S, {h0}), 0.5)


class FieldFlags(unittest.TestCase):
    def test_encodable_range_and_all_ones(self):
        e = field_quality.Encoding(length=8, signed=False, factor=1.0, offset=-40.0)
        self.assertEqual((e.bounds(), e.all_ones()), ((-40.0, 215.0), 215.0))
        f = field_quality.flags(np.array([np.nan, 0.0, 20.0, 215.0, 300.0]), [e])
        self.assertEqual(f["missing"].tolist(), [True, False, False, False, False])
        self.assertEqual(f["all_ones"].tolist(), [False, False, False, True, False])
        self.assertEqual(f["outside_encodable"].tolist(), [False, False, False, False, True])

    def test_compare_and_scale_hint(self):
        c = field_quality.compare(np.array([1.0, 2.0, np.nan, 5.0]), np.array([1.0, 0.2, 3.0, np.nan]))
        self.assertEqual({k: int(v.sum()) for k, v in c.items()},
                         dict(equal=1, differ=1, stored_only=1, log_only=1, both_missing=0))
        self.assertEqual(field_quality.scale_hint(np.array([20.0]), np.array([2.0]))["x1e1"], 1)


if __name__ == "__main__":
    unittest.main()


class StuckAndJumps(unittest.TestCase):
    def test_constant_runs_split_by_zero(self):
        t = np.arange(0, 1300, 100) * S          # 13 records, 100 s apart, one segment
        v = np.array([5.0] * 8 + [0.0] * 5)      # 700 s at 5, 400 s at 0
        out = field_quality.stuck_runs(v, t, np.zeros(13, int), [600])
        self.assertEqual(out["600s"], dict(runs_nonzero=1, records_nonzero=8, runs_zero=0, records_zero=0))

    def test_jumps_relative_to_range(self):
        t = np.arange(10) * 5 * S
        v = np.array([0.0, 1, 2, 3, 100, 4, 5, 6, 7, 8])
        out = field_quality.jumps(v, t, np.zeros(10, int), 60, 0.5)
        self.assertEqual((out["pairs"], out["jumps"]), (9, 2))

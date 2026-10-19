import unittest

import numpy as np

from hfcev.core import odometer

S = 1_000_000


class Timeline(unittest.TestCase):
    def setUp(self):
        t = np.array([0, 10, 20, 30, 40], np.int64) * S
        self.ref_t, self.ref_km = odometer.reference(t, [100.0, 100.1, np.nan, 100.3, 0.0])

    def test_reference_drops_invalid(self):
        self.assertEqual(self.ref_km.tolist(), [100.0, 100.1, 100.3])

    def test_between_neighbours(self):
        out = odometer.check(self.ref_t, self.ref_km, np.array([15, 15, 5, -1, 35], np.int64) * S,
                             [100.2, 99.0, 100.05, 100.0, 100.3])
        self.assertEqual(out[:3].tolist(), [1.0, 0.0, 1.0])
        self.assertTrue(np.isnan(out[3]) and np.isnan(out[4]))  # no neighbour before / after

    def test_neighbours_are_strict(self):
        out = odometer.check(self.ref_t, self.ref_km, np.array([10], np.int64) * S, [100.1])
        self.assertEqual(out.tolist(), [1.0])  # compared with 0 s and 30 s, not with itself

    def test_gap_limit(self):
        out = odometer.check(self.ref_t, self.ref_km, np.array([15], np.int64) * S, [100.2], max_gap_s=5)
        self.assertTrue(np.isnan(out[0]))

    def test_summary(self):
        self.assertEqual(odometer.summary(np.array([1.0, 0.0, np.nan, 1.0])), dict(tested=3, passed=2))


class Sequence(unittest.TestCase):
    def test_receipt_order(self):
        out = odometer.sequence_check([1.0, np.nan, 2.0, 1.5, 3.0, 4.0])
        self.assertTrue(np.isnan(out[0]) and np.isnan(out[1]))
        self.assertEqual(out[2:5].tolist(), [0.0, 0.0, 1.0])  # 2.0 > 1.5 fails both; NaN is skipped
        self.assertTrue(np.isnan(out[5]))

    def test_too_short(self):
        self.assertTrue(np.isnan(odometer.sequence_check([1.0, 2.0])).all())


if __name__ == "__main__":
    unittest.main()

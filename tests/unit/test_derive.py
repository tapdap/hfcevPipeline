import unittest

import numpy as np

from hfcev.core import derive

S = 1_000_000  # microseconds per second


class FixProxy(unittest.TestCase):
    # positions in degrees; 0.01 deg latitude ~ 1.11 km
    def test_frozen_run_while_moving_is_no_fix(self):
        t = np.arange(6) * 10 * S
        lat = np.array([30.0, 30.0, 30.0, 30.0, 30.05, 30.06])   # frozen 30 s, then 5.6 km away
        lon = np.full(6, 120.0)
        self.assertEqual(derive.fix_proxy(t, lat, lon, 5).tolist(), [False, True, True, True, False, False])

    def test_frozen_run_while_parked_stays_valid(self):
        t = np.arange(5) * 60 * S
        lat = np.array([30.0, 30.0, 30.0, 30.0, 30.00001])
        lon = np.full(5, 120.0)
        self.assertFalse(derive.fix_proxy(t, lat, lon, 5).any())

    def test_odometer_rise_marks_frozen_run(self):
        t = np.arange(4) * 60 * S
        lat, lon = np.full(4, 30.0), np.full(4, 120.0)          # never moves, no next fix
        odo_t, odo_km = np.array([0, 180]) * S, np.array([100.0, 102.0])
        self.assertEqual(derive.fix_proxy(t, lat, lon, 5, odo_t, odo_km).tolist(), [False, True, True, True])


class TrackSteps(unittest.TestCase):
    def test_unusable_fix_breaks_track(self):
        t = np.arange(4) * 10 * S
        lat = np.array([30.0, 30.001, 30.002, 30.003])
        lon = np.full(4, 120.0)
        usable = np.array([True, True, False, True])
        dist, speed, dur = derive.track_steps(t, lat, lon, usable, 60)
        self.assertTrue(np.isnan(dist[0]) and np.isnan(dist[2]) and np.isnan(dist[3]))
        self.assertAlmostEqual(dist[1], 0.1112, places=3)
        self.assertAlmostEqual(speed[1], 40.0, delta=0.1)
        self.assertEqual(dur[1], 10)

    def test_long_gap_breaks_track(self):
        t = np.array([0, 61]) * S
        dist, _, _ = derive.track_steps(t, np.array([30.0, 30.01]), np.array([120.0, 120.0]), np.array([True, True]), 60)
        self.assertTrue(np.isnan(dist).all())


class OdometerSteps(unittest.TestCase):
    def test_kinds(self):
        t = np.array([0, 60, 90, 120, 600, 900, 10_000, 10_060]) * S
        km = np.array([100, 101, 106, 106, 107, 150, 160, 159], float)
        seg = np.array([0, 0, 0, 0, 1, 2, 3, 3])
        inc, kind, prev = derive.odometer_steps(t, km, seg, 150, max_gap_s=3600)
        self.assertEqual(kind.tolist(), ["", "in_segment", "in_segment_delayed", "in_segment", "across_gap",
                                         "jump", "across_long_gap", "backward"])
        self.assertEqual(inc[2], 5)
        self.assertEqual(prev.tolist(), [-1, 0, 1, 2, 3, 4, 5, 6])

    def test_invalid_readings_skipped(self):
        t = np.array([0, 10, 20]) * S
        inc, kind, prev = derive.odometer_steps(t, np.array([100, 0, 100]), np.zeros(3), 150)
        self.assertEqual(kind.tolist(), ["", "", "in_segment"])
        self.assertEqual(prev[2], 0)


class CounterSteps(unittest.TestCase):
    P = 655.36

    def test_wrap_and_missing_readings(self):
        v = np.array([650, 0, 654, 2, 5, np.nan, 7], float)
        km = np.array([100, 101, 102, 103, 104, 105, 106], float)
        rest, inc, kind = derive.counter_steps(v, km, self.P, 1000)
        self.assertEqual(kind.tolist(), ["", "", "increment", "wrap", "increment", "", "increment"])
        self.assertAlmostEqual(rest[3], 2 + self.P)
        self.assertAlmostEqual(inc[3], 2 + self.P - 654)
        self.assertTrue(np.isnan(rest[1]) and np.isnan(rest[5]))

    def test_backward_step_kept(self):
        _, inc, kind = derive.counter_steps(np.array([10, 9, 11], float), None, self.P, 1000)
        self.assertEqual(kind.tolist(), ["", "backward", "increment"])
        self.assertEqual(inc[1], -1)

    def test_wrap_hidden_in_gap_inferred_from_odometer(self):
        # 0.1 kg/km on normal steps; 7,000 km gap -> ~700 kg consumed: one full wrap plus 44.64 kg
        v = np.array([100, 110, 120, 164.64 % self.P + 0], float)
        km = np.array([0, 100, 200, 7200], float) + 1000
        rest, inc, kind = derive.counter_steps(v, km, self.P, 1000)
        self.assertEqual(kind[3], "wrap_inferred")
        self.assertAlmostEqual(inc[3], 164.64 + self.P - 120)


class Align(unittest.TestCase):
    def test_nearest_tolerance_and_tie(self):
        gps = np.array([0, 10, 40]) * S
        can = np.array([4, 5, 26, 60]) * S
        idx, off = derive.align(can, gps, 10)
        self.assertEqual(idx.tolist(), [0, 0, -1, -1])               # 5 s tie -> earlier fix
        self.assertEqual(off[:2].tolist(), [-4, -5])
        idx, _ = derive.align(can, gps, 10, tie="ambiguous")
        self.assertEqual(idx.tolist(), [0, -2, -1, -1])

    def test_backward(self):
        idx, _ = derive.align(np.array([9, 10, 25]) * S, np.array([0, 10]) * S, 10, "backward")
        self.assertEqual(idx.tolist(), [0, 1, -1])


class GapCheck(unittest.TestCase):
    def test_gps_gap_explains_odometer_jump(self):
        t = np.array([0, 30]) * S                                  # receipt clock: 5 km in 30 s
        km_inc, prev, mask = np.array([np.nan, 5.0]), np.array([-1, 0]), np.array([False, True])
        gps_t = np.array([-400, -395, 20, 25]) * S                  # GPS silent for 415 s before the jump
        lat = np.array([30.0, 30.0, 30.045, 30.045])                # 5.0 km north across the gap
        out = derive.gap_check(t, km_inc, mask, prev, gps_t, lat, np.full(4, 120.0), 1200)
        self.assertEqual((out["n"], out["with_gps"], out["gap_over_120s"]), (1, 1, 1))
        self.assertEqual(out["gap_s"]["p50"], 415)
        self.assertAlmostEqual(out["displacement_to_odometer"]["p50"], 1.0, delta=0.01)


class Runs(unittest.TestCase):
    def test_runs_stay_in_segment(self):
        mask = np.array([1, 1, 0, 1, 1, 1], bool)
        seg = np.array([0, 0, 0, 0, 1, 1])
        self.assertEqual(derive.runs(mask, seg), [(0, 2), (3, 4), (4, 6)])


if __name__ == "__main__":
    unittest.main()

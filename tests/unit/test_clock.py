import unittest

import numpy as np

from hfcev.core import clock
from hfcev.core.clock import NAT

S = 1_000_000
BASE = 1_715_162_400 * S  # 2024-05-08 10:00:00


def run(reported_s, log_s, content=None, identity=None, lead=60, lag=86400):
    reported = np.array([NAT if r is None else BASE + int(r * S) for r in reported_s], np.int64)
    logged = np.array([NAT if t is None else BASE + int(t * S) for t in log_s], np.int64)
    content = content or [(i,) for i in range(len(reported))]
    identity = np.array(identity if identity is not None else [True] * len(reported))
    return clock.checks(reported, logged, content, identity, lead, lag)


class Checks(unittest.TestCase):
    def test_all_pass_inside(self):
        c = run([0, 5, 10, 15], [0.3, 5.3, 10.3, 15.3])
        self.assertEqual(c["valid"].tolist(), [False, True, True, False])  # end messages lack a neighbour
        self.assertEqual(clock.first_failure(c, 0), "clock_out_of_order")
        self.assertIsNone(clock.first_failure(c, 1))

    def test_missing_clock(self):
        c = run([0, None, 10, 15, 20], [0, 5, 10, 15, 20])
        self.assertEqual(clock.first_failure(c, 1), "clock_missing")
        self.assertTrue(c["valid"][2])  # neighbours are taken among V1+V2 messages only

    def test_lag_and_lead_bounds(self):
        c = run([0, -86401, 10, 76, 20, 25], [0, 5, 10, 15, 20, 25])
        self.assertEqual(clock.first_failure(c, 1), "clock_lag_out_of_range")  # reset device clock
        self.assertEqual(clock.first_failure(c, 3), "clock_lag_out_of_range")  # leads receipt by 61 s
        self.assertTrue(c["valid"][2] and c["valid"][4])
        self.assertAlmostEqual(c["lag_s"][1], 86406.0)
        edge = run([0, 5, 10, 75, 20, 25], [0, 5, 10, 15, 20, 25])
        self.assertEqual(clock.first_failure(edge, 3), "clock_out_of_order")  # a 60 s lead is allowed
        loose = run([0, 5, 10, 76, 20, 25], [0, 5, 10, 15, 20, 25], lead=600)
        self.assertEqual(clock.first_failure(loose, 3), "clock_out_of_order")

    def test_order_requires_strictly_between(self):
        c = run([0, 5, 5, 10], [0, 5, 6, 10])
        self.assertEqual([clock.first_failure(c, i) for i in (1, 2)], ["clock_out_of_order"] * 2)

    def test_reuse_with_different_content(self):
        c = run([0, 5, 10, 15, 5], [0, 5, 10, 15, 20], content=[("a",), ("b",), ("c",), ("d",), ("e",)])
        self.assertEqual(clock.first_failure(c, 1), "clock_reused")
        same = run([0, 5, 10, 15, 5], [0, 5, 10, 15, 20], content=[("a",), ("b",), ("c",), ("d",), ("b",)])
        self.assertTrue(same["v4"][1])

    def test_identity_failure_first(self):
        c = run([0, 5, 10], [0, 5, 10], identity=[True, False, True])
        self.assertEqual(clock.first_failure(c, 1), "identity_unresolved")


class Collisions(unittest.TestCase):
    def test_all_claimants_are_nulled(self):
        times = np.array([1, 2, 2, NAT, NAT, 3, 2], np.int64)
        self.assertEqual(clock.null_collisions(times).tolist(), [False, True, True, False, False, False, True])

    def test_empty(self):
        self.assertEqual(clock.null_collisions(np.array([], np.int64)).tolist(), [])


class EarlierRule(unittest.TestCase):
    def test_five_second_chain(self):
        r = np.array([BASE + 5 * S * i for i in range(6)], np.int64)
        ahead = clock.legacy_chain_ok(r, r - S, np.zeros(6, bool))
        self.assertFalse(ahead.any())  # reported time after log time
        ok = clock.legacy_chain_ok(r, r + 2 * S, np.zeros(6, bool))
        self.assertEqual(ok.tolist(), [False, False, True, True, False, False])
        off_grid = r.copy()
        off_grid[3] += S
        self.assertFalse(clock.legacy_chain_ok(off_grid, r + 2 * S, np.zeros(6, bool))[2])


if __name__ == "__main__":
    unittest.main()

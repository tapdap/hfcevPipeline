import unittest

import numpy as np

from hfcev.core import motion


class Motion(unittest.TestCase):
    def test_degrees_and_hemisphere(self):
        lat = motion.degrees([3630.0, 3630.0, 3699.0], ["N", " s ", "N"], "S", 90)
        np.testing.assert_allclose(lat[:2], [36.5, -36.5])
        self.assertTrue(np.isnan(lat[2]))  # minutes >= 60

    def test_haversine(self):
        self.assertAlmostEqual(float(motion.haversine_km(0.0, 0.0, 0.0, 1.0)), 111.195, places=2)

    def test_check_pass_fail_untestable(self):
        sec = 1_000_000
        ref = motion.track(np.array([0, 60 * sec]), np.array([36.0, 36.0]), np.array([111.0, 111.01]))
        t = np.array([30 * sec, 30 * sec, 30 * sec, 600 * sec])
        lat = np.array([36.0, 37.0, np.nan, 36.0])       # on the track / 111 km away / invalid / after the track
        lon = np.array([111.005, 111.005, 111.0, 111.0])
        out = motion.check(*ref, t, lat, lon, max_gap_s=300, max_speed_kmh=150)
        self.assertEqual(out[:2].tolist(), [1.0, 0.0])
        self.assertTrue(np.isnan(out[2]) and np.isnan(out[3]))


if __name__ == "__main__":
    unittest.main()

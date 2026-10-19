import unittest
from datetime import datetime

import numpy as np
import pyarrow as pa

from hfcev.core import gps_scale


def gps(rows):
    return pa.table({
        "time": pa.array([r[0] for r in rows], pa.timestamp("us")),
        "LAT": [r[1] for r in rows], "LATD": [r[2] for r in rows],
        "LON": [r[3] for r in rows], "LOND": [r[4] for r in rows],
    })


class Classify(unittest.TestCase):
    def test_classes(self):
        lat = np.array([np.nan, 0.0, 0.0, 36.5, 3630.12, 3690.0, 36.5, 99999.0])
        lon = np.array([111.0, 0.0, 111.0, 111.2, 11112.5, 11112.5, 11112.5, 111.0])
        names = [gps_scale.CLASSES[i] for i in gps_scale.classify(lat, lon)]
        self.assertEqual(names, ["nonfinite", "zero_pair", "single_axis_zero", "dd_range",
                                 "large_pair_dmm_valid", "large_pair_scaled100_only",
                                 "mixed_axis_scale", "mixed_axis_scale"])


class Restore(unittest.TestCase):
    day = datetime(2024, 5, 7, 8)

    def test_only_small_values_on_audited_dates_change(self):
        table = gps([(self.day, 36.501, "N", 111.209, "E"), (self.day, 3630.1, "N", 11112.5, "E"),
                     (self.day, 0.0, "N", 0.0, "E")])
        out, changed, days = gps_scale.restore_scale(table, ["2024-05-07"])
        self.assertEqual(list(changed), [0])
        self.assertEqual(days, {"2024-05-07": 1})
        np.testing.assert_allclose(out["LAT"].to_numpy(), [3650.1, 3630.1, 0.0])
        np.testing.assert_allclose(out["LON"].to_numpy(), [11120.9, 11112.5, 0.0])
        self.assertEqual(out.schema, table.schema)

    def test_small_value_outside_audited_dates_raises(self):
        table = gps([(datetime(2025, 1, 1), 36.501, "N", 111.209, "E")])
        with self.assertRaises(ValueError):
            gps_scale.restore_scale(table, ["2024-05-07"])

    def test_invalid_hemisphere_raises(self):
        table = gps([(self.day, 36.501, "X", 111.209, "E")])
        with self.assertRaises(ValueError):
            gps_scale.restore_scale(table, ["2024-05-07"])


if __name__ == "__main__":
    unittest.main()

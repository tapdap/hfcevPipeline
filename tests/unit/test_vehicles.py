import tempfile
import unittest
from pathlib import Path

import numpy as np

from hfcev.core import vehicles
from hfcev.core.clock import NAT

HEADER = "vehicle_id,tbox,unit,valid_from,valid_to,source,status\n"


def write(text: str) -> Path:
    f = tempfile.NamedTemporaryFile("w", suffix=".csv", delete=False)
    f.write(HEADER + text)
    f.close()
    return Path(f.name)


class ReadMap(unittest.TestCase):
    def test_confirmed_rows_ordered_by_unit(self):
        p = write("V1,B,2,2025-01-01,,record,confirmed\nV1,A,1,,2025-01-01,record,confirmed\nV2,C,1,,,ledger,confirmed\n"
                  "V9,D,1,,,data,candidate\n")
        out = vehicles.read_map(p, ["A", "B", "C"])
        self.assertEqual([u.tbox for u in out["V1"]], ["A", "B"])
        self.assertEqual(out["V1"][0].valid_from, NAT)
        self.assertNotIn("V9", out)                      # candidates are not applied

    def test_tbox_moved_between_vehicles(self):
        p = write("V1,A,1,,2025-01-01,record,confirmed\nV2,A,1,2025-03-01,,record,confirmed\n")
        out = vehicles.read_map(p, ["A"])
        self.assertEqual([u.vehicle_id for u in vehicles.by_tbox(out)["A"]], ["V1", "V2"])

    def test_rejects_overlap_duplicates_and_cohort_mismatch(self):
        with self.assertRaises(ValueError):              # open end before a later unit
            vehicles.read_map(write("V1,A,1,,,r,confirmed\nV1,B,2,2025-01-01,,r,confirmed\n"), ["A", "B"])
        with self.assertRaises(ValueError):
            vehicles.read_map(write("V1,A,1,,,r,confirmed\nV2,A,1,,,r,confirmed\n"), ["A"])
        with self.assertRaises(ValueError):
            vehicles.read_map(write("V1,A,1,,,r,confirmed\n"), ["A", "B"])


class Interval(unittest.TestCase):
    def test_inside_half_open(self):
        inst = vehicles.Installation("V1", "A", 1, 10, 20, "r")
        t = np.array([NAT, 9, 10, 19, 20])
        self.assertEqual(vehicles.inside(t, inst).tolist(), [False, False, True, True, False])

    def test_handover(self):
        day = 86_400_000_000
        prev = dict(t=0, km=1000.0, gps_t=0, lat=36.0, lon=111.0)
        nxt = dict(t=2 * day, km=1500.0, gps_t=2 * day, lat=36.0, lon=111.01)
        h = vehicles.handover(prev, nxt, 400)
        self.assertEqual((h["gap_days"], h["km_delta"], h["km_per_day"], h["odometer_consistent"]), (2.0, 500.0, 250.0, True))
        self.assertAlmostEqual(h["gps_displacement_km"], 0.9, delta=0.05)
        self.assertFalse(vehicles.handover(prev, dict(nxt, km=900.0), 400)["odometer_consistent"])


if __name__ == "__main__":
    unittest.main()

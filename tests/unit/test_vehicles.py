import tempfile
import unittest
from pathlib import Path

import numpy as np
import pandas as pd

from hfcev.core import vehicles
from hfcev.core.clock import NAT

HEADER = "vehicle_id,tbox,unit,valid_from,valid_to,source,status\n"


def write(text: str) -> Path:
    f = tempfile.NamedTemporaryFile("w", suffix=".csv", delete=False)
    f.write(HEADER + text)
    f.close()
    return Path(f.name)


S = 1_000_000
DAY = 86_400 * S


def summary(t, keep, segment=None, gps_t=(), events=None):
    n = len(t)
    op = dict(time=np.array(t, np.int64), segment=np.zeros(n, int) if segment is None else np.array(segment),
              FcVol=np.full(n, 300.0), FcCur=np.full(n, 100.0), km_increment=np.ones(n),
              km_kind=np.array(["in_segment"] * n, dtype=object), h2_increment=np.ones(n),
              h2_attributed=np.ones(n, bool), aligned=np.zeros(n, bool))
    g = len(gps_t)
    gps = dict(time=np.array(gps_t, np.int64), usable=np.ones(g, bool), fix_status=np.array(["valid"] * g, dtype=object),
               step_km=np.full(g, 0.1), speed_kmh=np.full(g, 50.0))
    ev = {k: np.array(v, np.int64) for k, v in (events or {}).items()}
    ev = {k: ev.get(k, np.array([], np.int64)) for k in ("fc_starts", "trips", "refuels")}
    return vehicles.daily_summary(op, np.array(keep, bool), gps, ev, 1.0, 150.0,
                                  ["in_segment", "in_segment_delayed", "across_gap"], ["no_fix", "no_fix_inferred"])


class DailySummary(unittest.TestCase):
    def test_shared_day_is_split_not_duplicated(self):
        # one TBOX, four records on one day; the boundary falls between the 2nd and 3rd record
        t = [DAY + 0, DAY + 5 * S, DAY + 10 * S, DAY + 15 * S]
        a, b = summary(t, [1, 1, 0, 0]), summary(t, [0, 0, 1, 1])
        self.assertEqual((a["records"].sum(), b["records"].sum()), (2, 2))
        self.assertEqual((a["active_s"].sum(), b["active_s"].sum()), (5.0, 5.0))   # 5->10 s crosses the boundary
        self.assertEqual(a["km_qualified"].sum() + b["km_qualified"].sum(), 4.0)
        self.assertAlmostEqual(a["energy_kwh"].sum(), 30.0 * 5 / 3600)

    def test_no_row_for_a_day_without_kept_records(self):
        # earlier installation ends at the first record of day 2: it keeps no day-2 contribution
        t = [DAY + 3600 * S, 2 * DAY + 3600 * S, 2 * DAY + 3605 * S]
        a = summary(t, [1, 0, 0], gps_t=[DAY + 3600 * S], events={"trips": [DAY + 3600 * S]})
        self.assertEqual(a["day"].tolist(), [pd.Timestamp("1970-01-02")])
        self.assertEqual((int(a["records"].iloc[0]), int(a["gps_fixes"].iloc[0]), int(a["trips"].iloc[0])), (1, 1, 1))

    def test_interval_rules(self):
        t = [0, 5 * S, 70 * S, 75 * S]          # 65 s gap is not an interval; segment change neither
        d = summary(t, [1, 1, 1, 1], segment=[0, 0, 0, 1])
        self.assertEqual(d["active_s"].sum(), 5.0)
        self.assertEqual(d["records"].dtype, np.int64)
        self.assertTrue(summary([], []).empty)


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

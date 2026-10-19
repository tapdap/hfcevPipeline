"""Stages 6-7 and the statistics on the synthetic sample.

Sample map: V001 = TBOX 1 (until 2024-05-08) then TBOX 3 (removed 2024-05-08 10:00:20); V002 = TBOX 2.
"""
import json
import tempfile
import unittest
from pathlib import Path

import numpy as np
import pandas as pd

from hfcev.validation import stats

from hfcev.io import read_json, sha256_file
from hfcev.runner import run_stage
from hfcev.stages import get
from tests.support import sample_config


class SampleRun(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.cfg = sample_config(Path(cls.tmp.name))
        for stage in ("s1", "s2", "s3", "s4", "s5"):
            run_stage(get(stage), cls.cfg, log=lambda _: None)
        cls.s6 = run_stage(get("s6"), cls.cfg, log=lambda _: None)
        cls.s7 = run_stage(get("s7"), cls.cfg, log=lambda _: None)
        cls.st = run_stage(get("stats"), cls.cfg, log=lambda _: None)
        cls.v = read_json(cls.cfg.layer("L6_vehicles") / "_manifest.json")["devices"]
        cls.rel = cls.cfg.layer("L7_release")

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def test_vehicles_assembled(self):
        self.assertEqual((self.s6["processed"], self.s6["failed"]), (2, []))
        u = self.v["V001"]["units"]
        self.assertEqual([x["records"] for x in u], [6, 5])
        self.assertEqual(u[1]["excluded_after_removal"], 3)          # records at/after 10:00:20
        self.assertEqual(len(self.v["V001"]["handovers"]), 1)
        op = pd.read_parquet(self.cfg.layer("L6_vehicles") / "operation" / "V001.parquet")
        self.assertTrue(op["time"].is_monotonic_increasing)
        self.assertEqual(sorted(op.groupby("unit")["segment"].unique().map(tuple)), [(0,), (1,)])

    def test_gps_links_point_at_matching_fixes(self):
        op = pd.read_parquet(self.rel / "operation" / "V001.parquet")
        gps = pd.read_parquet(self.rel / "gps" / "V001.parquet")
        x = op.dropna(subset=["gps_index"])
        dt = (gps["time"].to_numpy()[x["gps_index"].astype(int)] - x["time"].to_numpy()) / pd.Timedelta(seconds=1)
        self.assertTrue(((dt - x["gps_offset_s"]).abs() < 1e-6).all())

    def test_release_invariants_and_privacy(self):
        inv = json.loads((self.rel / "invariants.json").read_text())
        self.assertTrue(inv["ok"])
        self.assertEqual(inv["operation_conservation"]["excluded_outside_installation"], 3)
        gps = pd.read_parquet(self.rel / "gps" / "V001.parquet")   # coordinates rounded to 2 decimals
        for c in ("lat", "lon"):
            v = gps[c].dropna().to_numpy()
            self.assertTrue(len(v) and np.allclose(v, np.round(v, 2)))
        op = pd.read_parquet(self.rel / "operation" / "V001.parquet")
        self.assertFalse({"sub_state", "FaultLevel", "FaultCode"} & set(op.columns))   # withheld vendor codes
        self.assertIn("main_state", op.columns)
        trips = pd.read_parquet(self.rel / "trips.parquet")
        self.assertNotIn("fault_records", trips.columns); self.assertIn("fault_state_records", trips.columns)
        manifest = json.loads((self.rel / "MANIFEST.json").read_text())
        self.assertFalse(any("QLKJ" in f["path"] for f in manifest["files"]))
        for f in manifest["files"]:   # the manifest describes the files as written (README included)
            self.assertEqual(f["sha256"], sha256_file(self.rel / f["path"]), f["path"])
        self.assertTrue((self.rel / "dictionary.csv").exists() and any((self.rel / "sample").iterdir()))

    def test_stats_accounting_conserves_rows(self):
        a = pd.read_parquet(self.cfg.layer(get("stats").LAYER) / "accounting.parquet")
        for (vid, stream), g in a[a["stream"].isin(["operation", "gps"])].groupby(["vehicle_id", "stream"]):
            r = dict(zip(g["step"], g["rows"]))
            retired = sum(v for k, v in r.items() if k.startswith("1_"))
            self.assertEqual(r["0_input"] - retired, r["2_harmonized"], (vid, stream))
            self.assertEqual(r["2_harmonized"] + r["3_log_additions"] - r["4_excluded_outside_installation"], r["5_released"], (vid, stream))

    def test_added_stats_conserve_counts(self):
        self.assertEqual(self.st["failed"], [])
        out = self.cfg.layer(stats.LAYER)
        clock = pd.read_parquet(out / "clock_lag.parquet")
        self.assertEqual(list(clock), ["kind", "bin_left", "bin_right", "count"])
        totals = clock.groupby("kind")["count"].sum()
        op = pd.concat([pd.read_parquet(p) for p in sorted((self.rel / "operation").glob("*.parquet"))])
        for kind, column in (("receipt_minus_time", "time"), ("receipt_minus_device", "time_device")):
            self.assertEqual(totals[kind], op[["time_received_ms", column]].notna().all(axis=1).sum())
        signed = pd.read_parquet(out / "clock_lag_signed.parquet")
        self.assertEqual(list(signed), ["bin_left", "bin_right", "count"])
        self.assertEqual(signed["count"].sum(), totals["receipt_minus_time"])
        additions = pd.read_parquet(out / "log_additions_monthly.parquet")
        for stream in ("operation", "gps"):
            source = pd.concat([pd.read_parquet(p) for p in (self.rel / stream).glob("*.parquet")])
            expected_count = (source["source"] == "log_addition").sum()
            self.assertEqual(additions.loc[additions.stream == stream, "count"].sum(), expected_count)
        spans = pd.read_parquet(out / "unit_span.parquet")
        self.assertEqual(len(spans), len(op.groupby(["vehicle_id", "unit"])))
        self.assertTrue((spans.first_time <= spans.last_time).all())
        daily = pd.read_parquet(out / "odometer_daily.parquet")
        self.assertEqual(list(daily), ["vehicle_id", "unit", "day", "km_min", "km_max", "records"])
        positive = op[op["TotKM"] > 0].copy()
        positive["day"] = positive["time"].dt.strftime("%Y-%m-%d")
        expected = positive.groupby(["vehicle_id", "unit", "day"])["TotKM"].agg(
            km_min="min", km_max="max", records="size").reset_index()
        pd.testing.assert_frame_equal(daily, expected)
        self.assertEqual(daily["records"].sum(), len(positive))
        correction = pd.read_parquet(out / "correction.parquet")
        self.assertEqual(list(correction), ["vehicle_id", *stats.CORRECTION_COUNTS])
        s4 = read_json(self.cfg.layer("L4_quality") / "_manifest.json")["devices"]
        for key in stats.CORRECTION_COUNTS:
            self.assertEqual(correction[key].sum(), sum(
                r["correction"].get(key, r["correction"].get("verification", {}).get(key, 0)) for r in s4.values()))
        self.assertTrue((correction["stored"] == correction["reconstructed"] + correction["not_reconstructable"]).all())
        summary = pd.read_parquet(out / "segments_summary.parquet")
        self.assertEqual(list(summary), ["kind", "label", "bin_left", "bin_right", "count", "duration_s"])
        for kind in ("segments", "gaps"):
            source = pd.read_parquet(self.rel / f"{kind}.parquet")
            result = summary[summary["kind"] == kind]
            self.assertEqual(result["count"].sum(), len(source))
            self.assertAlmostEqual(result["duration_s"].sum(), source["duration_s"].sum() if len(source) else 0)
            if kind == "gaps" and len(source):
                for label, frame in source.groupby("label"):
                    bins = result[result["label"] == label]
                    self.assertEqual(bins["count"].sum(), len(frame))
                    self.assertAlmostEqual(bins["duration_s"].sum(), frame["duration_s"].sum())

    def test_segment_summary_labels_and_tails(self):
        with tempfile.TemporaryDirectory() as tmp:
            rel = Path(tmp)
            pd.DataFrame({"duration_s": [0., 1., 86400., 90000.]}).to_parquet(rel / "segments.parquet")
            pd.DataFrame({"duration_s": [61., 90., 90000.], "label": ["no_log_files", "no_log_files", "device_silent_in_logs"]}).to_parquet(rel / "gaps.parquet")
            summary = stats._segments_summary(rel)
            groups = summary.groupby(["kind", "label"])[["count", "duration_s"]].sum()
            self.assertEqual(groups.loc[("segments", "all")].tolist(), [4, 176401])
            self.assertEqual(groups.loc[("gaps", "no_log_files")].tolist(), [2, 151])
            self.assertEqual(groups.loc[("gaps", "device_silent_in_logs")].tolist(), [1, 90000])

    def test_clock_bins_boundaries_and_missing(self):
        lag = np.array([-.001, 0, .049, .05, .999, 1, 1.999, 2, 2.001, np.nan])
        result = stats._binned(lag, stats.RECEIPT_LAG_BINS)
        self.assertEqual(result["count"].sum(), 9)
        self.assertEqual(result.iloc[0]["count"], 1)
        self.assertEqual(result.iloc[-1]["count"], 1)
        self.assertEqual(result.iloc[1]["count"], 2)
        self.assertEqual(result.iloc[-2]["count"], 2)
        self.assertEqual(result.loc[(result.bin_left >= 0) & (result.bin_right <= 1), "count"].sum(), 4)
        lag = np.array([-61, -60, -.001, 0, .001, 86400, 86401, np.nan])
        result = stats._binned(lag, stats.DEVICE_LAG_BINS)
        self.assertEqual(result["count"].sum(), 7)
        self.assertEqual(result.iloc[0]["count"], 1)
        self.assertEqual(result.iloc[-1]["count"], 1)
        op = pd.DataFrame({"time": pd.to_datetime(["2025-01-01"] * 3),
                           "time_received_ms": pd.to_datetime(["2025-01-01", None, "2025-01-01"]),
                           "time_device": pd.to_datetime(["2025-01-01", "2025-01-01", None])})
        totals = stats._clock_lag(op).groupby("kind")["count"].sum()
        self.assertEqual(totals.to_dict(), {"receipt_minus_time": 2, "receipt_minus_device": 1})
        self.assertEqual(stats._clock_lag(op.iloc[:0])["count"].sum(), 0)

    def test_rerun_skips(self):
        again = run_stage(get("s7"), self.cfg, log=lambda _: None)
        self.assertEqual((again["processed"], again["skipped"]), (0, 2))


if __name__ == "__main__":
    unittest.main()

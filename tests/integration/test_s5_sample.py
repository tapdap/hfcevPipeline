"""Stage 5 on the synthetic sample (device 3 carries log-linked GPS fixes with `result`)."""
import tempfile
import unittest
from pathlib import Path

import pyarrow.parquet as pq

from hfcev.io import read_json
from hfcev.runner import run_stage
from hfcev.validation import s5_report
from hfcev.stages import s1_harmonize as s1, s2_logs as s2, s3_timeline as s3, s4_quality as s4, s5_derived as s5
from tests.support import sample_config

D3 = "QLKJ9999999990003"
OUTPUTS = ("gps_track", "alignment", "fc_runs", "trips", "refuels", "daily", "monthly")


class SampleRun(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.cfg = sample_config(Path(cls.tmp.name))
        for stage in (s1, s2, s3, s4):
            run_stage(stage, cls.cfg, log=lambda _: None)
        cls.summary = run_stage(s5, cls.cfg, log=lambda _: None)
        cls.out = cls.cfg.layer(s5.LAYER)
        cls.r = read_json(cls.out / "_manifest.json")["devices"][D3]

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def test_all_devices_processed_with_all_outputs(self):
        self.assertEqual((self.summary["processed"], self.summary["failed"]), (3, []))
        for name in OUTPUTS:
            self.assertTrue((self.out / name / f"{D3}.parquet").exists(), name)

    def test_fix_status_from_result_and_proxy(self):
        self.assertEqual(self.r["fix_status"], {"valid": 2, "valid_assumed": 1})
        self.assertEqual(self.r["fix_source"], {"result": 2, "proxy": 1})
        track = pq.read_table(self.out / "gps_track" / f"{D3}.parquet").to_pylist()
        self.assertEqual([(t["origin"], t["fix_source"]) for t in track],
                         [("stored", "result"), ("stored", "proxy"), ("addition", "result")])

    def test_alignment_rows_follow_operation_stream(self):
        rows = pq.read_table(self.out / "alignment" / f"{D3}.parquet").to_pylist()
        self.assertEqual(len(rows), self.r["records"])
        self.assertEqual(sum(r["status"] == "matched" for r in rows), self.r["alignment"]["main"]["hit"])
        self.assertTrue(all(abs(r["offset_s"]) <= 10 for r in rows if r["status"] == "matched"))
        u = self.r["alignment"]["unmatched"]
        self.assertEqual(self.r["alignment"]["main"]["hit"] + sum(u.values()), self.r["records"])

    def test_trip_and_fc_run(self):
        trips = pq.read_table(self.out / "trips" / f"{D3}.parquet").to_pylist()
        self.assertEqual(len(trips), 1)
        self.assertEqual((trips[0]["records"], trips[0]["fc_runs"], trips[0]["moving"]), (8, 1, False))
        self.assertAlmostEqual(trips[0]["km_qualified"], 0.7, places=6)

    def test_report_builds(self):
        devices = read_json(self.out / "_manifest.json")["devices"]
        report, monthly = s5_report.build(devices, self.out)
        self.assertEqual(report["devices"], 3)
        self.assertEqual(report["records"], sum(d["records"] for d in devices.values()))
        self.assertEqual(report["device_months"]["devices"], 3)
        for lang in s5_report.TEXT:
            self.assertIn("CAN", s5_report.markdown(report, lang))

    def test_rerun_skips(self):
        again = run_stage(s5, self.cfg, log=lambda _: None)
        self.assertEqual((again["processed"], again["skipped"]), (0, 3))


if __name__ == "__main__":
    unittest.main()

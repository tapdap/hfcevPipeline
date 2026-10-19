"""Stage 4 on the synthetic sample (devices 1-3; device 3 carries the stage-3 scenarios)."""
import tempfile
import unittest
from pathlib import Path

import pyarrow.parquet as pq

from hfcev.io import read_json
from hfcev.runner import run_stage
from hfcev.stages import s1_harmonize as s1, s2_logs as s2, s3_timeline as s3, s4_quality as s4
from tests.support import sample_config

D3 = "QLKJ9999999990003"


class SampleRun(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.cfg = sample_config(Path(cls.tmp.name))
        for stage in (s1, s2, s3):
            run_stage(stage, cls.cfg, log=lambda _: None)
        cls.summary = run_stage(s4, cls.cfg, log=lambda _: None)
        cls.out = cls.cfg.layer(s4.LAYER)
        cls.r = read_json(cls.out / "_manifest.json")["devices"][D3]

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def test_all_devices_processed(self):
        self.assertEqual((self.summary["processed"], self.summary["failed"]), (3, []))

    def test_one_reporting_segment_with_stored_and_added_records(self):
        # stored 10:00:00-10:00:30 plus additions at 10:00:15/20/35: no gap above 60 s
        self.assertEqual(self.r["records"], dict(total=8, stored=5, additions=3))
        seg = pq.read_table(self.out / "segments" / f"{D3}.parquet").to_pylist()
        self.assertEqual(len(seg), 1)
        self.assertEqual((seg[0]["records"], seg[0]["additions"], seg[0]["duration_s"], seg[0]["short"]), (8, 3, 35.0, True))
        self.assertEqual(self.r["gaps"]["count"], 0)

    def test_flags_and_log_comparison(self):
        flags = pq.read_table(self.out / "operation_flags" / f"{D3}.parquet").to_pylist()
        self.assertEqual(len(flags), 8)
        self.assertEqual({f["outside_encodable_mask"] for f in flags}, {0})
        comparison = self.r["log_comparison"]
        self.assertEqual(comparison["FcVol"]["equal"], 5)       # linked stored records equal their log values
        self.assertFalse(self.r["correction"]["applied"])      # sample export has no HyBottleP/HighPPMNum

    def test_gps_flags(self):
        g = self.r["gps"]
        self.assertEqual((g["fixes"], g["stored"], g["additions"]), (3, 2, 1))
        self.assertEqual(g["coordinate_class"], {"large_pair_dmm_valid": 3})

    def test_rerun_skips(self):
        again = run_stage(s4, self.cfg, log=lambda _: None)
        self.assertEqual((again["processed"], again["skipped"]), (0, 3))


if __name__ == "__main__":
    unittest.main()

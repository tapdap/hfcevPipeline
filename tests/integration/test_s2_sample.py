"""Stage 2 end to end on the synthetic logs (see sample_data/make_sample.py for the scenarios)."""
import tempfile
import unittest
from pathlib import Path

import pyarrow.parquet as pq

from hfcev.core.logs import UNASSIGNED
from hfcev.io import read_json
from hfcev.runner import run_stage
from hfcev.stages import s2_logs as s2
from tests.support import sample_config

D1, D2, D3 = "QLKJ9999999990001", "QLKJ9999999990002", "QLKJ9999999990003"


class SampleRun(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.cfg = sample_config(Path(cls.tmp.name))
        cls.summary = run_stage(s2, cls.cfg, log=lambda _: None)
        cls.out = cls.cfg.layer(s2.LAYER)
        cls.records = read_json(cls.out / "_manifest.json")["devices"]
        cls.counts = read_json(cls.out / "_pass1_summary.json")["counts"]

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def test_accounting(self):
        c = self.counts
        # device 3 (stage-3 scenarios) adds one file of 10 operation messages (30 frames, 1 unknown) and 5 GPS fixes
        self.assertEqual((c["input_files"], c["log_blocks:message"], c["rows:messages"]), (4, 25, 25))
        self.assertEqual((c["rows:operation"], c["rows:gps"], c["rows:rejects"], c["rows:can_frames"]), (14, 8, 8, 37))
        self.assertEqual((c["frames:decoded"], c["frames:unknown_can_id"]), (35, 2))
        self.assertEqual(sorted(self.records), [D1, D2, D3, UNASSIGNED])
        self.assertEqual(sum(r["messages"]["rows"] for r in self.records.values()), 25)

    def test_identity(self):
        self.assertEqual(self.records[D1]["messages"]["identity"], {"topic_agrees": 3, "topic_sn_mismatch": 1})
        self.assertEqual(self.records[UNASSIGNED]["messages"]["identity"], {"invalid_or_missing_sn": 1})

    def test_rejects(self):
        reasons = pq.read_table(self.out / "rejects.parquet")["reason"].to_pylist()
        self.assertEqual(reasons, ["topic_sn_mismatch", "invalid_or_missing_sn", "invalid_or_truncated_json",
                                   "unframed_prefix", "invalid_frame_fields", "unsupported_gps_shape",
                                   "unrecognised_json_prefix", "missing_data"])

    def test_operation_rows_in_log_order(self):
        op = pq.read_table(self.out / "operation" / f"{D1}.parquet").to_pylist()
        self.assertEqual([r["FcVol"] for r in op], [600.0, None])
        self.assertEqual(op[1]["legacy_conflicts"], ["FcVol"])
        self.assertEqual(self.records[D1]["operation"]["normal_frames"], 5)
        offsets = [r["byte_offset"] for r in op]
        self.assertEqual(offsets, sorted(offsets))

    def test_gps_without_time(self):
        gps = pq.read_table(self.out / "gps" / f"{D2}.parquet").to_pylist()
        self.assertEqual(len(gps), 1)
        self.assertIsNone(gps[0]["device_time"])
        self.assertEqual(gps[0]["latitude_raw"], 3740.0)

    def test_rerun_skips_everything(self):
        again = run_stage(s2, self.cfg, log=lambda _: None)
        self.assertEqual((again["processed"], again["skipped"]), (0, 4))


if __name__ == "__main__":
    unittest.main()

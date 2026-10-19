"""Stage 1 end to end on the synthetic sample (see sample_data/make_sample.py for the scenarios)."""
import tempfile
import unittest
from pathlib import Path

import numpy as np
import pyarrow.parquet as pq

from hfcev.io import read_json
from hfcev.runner import run_stage
from hfcev.stages import s1_harmonize as s1
from tests.support import sample_config

D1, D2 = "QLKJ9999999990001", "QLKJ9999999990002"


class SampleRun(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.cfg = sample_config(Path(cls.tmp.name))
        cls.summary = run_stage(s1, cls.cfg, log=lambda _: None)
        cls.out = cls.cfg.layer(s1.LAYER)
        cls.records = read_json(cls.out / "_manifest.json")["devices"]

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def test_operation_dispositions(self):
        r = self.records[D1]["can_run"]
        self.assertEqual((r["input_rows"], r["before_window"], r["after_window"]), (12, 1, 1))
        self.assertEqual((r["rows"], r["can_rows"], r["run_rows"]), (6, 5, 1))
        self.assertEqual(r["retired_reasons"], {"can_exact_duplicate": 1, "run_conflict_prefer_can": 1,
                                                "run_exact_duplicate": 1, "run_matches_can_shared_fields": 1})
        self.assertEqual((r["duplicate_rows"], r["old_run_conflict_rows"]), (3, 1))
        self.assertEqual(r["retained_same_time_variant_rows"], 2)

    def test_parts_month_boundary_and_retyped_column(self):
        r = self.records[D2]["can_run"]
        self.assertEqual((r["input_rows"], r["rows"], r["can_rows"], r["run_rows"], r["duplicate_rows"]), (6, 5, 4, 1, 1))
        self.assertEqual(r["daily"], {"2024-05-31": 2, "2024-06-01": 3})

    def test_output_values_and_schema(self):
        table = pq.read_table(self.out / "can_run" / f"{D1}.parquet")
        self.assertFalse({"ActPower", "Con", "CoolOutTarTemp"} & set(table.column_names))
        self.assertEqual(table["FcVol"].to_pylist(), [600.0, 601.0, 602.0, 650.0, 603.0, 604.0])
        self.assertEqual(table["source_stream"].to_pylist(), ["can"] * 5 + ["run"])
        self.assertEqual(table["same_time_variants"].to_pylist(), [1, 1, 2, 2, 1, 1])
        retired = pq.read_table(self.out / "retired" / "can_run" / f"{D1}.parquet")
        self.assertEqual(len(retired), 4)

    def test_gps(self):
        r = self.records[D1]["gps"]
        self.assertEqual((r["input_rows"], r["before_window"], r["duplicate_rows"], r["rows"]), (7, 1, 1, 5))
        self.assertEqual((r["scale_restored_rows"], r["retained_same_time_variant_rows"]), (1, 2))
        lat = pq.read_table(self.out / "gps" / f"{D1}.parquet")["LAT"].to_numpy()
        np.testing.assert_allclose(lat, [3630.1234, 3650.1, 0.0, 3630.2, 3630.3])
        self.assertEqual(self.records[D2]["gps"]["rows"], 3)

    def test_rerun_skips_and_force_is_reproducible(self):
        again = run_stage(s1, self.cfg, log=lambda _: None)
        self.assertEqual((again["processed"], again["skipped"]), (0, 3))
        before = {d: r["can_run"]["sha256"] for d, r in self.records.items()}
        forced = run_stage(s1, self.cfg, devices=[D1], force=True, log=lambda _: None)
        self.assertEqual(forced["processed"], 1)
        after = read_json(self.out / "_manifest.json")["devices"][D1]["can_run"]["sha256"]
        self.assertEqual(after, before[D1])


if __name__ == "__main__":
    unittest.main()

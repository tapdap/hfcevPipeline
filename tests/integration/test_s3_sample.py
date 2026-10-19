"""Stage 3 end to end on the synthetic sample (device 3 in sample_data/make_sample.py holds the scenarios)."""
import tempfile
import unittest
from datetime import datetime
from pathlib import Path

import pyarrow.parquet as pq

from hfcev.io import read_json
from hfcev.runner import run_stage
from hfcev.stages import s1_harmonize as s1, s2_logs as s2, s3_timeline as s3
from tests.support import sample_config

D1, D3 = "QLKJ9999999990001", "QLKJ9999999990003"


def t(text):
    return datetime.fromisoformat("2024-05-08 " + text)


class SampleRun(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.cfg = sample_config(Path(cls.tmp.name))
        for stage in (s1, s2):
            run_stage(stage, cls.cfg, log=lambda _: None)
        cls.summary = run_stage(s3, cls.cfg, log=lambda _: None)
        cls.out = cls.cfg.layer(s3.LAYER)
        cls.records = read_json(cls.out / "_manifest.json")["devices"]
        cls.r = cls.records[D3]

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def table(self, kind, device=D3):
        return pq.read_table(self.out / kind / f"{device}.parquet").to_pylist()

    def test_all_devices_processed(self):
        self.assertEqual(self.summary["failed"], [])
        self.assertEqual(self.summary["processed"], 3)

    def test_stored_records_keep_receipt_time_and_gain_clocks(self):
        rows = self.table("operation")
        self.assertEqual([r["time"] for r in rows], [t("10:00:00"), t("10:00:05"), t("10:00:10"), t("10:00:10"),
                                                      t("10:00:30")])
        self.assertEqual([r["match_status"] for r in rows], ["unique_core_candidate"] * 5)
        self.assertEqual([r["time_received_ms"] for r in rows],
                         [t("10:00:00.250"), t("10:00:05.250"), t("10:00:10.100"), t("10:00:10.700"), t("10:00:30.200")])
        self.assertEqual([r["time_device"] for r in rows], [t("10:00:00"), t("10:00:05"), t("10:00:09"), t("10:00:10"),
                                                             t("10:00:30")])
        self.assertEqual({r["time_device_status"] for r in rows}, {"verified"})
        self.assertEqual(self.r["stored"]["device_time_differs_from_time"], 1)

    def test_same_time_pair_is_ordered_by_both_clocks(self):
        self.assertEqual(self.r["groups"], dict(same_time_groups=1, same_time_rows=2, rows_with_received_ms=2,
                                                rows_with_device_time=2, ordered_by_received_ms=1,
                                                ordered_by_device_time=1))

    def test_additions_at_receipt_second_with_device_clock_status(self):
        # every addition is published; a failed device clock only leaves time_device empty
        rows = self.table("additions")
        self.assertEqual([r["time"] for r in rows], [t("10:00:15"), t("10:00:20"), t("10:00:35")])
        self.assertEqual([r["time_received_ms"] for r in rows], [t("10:00:15.300"), t("10:00:20.400"), t("10:00:35.200")])
        self.assertEqual([r["time_device_status"] for r in rows], ["verified", "clock_lag_out_of_range", "clock_out_of_order"])
        self.assertEqual([r["time_device"] for r in rows], [t("10:00:15"), None, None])
        self.assertEqual([r["FcVol"] for r in rows], [415.0, 420.0, 435.0])
        self.assertEqual({r["same_time_variants"] for r in rows}, {1})
        self.assertEqual((self.r["additions"]["rows"], self.r["additions"]["rows_released"]), (3, 3))

    def test_message_dispositions(self):
        self.assertEqual(self.r["disposition"], {"linked_to_stored_record": 5, "added": 3, "not_candidate:quality": 1,
                                                 "not_candidate:missing_raw_core": 1})
        events = self.table("log_events")
        self.assertEqual([e["clock_status"] for e in events][:2], ["clock_out_of_order", "verified"])  # no previous

    def test_earlier_pool_kept_for_regression(self):
        # the reset clock (reported 2020) falls outside the window by reported time, inside by receipt time
        self.assertEqual(self.r["candidates"], dict(core_absent_envelopes=3, addition_candidates=2, quality_flagged=1,
                                                    repeated_point_envelopes=0, unique_provenance=5))
        self.assertEqual((self.r["additions"]["in_earlier_pool"], self.r["additions"]["earlier_pool_only"]), (2, 0))

    def test_no_device_time_collision(self):
        # V4 (clock reuse) and first-occurrence pooling already exclude shared device times in this sample
        self.assertEqual(self.r["clock"]["collisions"], 0)

    def test_odometer(self):
        timeline, receipt = self.r["odometer"]["stored_timeline"], self.r["odometer"]["receipt_order"]
        self.assertEqual(timeline["added_at_receipt_time"]["any_gap"], dict(tested=2, passed=2))
        self.assertEqual(timeline["added_at_device_time"]["any_gap"], dict(tested=1, passed=1))
        self.assertEqual(timeline["added_clock_lag_out_of_range_at_reported_time"]["any_gap"], dict(tested=0, passed=0))
        self.assertEqual(receipt["all_messages"], dict(tested=8, passed=8))
        device = self.r["odometer"]["device_axis"]
        self.assertEqual((device["stored"]["any_gap"], device["added"]["any_gap"]),
                         (dict(tested=3, passed=3), dict(tested=1, passed=1)))
        self.assertEqual(receipt["added:clock_lag_out_of_range"], dict(tested=1, passed=1))

    def test_gps_correspondence_and_additions(self):
        stored = self.table("gps")
        self.assertEqual([r["match_status"] for r in stored], ["linked", "no_log_fix"])
        self.assertEqual([r["time_received_ms"] for r in stored], [t("10:00:40.100"), None])
        added = self.table("gps_additions")
        self.assertEqual([(r["time"], r["LAT"], r["LATD"], r["time_received_ms"]) for r in added],
                         [(t("10:00:10"), 3641.01, "N", t("10:00:40.300"))])
        events = self.table("gps_log_events")
        self.assertEqual([e["disposition"] for e in events],
                         ["linked_to_stored_fix", "pending_same_time_different_coords", "added",
                          "pending_clock_lag_out_of_range", "pending_clock_out_of_order"])
        g = self.r["gps"]
        self.assertEqual((g["stored_rows"], g["stored_linked"], g["pool"], g["published"]), (2, 1, 3, 1))

    def test_devices_without_core_content(self):
        r = self.records[D1]
        self.assertEqual((r["stored"]["linked"], r["additions"]["rows"]), (0, 0))
        self.assertEqual(len(self.table("operation", D1)), 6)

    def test_rerun_skips_everything(self):
        again = run_stage(s3, self.cfg, log=lambda _: None)
        self.assertEqual((again["processed"], again["skipped"]), (0, 3))



class VerifiedOnlySetting(unittest.TestCase):
    """additions_require_verified_device_time = true publishes only additions with a verified device clock."""

    def test_only_verified_additions_published(self):
        import dataclasses
        with tempfile.TemporaryDirectory() as tmp:
            cfg = sample_config(Path(tmp))
            release = {**cfg.release, "s3_timeline": {**cfg.release["s3_timeline"],
                                                      "additions_require_verified_device_time": True}}
            cfg = dataclasses.replace(cfg, release=release)
            for stage in (s1, s2, s3):
                run_stage(stage, cfg, devices=[D3], log=lambda _: None)
            rows = pq.read_table(cfg.layer(s3.LAYER) / "additions" / f"{D3}.parquet").to_pylist()
            record = read_json(cfg.layer(s3.LAYER) / "_manifest.json")["devices"][D3]
        self.assertEqual([r["time_device_status"] for r in rows], ["verified"])
        self.assertEqual((record["additions"]["rows"], record["additions"]["rows_released"]), (3, 1))


if __name__ == "__main__":
    unittest.main()

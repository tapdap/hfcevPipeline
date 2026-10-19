"""Stages against the frozen earlier versions (private data; skipped when unavailable).

Keep configs/local.toml pointing at the real work directory after running the stages and set
HFCEV_REFERENCE_S1 / HFCEV_REFERENCE_S2 to the reference JSON files.
"""
import os
import unittest
from pathlib import Path

from hfcev import config
from hfcev.io import read_json
from hfcev.stages import s1_harmonize, s2_logs
from hfcev.validation.reference import compare, compare_files


def reference(name):
    path = os.environ.get(name)
    return read_json(path) if path and Path(path).exists() and config.DEFAULT_LOCAL.exists() else None


class FrozenVersions(unittest.TestCase):
    def check_devices(self, stage, ref):
        cfg = config.load()
        manifest = read_json(cfg.layer(stage.LAYER) / "_manifest.json", {"devices": {}})
        report = compare(manifest["devices"], ref)
        self.assertEqual(report["missing_devices"], [])
        self.assertEqual(report["mismatches"], [])
        self.assertEqual(report["totals_actual"], report["totals_expected"])
        return cfg

    @unittest.skipUnless(reference("HFCEV_REFERENCE_S1"), "needs HFCEV_REFERENCE_S1 and configs/local.toml")
    def test_s1_rows_dispositions_and_sha256(self):
        self.check_devices(s1_harmonize, reference("HFCEV_REFERENCE_S1"))

    @unittest.skipUnless(reference("HFCEV_REFERENCE_S2"), "needs HFCEV_REFERENCE_S2 and configs/local.toml")
    def test_s2_per_file_and_per_device_counts(self):
        ref = reference("HFCEV_REFERENCE_S2")
        cfg = self.check_devices(s2_logs, ref)
        layer = cfg.layer(s2_logs.LAYER)
        tasks = read_json(layer / "_pass1_summary.json")["tasks"]
        files = compare_files([read_json(layer / "_pass1" / t / "receipt.json") for t in tasks], ref["files"])
        self.assertEqual(files["mismatches"], [])
        self.assertEqual(files["files"], len(ref["files"]))


if __name__ == "__main__":
    unittest.main()

import unittest

import numpy as np
import pandas as pd

from hfcev.stages.s7_release import merge_fields


class MergeFields(unittest.TestCase):
    def test_target_filled_from_source_with_its_quality_bits(self):
        fields = ["a", "OLD", "NEW"]  # bits 0, 1, 2
        op = pd.DataFrame({
            "OLD": ["1.5", "0", None, None],
            "NEW": [np.nan, np.nan, 2.25, np.nan],
            # row 0: OLD present; row 1: OLD present and outside encodable; row 2: NEW present; row 3: neither
            "missing_mask": [0b100, 0b100, 0b010, 0b110],
            "outside_encodable_mask": [0, 0b010, 0, 0],
        })
        overlap = merge_fields(op, {"NEW": ["OLD"]}, fields)
        self.assertEqual(overlap, 0)
        self.assertEqual(op["NEW"].tolist()[:3], [1.5, 0.0, 2.25])
        self.assertTrue(np.isnan(op["NEW"].iloc[3]))
        self.assertEqual(((op["missing_mask"].to_numpy() >> 2) & 1).tolist(), [0, 0, 0, 1])
        self.assertEqual(((op["outside_encodable_mask"].to_numpy() >> 2) & 1).tolist(), [0, 1, 0, 0])

    def test_overlap_is_counted_and_target_kept(self):
        op = pd.DataFrame({"OLD": [1.0], "NEW": [2.0], "missing_mask": [0]})
        self.assertEqual(merge_fields(op, {"NEW": ["OLD"]}, ["OLD", "NEW"]), 1)
        self.assertEqual(op["NEW"].iloc[0], 2.0)


if __name__ == "__main__":
    unittest.main()

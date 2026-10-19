import unittest

import pandas as pd

from hfcev.core import dedup


def frame(rows):
    return pd.DataFrame(rows, columns=["time", "a", "b", "source_stream", "source_file", "source_row"])


class OperationDispositions(unittest.TestCase):
    def setUp(self):
        t = pd.Timestamp("2024-05-01 00:00:00")
        s = pd.Timedelta(seconds=5)
        self.df = frame([
            (t,     1.0, 9.0, "can", "can/x.parquet", 0),
            (t,     1.0, 9.0, "can", "can/x.parquet", 1),   # exact repeat -> duplicate
            (t + s, 2.0, 9.0, "can", "can/x.parquet", 2),
            (t + s, 3.0, 9.0, "can", "can/x.parquet", 3),   # same time, other value -> kept
            (t,     1.0, None, "run", "run.parquet", 0),     # shared field `a` equal -> match
            (t + s, 7.0, None, "run", "run.parquet", 1),     # shared field differs -> conflict
            (t + 2 * s, 4.0, None, "run", "run.parquet", 2),  # no CAN at this time -> kept
            (t + 2 * s, 4.0, None, "run", "run.parquet", 3),  # exact repeat -> duplicate
        ])

    def test_reasons(self):
        keep, reason = dedup.operation_dispositions(self.df, ["a", "b"], ["a"])
        self.assertEqual(list(reason), ["", dedup.CAN_DUPLICATE, "", "", dedup.RUN_MATCHES_CAN,
                                        dedup.RUN_CONFLICT, "", dedup.RUN_DUPLICATE])
        self.assertEqual(list(keep), [0, 2, 3, 6])  # ordered by time, then stream/file/row

    def test_first_occurrence_kept_regardless_of_input_order(self):
        shuffled = self.df.iloc[::-1].reset_index(drop=True)
        keep, _ = dedup.operation_dispositions(shuffled, ["a", "b"], ["a"])
        kept = shuffled.loc[keep, ["source_file", "source_row"]].values.tolist()
        self.assertEqual(kept, [["can/x.parquet", 0], ["can/x.parquet", 2], ["can/x.parquet", 3], ["run.parquet", 2]])


class GpsDispositions(unittest.TestCase):
    def test_exact_repeats_only(self):
        t = pd.Timestamp("2024-05-01")
        df = pd.DataFrame({"time": [t, t, t], "LAT": [1.0, 1.0, 2.0],
                           "source_file": ["g", "g", "g"], "source_row": [0, 1, 2]})
        keep, retired = dedup.gps_dispositions(df, ["LAT"])
        self.assertEqual(list(keep), [0, 2])
        self.assertEqual(list(retired), [1])
        self.assertEqual(list(dedup.same_time_variants(df.loc[keep, "time"])), [2, 2])


if __name__ == "__main__":
    unittest.main()

import unittest
from datetime import datetime

import pyarrow as pa

from hfcev.core import raw

REFERENCE = pa.schema([("id", pa.int64()), ("sn", pa.string()), ("time", pa.timestamp("us")),
                       ("v", pa.float64()), ("w", pa.float64())])


def table(sn, times, v=None):
    v = v if v is not None else pa.array([1.0] * len(times))
    return pa.table({"id": pa.array(range(len(times)), pa.int64()), "sn": sn,
                     "time": pa.array(times, pa.timestamp("us")), "v": v})


class ToUnified(unittest.TestCase):
    def test_provenance_ids_and_missing_fields(self):
        schema = raw.unified_schema(REFERENCE)
        t = table([" qlkj9999999990001 ", None], [datetime(2024, 5, 1), datetime(2024, 5, 2)])
        u = raw.to_unified(t, schema, "can", "can/z_x.parquet", 10)
        self.assertEqual(u.schema, schema)
        self.assertEqual(u["tbox"].to_pylist(), ["QLKJ9999999990001", raw.MISSING_DEVICE])
        self.assertEqual(u["source_row"].to_pylist(), [10, 11])
        self.assertEqual(u["w"].null_count, 2)          # absent in source -> null
        self.assertEqual(u["v"].to_pylist(), [1.0, 1.0])  # values untouched

    def test_all_null_string_column_is_retyped(self):
        t = table(["A"], [datetime(2024, 5, 1)], v=pa.nulls(1, pa.string()))
        u = raw.to_unified(t, raw.unified_schema(REFERENCE), "can", "f", 0)
        self.assertEqual(u.schema.field("v").type, pa.float64())

    def test_non_null_string_in_numeric_column_raises(self):
        t = table(["A"], [datetime(2024, 5, 1)], v=pa.array(["1.5"]))
        with self.assertRaises(ValueError):
            raw.to_unified(t, raw.unified_schema(REFERENCE), "can", "f", 0)


class Window(unittest.TestCase):
    def test_counts_and_bounds(self):
        times = [datetime(2024, 3, 31, 23, 59, 59), datetime(2024, 4, 1), datetime(2026, 5, 31, 23, 59, 59),
                 datetime(2026, 6, 1), None]
        t = pa.table({"time": pa.array(times, pa.timestamp("us"))})
        kept, counts = raw.window_filter(t, "2024-04-01T00:00:00", "2026-06-01T00:00:00")
        self.assertEqual(len(kept), 2)
        self.assertEqual(counts, {"null_time": 1, "before_window": 1, "after_window": 1})


if __name__ == "__main__":
    unittest.main()

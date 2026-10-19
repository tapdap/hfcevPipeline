import unittest

import pandas as pd

from hfcev.validation.stats import _stack_voltage


class StackVoltage(unittest.TestCase):
    def test_window_and_monthly_quartiles_per_unit(self):
        op = pd.DataFrame({
            "unit": [1, 1, 1, 1, 2],
            "FcCur": [85, 90, 91, 80, 85],          # 91 A outside the current window
            "CoolOutActTemp": [65, 75, 65, 59, 70],  # 59 degC outside the temperature window
            "FcVol": [350.0, 354.0, 300.0, 300.0, 340.0],
        })
        month = pd.Series(["2025-07", "2025-07", "2025-07", "2025-07", "2025-08"])
        t = _stack_voltage(op, month, {"current_a": [80, 90], "coolant_out_c": [60, 75]})
        self.assertEqual(t[["unit", "month", "records"]].values.tolist(), [[1, "2025-07", 2], [2, "2025-08", 1]])
        self.assertEqual(t["v_median"].tolist(), [352.0, 340.0])

    def test_missing_columns_give_empty_table(self):
        self.assertTrue(_stack_voltage(pd.DataFrame({"unit": [1]}), pd.Series(["2025-07"]), {}).empty)


if __name__ == "__main__":
    unittest.main()

import tempfile
import unittest
from pathlib import Path

from hfcev.core import dbc
from hfcev.core.messages import MessageDecoder
from tests.support import SAMPLE

FC, VCU, CELL = 0x18F00101, 0x18F00202, 0x18F00303
FC_BYTES = [112, 23, 220, 5, 200, 0, 0, 0]
BASE = dict(event_id="e", tbox="D1", log_time="2024-05-07 08:00:06.000", log_month="2024-05",
            source_index=0, byte_offset=0, end_offset=10)


class Dbc(unittest.TestCase):
    def setUp(self):
        self.messages = dbc.read_dbc(SAMPLE / "protocol" / "sample.dbc")

    def test_messages_scaling_and_sign(self):
        self.assertEqual(sorted(self.messages), [FC, VCU, CELL])  # non-bus placeholder ignored
        self.assertEqual(self.messages[FC].decode(bytes(FC_BYTES)), {"FcVol": 600.0, "FcCur": 50.0, "SOC": 80.0})
        self.assertEqual(self.messages[VCU].decode(bytes([0, 0, 0, 0, 0, 0, 246, 0]))["CabinTemp"], -10.0)

    def test_motorola_signal_is_refused(self):
        text = 'BO_ 100 M: 8 X\n SG_ s : 7|8@0+ (1,0) [0|255] "" X\n'
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "m.dbc"
            path.write_text(text)
            with self.assertRaises(ValueError):
                dbc.read_dbc(path)


class Decoder(unittest.TestCase):
    def setUp(self):
        self.decoder = MessageDecoder(dbc.read_dbc(SAMPLE / "protocol" / "sample.dbc"), ["FcVol", "FcCur", "veh_spd"])

    def test_operation_record(self):
        payload = dict(type="real_batch", time="2024-05-07 08:00:05",
                       data=[0, FC, *FC_BYTES, 0, 0x123, *range(8)], fault=["2024-05-07 08:00:04", 0, FC, *FC_BYTES])
        status, gps, op, rejects = self.decoder.decode(payload, BASE, "topic_agrees")
        self.assertEqual((status, gps, rejects), ("parsed", None, []))
        self.assertEqual((op["normal_frames"], op["fault_frames"], op["unknown_frames"]), (2, 1, 1))
        self.assertEqual((op["FcVol"], op["FcCur"], op["veh_spd"]), (600.0, 50.0, None))
        self.assertEqual(op["message_time_raw"], "2024-05-07 08:00:05")
        self.assertEqual(self.decoder.counts["clock_delta_count:can_frames"], 3)

    def test_conflicting_values_become_null(self):
        other = list(FC_BYTES)
        other[0] = 122
        payload = dict(type="real_batch", time="2024-05-07 08:00:05", data=[0, FC, *FC_BYTES, 0, FC, *other])
        _, _, op, _ = self.decoder.decode(payload, BASE, "topic_agrees")
        self.assertIsNone(op["FcVol"])
        self.assertEqual(op["FcCur"], 50.0)
        self.assertEqual(op["legacy_conflicts"], ["FcVol"])
        self.assertEqual(op["qualified_conflicts"], [dbc.qualified_name(FC, "FC_STATUS", "FcVol")])

    def test_rejects_keep_the_message(self):
        payload = dict(type="real_batch", time="t", data=[0, FC, 300, 0, 0, 0, 0, 0, 0, 0])
        status, _, op, rejects = self.decoder.decode(payload, BASE, "topic_agrees")
        self.assertEqual(status, "partial")
        self.assertEqual([(r["reason"], r["frame_index"]) for r in rejects], [("invalid_frame_fields", 0)])
        self.assertEqual(op["normal_frames"], 0)
        _, gps, _, rejects = self.decoder.decode(dict(type="real_gps", data=[1.0]), BASE, "topic_agrees")
        self.assertIsNone(gps)
        self.assertEqual(rejects[0]["reason"], "unsupported_gps_shape")


if __name__ == "__main__":
    unittest.main()

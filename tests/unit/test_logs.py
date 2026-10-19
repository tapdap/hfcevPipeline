import hashlib
import unittest

from hfcev.core import logs

H = "2024-05-07 08:00:00.001 [T1] INFO  x.y.z:1 - "


class Blocks(unittest.TestCase):
    def test_topic_pairs_with_next_message_on_same_thread(self):
        raw = (H + "接收消息主题:/f/tbox/D1/CAN\n" + H + '回调接受：接收消息:{\n"sn": "D1",\n"type": "heart"\n}\n').encode()
        kinds = [(b["kind"], b["topic"]) for b in logs.log_blocks(raw)]
        self.assertEqual(kinds, [("topic", "/f/tbox/D1/CAN"), ("message", "/f/tbox/D1/CAN")])

    def test_offsets_cover_the_file(self):
        raw = ("prefix bytes\n" + H + "plain line\n" + H + "接收消息:{\"a\": 1}\n").encode()
        blocks = list(logs.log_blocks(raw))
        self.assertEqual([b["kind"] for b in blocks], ["fragment", "other", "message"])
        self.assertEqual(blocks[0]["offset"], 0)
        self.assertEqual([b["end_offset"] for b in blocks[:-1]], [b["offset"] for b in blocks[1:]])
        self.assertEqual(blocks[-1]["end_offset"], len(raw))

    def test_truncated_and_unrecognised_json_are_rejected(self):
        raw = (H + '{"a": 1}\n' + H + "接收消息:{\"a\": \n").encode()
        self.assertEqual([b["reason"] for b in logs.log_blocks(raw)],
                         ["unrecognised_json_prefix", "invalid_or_truncated_json"])

    def test_non_finite_json_is_rejected(self):
        raw = (H + "接收消息:{\"a\": NaN}\n").encode()
        self.assertEqual(next(logs.log_blocks(raw))["kind"], "reject")


class Identity(unittest.TestCase):
    def test_statuses(self):
        self.assertEqual(logs.identity({"sn": "D1"}, "/f/tbox/D1/CAN"), ("D1", "topic_agrees", "D1"))
        self.assertEqual(logs.identity({"sn": "D1"}, "/f/tbox/D2/CAN"), ("D1", "topic_sn_mismatch", "D2"))
        self.assertEqual(logs.identity({"sn": "D1"}, None), ("D1", "payload_sn_only", None))
        self.assertEqual(logs.identity({"sn": "bad sn"}, None)[:2], (logs.UNASSIGNED, "invalid_or_missing_sn"))

    def test_event_id_definition(self):
        expected = hashlib.sha256("abc\0dir/f.log\x0042".encode()).hexdigest()[:32]
        self.assertEqual(logs.event_id("abc", "dir/f.log", 42), expected)

    def test_parse_time(self):
        self.assertEqual(str(logs.parse_time("2024-05-07 08:00:05")), "2024-05-07 08:00:05")
        self.assertIsNone(logs.parse_time("2024-05-07T08:00:05+08:00"))
        self.assertIsNone(logs.parse_time(None))


if __name__ == "__main__":
    unittest.main()

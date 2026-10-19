import unittest

from hfcev.core import matching

N_AUX = len(matching.AUXILIARY)


def core(x):
    return tuple(matching.canonical(v) for v in (x, 50.0, 80.0, 1000.0, 0.8, 0.7))


def aux(first=None):
    return (matching.canonical(first),) + (None,) * (N_AUX - 1)


class Canonical(unittest.TestCase):
    def test_rounding_and_missing(self):
        self.assertEqual(matching.canonical(0.1 + 0.2), "0.300000")
        self.assertEqual(matching.canonical(-0.0), "0.000000")
        self.assertIsNone(matching.canonical(float("nan")))
        self.assertIsNone(matching.canonical(None))


class Match(unittest.TestCase):
    def run_match(self, old, raw):
        meta = [(f"e{i}", "2024-05-08 10:00:00", "topic_agrees") for i in range(len(raw))]
        return matching.match([c for c, _ in old], [a for _, a in old], [c for c, _ in raw], [a for _, a in raw], meta)

    def test_unique_core_both_directions(self):
        records, messages, _ = self.run_match([(core(1), aux())], [(core(1), aux()), (core(2), aux())])
        self.assertEqual(records[0]["status"], "unique_core_candidate")
        self.assertEqual(records[0]["event_id"], "e0")
        self.assertEqual(messages, ["unique_content_candidate", "no_old_core_candidate"])

    def test_repeated_message_content_is_ambiguous(self):
        records, messages, _ = self.run_match([(core(1), aux())], [(core(1), aux()), (core(1), aux())])
        self.assertEqual(records[0]["status"], "ambiguous_content")
        self.assertEqual(messages, ["ambiguous_or_ineligible_content"] * 2)

    def test_auxiliary_field_separates_records(self):
        old = [(core(1), aux(5.0)), (core(1), aux(6.0))]
        records, messages, _ = self.run_match(old, [(core(1), aux(5.0)), (core(1), aux(6.0))])
        self.assertEqual([r["status"] for r in records], ["unique_extended_candidate"] * 2)
        self.assertEqual([r["event_id"] for r in records], ["e0", "e1"])

    def test_missing_core_never_matches(self):
        records, messages, _ = self.run_match([(None, aux())], [(None, aux())])
        self.assertEqual(records[0]["status"], "missing_old_core")
        self.assertEqual(messages, ["missing_raw_core"])

    def test_bad_identity_is_not_linked(self):
        meta = [("e0", "2024-05-08 10:00:00", "topic_sn_mismatch")]
        records, messages, _ = matching.match([core(1)], [aux()], [core(1)], [aux()], meta)
        self.assertEqual(records[0]["status"], "unique_invalid_time_or_identity")
        self.assertEqual(messages, ["ambiguous_or_ineligible_content"])


if __name__ == "__main__":
    unittest.main()

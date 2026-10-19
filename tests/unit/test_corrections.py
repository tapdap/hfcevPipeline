import unittest

import numpy as np

from hfcev.core import corrections


class NibbleSwap(unittest.TestCase):
    def test_every_byte_pair_is_recovered_exactly(self):
        d4, d5 = np.meshgrid(np.arange(256), np.arange(256))
        stored = [corrections.buggy_encode(a, b) for a, b in zip(d4.ravel(), d5.ravel())]
        bp, hp, ok = corrections.nibble_swap([s[0] for s in stored], [s[1] for s in stored])
        truth = [corrections.dbc_decode(a, b) for a, b in zip(d4.ravel(), d5.ravel())]
        self.assertTrue(ok.all())
        np.testing.assert_allclose(bp, [x[0] for x in truth], atol=1e-9)
        np.testing.assert_array_equal(hp, [x[1] for x in truth])

    def test_values_the_parser_cannot_produce_are_left(self):
        bp, hp, ok = corrections.nibble_swap([np.nan, 0.15, 409.6, 12.3], [1, 1, 1, 16])
        self.assertEqual(ok.tolist(), [False, False, False, False])
        self.assertTrue(np.isnan(bp).all() and np.isnan(hp).all())


if __name__ == "__main__":
    unittest.main()

"""Derived manuscript percentages must be checked as formulas, not JSON keys."""
import unittest

from figures.number_checks import expected_key_number


class FigureNumbers(unittest.TestCase):
    def setUp(self):
        self.numbers = {
            'vehicles': 2,
            'gps_proxy_check': {
                'result0': 100, 'proxy_on_result0': 80,
                'result0_missed_repeating': 5, 'result0_missed_new_position': 15,
            },
            'clock_integration': {
                'pairs': 200, 'pairs_diff_over_2s': 10,
                'on_time_ratio': 1.054, 'energy_ratio': 1.059,
            },
            'trip_gps_to_odometer_well_covered': {'km_share': 0.6},
            'hydrogen': {'fleet_kg_per_kwh': 1 / 33.33},
        }

    def test_formulas_preserve_percent_scales_and_conditional_denominators(self):
        expected = {
            'NumProxyNoFixFlaggedPercent': 80,
            'NumProxyMissed': 20,
            'NumProxyMissedRepeatingPercent': 25,
            'NumProxyMissedNewPercent': 15,
            'NumClockOnTimeExcessPercent': 5.4,
            'NumClockEnergyExcessPercent': 5.9,
            'NumClockPairsDiffPercent': 5,
            'NumWellCoveredKmSharePercent': 60,
            'NumStackEfficiencyPercent': 100,
        }
        for macro, value in expected.items():
            with self.subTest(macro=macro):
                self.assertAlmostEqual(expected_key_number(self.numbers, macro, 'formula (key_numbers.json): ...'), value)

    def test_direct_fields_and_unhandled_sources(self):
        self.assertEqual(expected_key_number(self.numbers, 'NumVehicles', 'key_numbers.json:vehicles'), 2)
        self.assertEqual(expected_key_number(self.numbers, 'Example', 'key_numbers.json:clock_integration.pairs'), 200)
        self.assertIsNone(expected_key_number(self.numbers, 'DatasetStart', 'key_numbers.json:period'))
        self.assertIsNone(expected_key_number(self.numbers, 'NumHandovers', 'handover:rows'))

    def test_unknown_direct_field_fails_instead_of_skipping(self):
        with self.assertRaises(KeyError):
            expected_key_number(self.numbers, 'Unknown', 'key_numbers.json:missing_field')

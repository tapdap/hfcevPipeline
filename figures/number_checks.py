"""Resolve manuscript values for independent figure-output checks."""

def expected_key_number(numbers, macro, source):
    """Resolve direct fields and the explicitly supported derived manuscript values."""
    proxy = numbers['gps_proxy_check']
    clock = numbers['clock_integration']
    formulas = {
        'NumProxyNoFixFlaggedPercent': lambda: 100 * proxy['proxy_on_result0'] / proxy['result0'],
        'NumProxyMissed': lambda: proxy['result0'] - proxy['proxy_on_result0'],
        'NumProxyMissedRepeatingPercent': lambda: 100 * proxy['result0_missed_repeating'] / (proxy['result0'] - proxy['proxy_on_result0']),
        'NumProxyMissedNewPercent': lambda: 100 * proxy['result0_missed_new_position'] / proxy['result0'],
        'NumClockOnTimeExcessPercent': lambda: 100 * (clock['on_time_ratio'] - 1),
        'NumClockEnergyExcessPercent': lambda: 100 * (clock['energy_ratio'] - 1),
        'NumClockPairsDiffPercent': lambda: 100 * clock['pairs_diff_over_2s'] / clock['pairs'],
        'NumWellCoveredKmSharePercent': lambda: 100 * numbers['trip_gps_to_odometer_well_covered']['km_share'],
        'NumStackEfficiencyPercent': lambda: 100 / (numbers['hydrogen']['fleet_kg_per_kwh'] * 33.33),
    }
    if macro in formulas:
        return formulas[macro]()
    if source.startswith('key_numbers.json:') and source != 'key_numbers.json:period':
        value = numbers
        for key in source.split(':', 1)[1].split('.'):
            value = value[key]
        return value
    return None

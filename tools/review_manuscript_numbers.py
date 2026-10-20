"""Recompute aggregate manuscript checks from current private stage manifests.

Usage: python tools/review_manuscript_numbers.py --work ../work --out review.json
Only aggregate counts are written; no device or vehicle identities are exported.
"""
import argparse
import json
import re
from pathlib import Path
from collections import Counter
import numpy as np


def review(work):
    def read(p):
        return json.loads((work / p).read_text())
    s3 = list(read('L3_timeline/_manifest.json')['devices'].values())
    s4 = list(read('L4_quality/_manifest.json')['devices'].values())
    s5 = list(read('L5_derived/_manifest.json')['devices'].values())
    logs = read('L0_logs/_manifest.json')
    dates = sorted({re.search(r'\d{4}-\d{2}-\d{2}', x['relative_path'])[0] for x in logs})
    gps = {k: sum(r['gps'].get(k, 0) for r in s3) for k in ('log_in_window', 'log_linked')}
    odo = {k: sum(r['odometer']['receipt_order']['all_messages'][k] for r in s3) for k in ('tested', 'passed')}
    mismatches = Counter()
    pressure = {}
    for r in s4:
        mismatches.update({k: v['differ'] for k, v in r['log_comparison'].items() if v['differ']})
        for group, vals in r['correction'].get('continuity_step_histogram', {}).get('classes', {}).items():
            if group not in pressure:
                pressure[group] = {k: np.zeros(len(v), dtype=np.int64) for k, v in vals.items()}
            for k, v in vals.items():
                pressure[group][k] += v
    clocks = [r['alignment']['clocks'] for r in s5]
    same = {k: sum(r['same_fix_chosen'][k] for r in clocks) for k in ('n', 'hit')}
    p90 = [r['chosen_fix_distance_km'].get('p90') for r in clocks]
    p90 = [x for x in p90 if x is not None and np.isfinite(x)]
    return dict(scope='111-TBOX stage manifests before vehicle installation filtering', devices=len(s3),
                log_files=len(logs), log_file_dates=len(dates), log_date_range=[dates[0], dates[-1]],
                gps_matching=gps, odometer_receipt_order=odo, log_mismatches=dict(mismatches),
                pressure={g: {k: v.tolist() for k, v in d.items()} for g, d in pressure.items()},
                same_fix_chosen=same, median_device_p90_pairing_km=float(np.median(p90)), pairing_devices=len(p90))


if __name__ == '__main__':
    p = argparse.ArgumentParser()
    p.add_argument('--work', type=Path, required=True)
    p.add_argument('--out', type=Path, required=True)
    a = p.parse_args()
    a.out.write_text(json.dumps(review(a.work), indent=2) + '\n')

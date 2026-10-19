"""Export small, anonymous figure-review summaries without rerunning any stage."""
from pathlib import Path
import argparse
import json
import numpy as np
import pandas as pd
from hfcev.config import load
from hfcev.core.field_quality import encodings
from hfcev.io import read_json, sha256_file
from hfcev.validation.stats import _device_vehicle, _write


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--local', type=Path)
    args = parser.parse_args()
    cfg = load(local=args.local)
    out = cfg.work / 'stats/v1.0'
    s5_path = cfg.layer('L5_derived') / '_manifest.json'
    s5 = read_json(s5_path)['devices']
    counts = np.zeros(8, dtype=np.int64)
    for device in _device_vehicle(cfg):
        values = s5[device]['refuels'].get('rise_hist', [])
        if values:
            counts += np.asarray(values, dtype=np.int64)
    bounds = [0, 1, 2, 5, 10, 15, 20, 30, np.inf]
    _write(out / 'pressure_rise.parquet', pd.DataFrame(dict(bin_left=bounds[:-1], bin_right=bounds[1:], count=counts)))
    dictionary = pd.read_csv(cfg.layer('L7_release') / 'dictionary.csv')
    fields = dictionary.loc[dictionary.table == 'operation', 'column'].tolist()
    resolutions = []
    for field, specs in encodings(cfg.file('dbc'), fields).items():
        factors = sorted({abs(e.factor) for e in specs})
        resolutions.append(dict(field=field, resolution=' / '.join(f'{v:g}' for v in factors), alternatives=len(factors)))
    _write(out / 'field_resolution.parquet', pd.DataFrame(resolutions))
    (out / 'figure_review_sources.json').write_text(json.dumps(dict(
        pressure_rise_source_sha256=sha256_file(s5_path),
        resolution_source_sha256=sha256_file(cfg.file('dbc')),
        pressure_rise_population='Positive consecutive nonzero pressure rises, before installation exclusions; stage-5 rise_hist bins.',
        resolution_basis='Absolute protocol scale factors; multiple mappings retained; units require dictionary interpretation.'), indent=2)+'\n')
    print(f'Exported {counts.sum():,} positive pressure rises and protocol resolution summaries.')


if __name__ == '__main__':
    main()

"""Read only figure inputs, write figures and explicit numerical evidence."""
import argparse
import hashlib
import json
from pathlib import Path

import numpy as np
import pandas as pd
from hfcev.config import load
from figures import style


class Inputs:
    def __init__(self):
        parser = argparse.ArgumentParser()
        parser.add_argument('--local', type=Path)
        parser.add_argument('--output', type=Path)
        args = parser.parse_args()
        self.cfg = load(local=args.local)
        self.stats = self.cfg.work / 'stats/v1.0'
        self.out = args.output or self.cfg.work / 'figures/v1.0'
        self.out.mkdir(parents=True, exist_ok=True)
        self.n = json.loads((self.stats / 'key_numbers.json').read_text())
        self.sources = {'stats/key_numbers.json': hashlib.sha256((self.stats / 'key_numbers.json').read_bytes()).hexdigest()}

    def table(self, name):
        path = self.stats / f'{name}.parquet'
        self.sources[f'stats/{path.name}'] = hashlib.sha256(path.read_bytes()).hexdigest()
        return pd.read_parquet(path)

    def release(self, name):
        if name not in ('dictionary.csv', 'refuels.parquet', 'segments.parquet', 'gaps.parquet', 'fc_runs.parquet', 'trips.parquet', 'daily.parquet'):
            raise ValueError('Only the dictionary and the fleet-wide event and calendar tables are read from the release')
        path = self.cfg.work / 'L7_release' / name
        self.sources[f'release/{name}'] = hashlib.sha256(path.read_bytes()).hexdigest()
        return pd.read_csv(path).fillna('') if name.endswith('.csv') else pd.read_parquet(path)

    def evidence(self, name, metrics, caption):
        write_json(self.out / f'{name}.json', dict(metrics=metrics, sources=self.sources, caption=caption,
                   font=style.FONT, width_mm=183))
        (self.out / f'{name}.caption.txt').write_text(caption + '\n')


def write_json(path, value):
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False, allow_nan=False,
                              default=lambda x: x.item() if hasattr(x, 'item') else str(x)) + '\n')


def ratio(a, b):
    return np.divide(a, b, out=np.full_like(np.asarray(a), np.nan, dtype=float), where=np.asarray(b) != 0)


def grouped(frame, keys):
    return frame.groupby(keys, dropna=False).sum(numeric_only=True).reset_index()


def tex(value):
    value = str(value)
    return ''.join({'\\': r'\textbackslash{}', '_': r'\_', '%': r'\%', '&': r'\&', '#': r'\#',
                    '{': r'\{', '}': r'\}', '$': r'\$', '≥': r'\ensuremath{\geq}', '≤': r'\ensuremath{\leq}', '°': r'\ensuremath{^\circ}', '—': '---', '–': '--', '×': r'\ensuremath{\times}', 'µ': r'\ensuremath{\mu}'}.get(c, c) for c in value)


def table_output(inp, name, frame, caption, label):
    frame.to_csv(inp.out / f'{name}.csv', index=False)
    rows = ['% Requires booktabs; TODO entries require the manuscript TODO macro.',
            r'\begin{table}[htbp]', r'\centering', r'\caption{' + tex(caption) + '}',
            r'\label{' + label + '}', r'\small', r'\begin{tabular}{' + ('p{65mm}p{105mm}' if len(frame.columns)==2 else 'p{57mm}p{55mm}p{55mm}') + '}',
            r'\toprule', ' & '.join(tex(c) for c in frame.columns) + r' \\', r'\midrule']
    for row in frame.itertuples(index=False, name=None):
        rows.append(' & '.join(str(v) if str(v).startswith(r'\TODO{') else tex(v) for v in row) + r' \\')
    rows += [r'\bottomrule', r'\end{tabular}', r'\end{table}', '']
    (inp.out / f'{name}.tex').write_text('\n'.join(rows))

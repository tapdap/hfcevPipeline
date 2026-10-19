#!/usr/bin/env python3
"""Package the stage-7 release for upload to a repository (Zenodo: at most 100 files per record).

Per-vehicle streams go into two ZIP archives (stored, not recompressed: Parquet is already zstd);
fleet tables, metadata and the CSV sample stay as individual files. Archives are deterministic
(sorted members, fixed timestamps). Writes SHA256SUMS.txt and MD5SUMS.txt for the upload files.
Internal files (`_parts/`, `_manifest.json`) are not packaged. With --stats, the figure-ready
statistics (anonymous summary tables, key numbers and their README) are added as a third archive,
so that every figure, table and number of the article can be regenerated from the deposit.

    python3 tools/package_release.py --release ../work/L7_release --stats ../work/stats/v1.0 --out ../work/zenodo/v1.0
"""
import argparse
import hashlib
import json
import shutil
import zipfile
from pathlib import Path

FIXED_TIME = (2026, 1, 1, 0, 0, 0)
ARCHIVES = {'operation': 'hfcev_fleet_v1.0_operation.zip', 'gps': 'hfcev_fleet_v1.0_gps.zip'}
SINGLE = ['README.md', 'MANIFEST.json', 'invariants.json', 'dictionary.csv', 'field_bits.csv', 'vehicles.csv',
          'segments.parquet', 'gaps.parquet', 'trips.parquet', 'fc_runs.parquet', 'refuels.parquet',
          'daily.parquet', 'monthly.parquet']


def digest(path, algo):
    h = hashlib.new(algo)
    with open(path, 'rb') as f:
        for block in iter(lambda: f.read(1 << 20), b''):
            h.update(block)
    return h.hexdigest()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--release', type=Path, required=True)
    ap.add_argument('--out', type=Path, required=True)
    ap.add_argument('--stats', type=Path)
    args = ap.parse_args()
    rel, out = args.release.resolve(), args.out.resolve()
    if out.exists() and any(out.iterdir()):
        raise SystemExit(f'{out} is not empty')
    out.mkdir(parents=True, exist_ok=True)
    manifest = json.loads((rel / 'MANIFEST.json').read_text())
    expected = {f['path']: f['sha256'] for f in manifest['files']}

    for folder, name in ARCHIVES.items():
        members = sorted((rel / folder).glob('*.parquet'))
        with zipfile.ZipFile(out / name, 'w', compression=zipfile.ZIP_STORED) as z:
            for m in members:
                arc = f'{folder}/{m.name}'
                if digest(m, 'sha256') != expected[arc]:
                    raise SystemExit(f'{arc} differs from MANIFEST.json')
                info = zipfile.ZipInfo(arc, FIXED_TIME); info.external_attr = 0o644 << 16
                with open(m, 'rb') as f:
                    z.writestr(info, f.read(), compress_type=zipfile.ZIP_STORED)
        print(f'{name}: {len(members)} files')
    if args.stats:
        members = sorted(p for p in args.stats.resolve().iterdir()
                         if p.is_file() and (p.suffix == '.parquet' or p.name in ('key_numbers.json', 'README.md')))
        with zipfile.ZipFile(out / 'hfcev_fleet_v1.0_statistics.zip', 'w', compression=zipfile.ZIP_DEFLATED) as z:
            for m in members:
                info = zipfile.ZipInfo(f'statistics/{m.name}', FIXED_TIME); info.external_attr = 0o644 << 16
                z.writestr(info, m.read_bytes(), compress_type=zipfile.ZIP_DEFLATED)
        print(f'hfcev_fleet_v1.0_statistics.zip: {len(members)} files')
    for name in SINGLE:
        if name in expected and digest(rel / name, 'sha256') != expected[name]:
            raise SystemExit(f'{name} differs from MANIFEST.json')
        shutil.copyfile(rel / name, out / name)
    for f in sorted((rel / 'sample').glob('*.csv')):
        shutil.copyfile(f, out / f'sample_{f.name}')

    files = sorted(p for p in out.iterdir() if p.is_file())
    (out / 'SHA256SUMS.txt').write_text(''.join(f'{digest(p, "sha256")}  {p.name}\n' for p in files))
    (out / 'MD5SUMS.txt').write_text(''.join(f'{digest(p, "md5")}  {p.name}\n' for p in files))
    total = sum(p.stat().st_size for p in out.iterdir())
    print(f'{len(list(out.iterdir()))} upload files, {total / 1e9:.3f} GB -> {out}')


if __name__ == '__main__':
    main()

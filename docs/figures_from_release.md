# Reproduce the manuscript figures from the data deposit

Install the code with figure dependencies as described in the repository README. Place the
downloaded upload files in `downloaded-data/` under the repository root and verify their
SHA-256 checksums:

```sh
cd downloaded-data
shasum -a 256 -c SHA256SUMS.txt
cd ..
```

Run this setup from the repository root. It copies the small public tables and extracts the
figure statistics to a separate work directory. The operation and GPS ZIP archives are not
needed for these figure scripts. All identifiers used in this setup are public vehicle IDs.

```python
from pathlib import Path
import csv
import shutil
import zipfile

download = Path('downloaded-data')
work = Path('work').resolve()
release = work / 'L7_release'
stats = work / 'stats' / 'v1.0'
release.mkdir(parents=True, exist_ok=True)
stats.mkdir(parents=True, exist_ok=True)

for name in ('dictionary.csv', 'vehicles.csv', 'segments.parquet', 'gaps.parquet',
             'refuels.parquet', 'fc_runs.parquet', 'trips.parquet', 'daily.parquet'):
    shutil.copyfile(download / name, release / name)
with zipfile.ZipFile(download / 'hfcev_fleet_v1.0_statistics.zip') as archive:
    for member in archive.infolist():
        if not member.is_dir():
            (stats / Path(member.filename).name).write_bytes(archive.read(member))

# The shared configuration loader expects a cohort file. Figure scripts use the
# published summary tables, so list the public vehicle IDs for this figure-only setup.
with (release / 'vehicles.csv').open(newline='') as source:
    vehicles = [row['vehicle_id'] for row in csv.DictReader(source)]
with (work / 'figure_cohort.csv').open('w', newline='') as target:
    writer = csv.writer(target)
    writer.writerow(['device_id'])
    writer.writerows([vehicle] for vehicle in vehicles)
Path('configs/local.figures.toml').write_text(
    f'work = "{work.as_posix()}"\n'
    'cohort = "figure_cohort.csv"\nworkers = 1\n'
)
```

Save that Python block as a temporary script or run it in Python, then generate the figures:

```sh
make figures LOCAL=configs/local.figures.toml
```

Outputs are written to `work/figures/v1.0/`: eight PDF/PNG figures, table CSV/LaTeX files,
number macros and numerical evidence JSON. The final `acceptance.json` records the verification
checks. `configs/local.figures.toml` and `work/` are ignored by Git. This configuration is for
figure generation; processing raw device streams uses the separate configuration in the README.

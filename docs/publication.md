# Code and data publication

The code repository and dataset deposit are separate resources. Data release v1.0 is associated
with software version 1.0.0. Record the exact Git commit when preparing an archive; a version
label alone does not identify changes made during publication preparation. The code repository is
[tapdap/hfcevPipeline](https://github.com/tapdap/hfcevPipeline). Both archive DOIs are pending.

## Code contents

Prepare the code snapshot from a reviewed Git commit using `git archive`, so it contains only
tracked files. Include the source, configuration templates, synthetic sample, tests, figure
scripts, packaging tool, documentation, `LICENSE`, `CITATION.cff` and `.zenodo.json`.

Keep local configurations, generated `outputs/`, Python caches, private inputs, private vehicle
maps and real telemetry outside this snapshot. The small files in `sample_data/` are synthetic
inputs and belong in the code archive. Do not copy the whole working directory as the code release.

## Dataset upload files

The current upload set contains 20 files:

| Group | Files | Count |
|---|---|---:|
| Vehicle streams | `hfcev_fleet_v1.0_operation.zip`, `hfcev_fleet_v1.0_gps.zip` | 2 |
| Figure inputs | `hfcev_fleet_v1.0_statistics.zip` | 1 |
| Event and calendar tables | `segments.parquet`, `gaps.parquet`, `trips.parquet`, `fc_runs.parquet`, `refuels.parquet`, `daily.parquet`, `monthly.parquet` | 7 |
| Metadata | `README.md`, `MANIFEST.json`, `invariants.json`, `dictionary.csv`, `field_bits.csv`, `vehicles.csv` | 6 |
| CSV examples | `sample_operation_V001_2025-12-23.csv`, `sample_gps_V001_2025-12-23.csv` | 2 |
| Transfer checksums | `SHA256SUMS.txt`, `MD5SUMS.txt` | 2 |

The operation and GPS archives each contain 101 vehicle Parquet files. Keep them zipped for
upload. The two checksum files cover the other 18 upload files; they do not cover each other.
`MANIFEST.json` describes the underlying stage-7 release files. It is distinct from the upload
checksums, which cover the ZIP containers and the separately uploaded files. The statistics
archive is an additional figure input and is checked against its source statistics directory.

From the upload directory, verify transfer integrity with:

```sh
shasum -a 256 -c SHA256SUMS.txt
```

Do not upload raw database exports, receive logs, the private CAN protocol, installation maps,
intermediate pipeline layers or internal review records as part of this deposit.

## Publication metadata

`CITATION.cff` and `.zenodo.json` describe the software. Dataset metadata is maintained separately
from the code snapshot. When the archive identifiers are available, fill the data DOI, software
DOI and public repository URL consistently in the README, manuscript and deposit records, and
add the dataset citation to the manuscript's reference generator. Use the actual publication
date in software citation metadata when publishing.

The Python distribution name is `hfcev-fleet-dataset`, its import package is `hfcev`, and its
command is `hfcev`. These names work independently of the GitHub repository and local folder names.

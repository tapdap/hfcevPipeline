# Code and data publication

The code repository and dataset deposit are separate resources. Data release v1.0 is associated
with software version 1.0.0. Record the exact Git commit when preparing an archive; a version
label alone does not identify changes made during publication preparation. The code repository is
[tapdap/hfcevPipeline](https://github.com/tapdap/hfcevPipeline). The data DOI is 10.5281/zenodo.23477229; the software archive DOI remains pending.

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

The operation and GPS archives each contain 101 Parquet files keyed by anonymous identifier (100 confirmed vehicles and one unresolved series). Keep them zipped for
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

## Manuscript closeout, 20 October 2026

The manuscript uses 100 confirmed vehicles plus one unresolved telemetry series, not 101 confirmed vehicles. Factory tests from late 2024 are retained; installation dates are outside the supplied metadata. Dataset totals include all anonymous identifiers.

`tools/review_manuscript_numbers.py --work <private-work> --out <work>/stats/v1.0/manuscript_review.json` recomputes aggregate checks from the current 111-device stage manifests. The output contains no device identifiers and is included in the statistics archive. These stage-level checks precede installation filtering. The log inventory contains 171 distinct file-path dates, which are not a guarantee of full-day logging. The former inventory endpoint percentage is omitted because dates and mileage conventions differ.

Comparison-table sources checked on 20 October 2026:

- VED: [author repository](https://github.com/gsoh/VED), 383 vehicles, November 2017–November 2018.
- HELECAR-D: [provider dataset page](https://eeb.uir.ac.ma/dataset_2/), urban EV data and public deposit.
- ZTBus: [data descriptor](https://www.nature.com/articles/s41597-023-02600-6), two trolleybuses, 1,409 missions, April 2019–December 2022, one-second synchronised data.
- DT-CARGO: [author institution record](https://portal.fis.tum.de/en/publications/heavy-commercial-vehicles-mobility-dataset-of-trucks-anonymized-r/), 54 instrumented trucks, four fleets, seven months, 1.26 million km. This is the instrumentation count reported in the abstract, not an assertion of 54 released vehicle IDs.
- SCANIA Component X: [data descriptor](https://www.nature.com/articles/s41597-025-04802-6), 23,550 vehicles in the training split; anonymised irregular operational readouts.

Owner actions after local validation: upload the replacement data package and updated data-record metadata; push the prepared public code repository and create its v1.0.0 release; obtain the software archive DOI and insert it into the manuscript and citation metadata; link the data and software deposits. No remote upload or publication is performed by this closeout. V101 remains unresolved and does not require an identity decision to retain it under the stated limitation.

## Editorial revision, 20 October 2026

The comparison is now manuscript Table 1 and includes fuel-cell bench datasets, the demand-power repository, JRC testing, Run on Less and NREL composite results. Fleet overview, accounting, fields and release files become Tables 2–5. Figure 1 points to accounting Table 3. This revision changes positioning, wording and presentation only; it adds no validation experiment or numerical result. Public resource descriptions use the existing cited studies, the [Mendeley repository](https://data.mendeley.com/datasets/jmf89ryh9n/1), [JRC catalogue](https://publications.jrc.ec.europa.eu/repository/handle/JRC139400), [NACFE findings](https://nacfe.org/research/run-on-less/messy-middle/final-findings/) and [NREL/NLR evaluation-centre description](https://www.nlr.gov/hydrogen/nfctec). Repository descriptions without a vehicle count or duration are reported as such. Telemetry-series terminology distinguishes data groupings from physical vehicles.

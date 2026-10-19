# HFCEV Pipeline

Processing, validation and figure code for the hydrogen fuel-cell electric vehicle (HFCEV)
fleet telemetry dataset. The code turns the raw database export (L0) into the released
data files and regenerates every figure, table and number in the data descriptor.

> Release v1.0 of the dataset (101 vehicles, 114 TBOX installations, October 2024 to May 2026) was produced
> with version 1.0.0 of this code. Data and software deposits are being prepared; the data DOI,
> software DOI will be added after registration. The code repository is
> [tapdap/hfcevPipeline](https://github.com/tapdap/hfcevPipeline). The raw inputs,
> the CAN protocol definition and the TBOX-to-vehicle map are private. The protocol checksum is
> fixed in `configs/release_v1.toml`; stage manifests record input fingerprints, including the map
> fingerprint at vehicle assembly. A synthetic sample (`sample_data/`) exercises every stage.

## Quick start

Requires Python ≥ 3.11 with `numpy` ≥ 2.4, `pandas` ≥ 3 and `pyarrow` ≥ 25 (see `pyproject.toml`).
Run the commands from the cloned repository root. The local directory name can be changed.

```bash
git clone https://github.com/tapdap/hfcevPipeline.git
cd hfcevPipeline
python -m venv .venv
source .venv/bin/activate
python -m pip install -e '.[figures]'
make test      # unit and integration tests on synthetic data
make sample    # run stages 1-7 and the statistics on the bundled synthetic inputs -> outputs/sample/
```

To regenerate the manuscript figures from the public data deposit, follow
[`docs/figures_from_release.md`](docs/figures_from_release.md).

To process real data, copy `configs/local.example.toml` to `configs/local.toml`, point
`work` at a directory containing `L0_raw/`, `L0_logs/`, a cohort CSV, the private DBC and the
private vehicle map (TBOX -> vehicle, see `src/hfcev/core/vehicles.py`), then:

```bash
make s1        # or: PYTHONPATH=src python -m hfcev run s1 --workers 4
make all       # s1 ... s7 and stats
make status
```

## Layout

| Path | Contents |
|---|---|
| `configs/release_v1.toml` | every parameter that changes the released data |
| `configs/local.example.toml` | machine paths and worker count (copy to `local.toml`) |
| `src/hfcev/stages/` | one module per Methods subsection; I/O and orchestration only |
| `src/hfcev/core/` | pure algorithms (tables in, tables out) |
| `src/hfcev/runner.py` | per-device parallel execution, resumable via stage manifests |
| `src/hfcev/validation/` | invariants and comparison with reference expectations |
| `sample_data/` | synthetic L0 export and its generator |
| `tests/` | unit, integration (synthetic) and regression (private data, skipped otherwise) |
| `docs/methods_map.md` | Methods subsection ↔ module ↔ configuration |
| `figures/` | scripts for every figure, table and number of the data descriptor (`make figures`) |
| `tools/package_release.py` | packs the release for repository upload (`make package`) |
| `docs/publication.md` | code and data upload contents, checksum verification and publication metadata |
| `docs/figures_from_release.md` | reproduce figures from the public deposit |

## Data layers

Data never live in this repository. Each stage reads one layer of the work directory
and writes the next, one Parquet file per device (vehicle from stage 6) and stream, plus `_manifest.json`
recording rows, SHA-256, parameter and code fingerprints per device:

```text
work/
  L0_raw/          raw export (read-only)
  L0_logs/         raw receive logs (read-only) with a SHA-256 inventory
  L1_harmonized/   stage 1: one operation stream and one GPS stream per device
  L2_logs/         stage 2: decoded log messages, operation records and GPS per device
  L3_timeline/     stage 3: stored records with log clocks, log-only additions, per-message checks
  L4_quality/      stage 4: reporting segments, no-data gaps, field and GPS flags, decoding corrections
  L5_derived/      stage 5: GPS fix status and track, alignment, odometer intervals, runs, trips, statistics
  L6_vehicles/     stage 6: TBOX installations joined into vehicles (anonymous ids), handover checks
  L7_release/      stage 7: the release package (per-vehicle Parquet, fleet tables, dictionary, manifest)
  stats/v1.0/      figure-ready statistics (the only input of figure scripts)
  runs/            one log directory per run
```

A device is recomputed only when the stage parameters or the code change, or with `--force`.

## Citation and licence

If you use this code, please cite the data descriptor and the archived software version (see `CITATION.cff`).
The code is licensed under the Apache License 2.0 (`LICENSE`); the dataset licence is CC BY 4.0.
The deposit links remain pending.

# Figures, tables and manuscript numbers

The scripts in this folder regenerate every figure, table and number of the data descriptor from the
figure-ready statistics (`work/stats/v1.0/`, written by `hfcev run stats`) and a few small release files.
They never read the record-level streams, the private protocol definition or the private vehicle map,
and they run no processing stage.

```sh
make figures                                      # all scripts below, then verify.py
make figures FIGURE_OUT=/tmp/hfcev-figures        # write to another folder
PYTHONPATH=src python figures/fig3_clocks.py      # a single script
```

`matplotlib` is the only extra dependency (`pip install .[figures]`). Each script runs single-threaded.
Outputs go to `work/figures/v1.0/` by default.

For the public deposit, use the setup in [`docs/figures_from_release.md`](../docs/figures_from_release.md).
Its statistics archive already contains the pressure-rise and resolution summaries.
When producing a new build from private inputs, these two summaries are exported separately with
`PYTHONPATH=src python -m hfcev.validation.figure_review_stats --local configs/local.toml`;
that preparation command reads stage-5 manifests and the protocol definition.

| Script | Output |
|---|---|
| `tab1_fleet.py`, `tab3_accounting.py`, `tab2_fields.py` | Tables 1–3 (CSV and LaTeX; Table 2 is accounting, Table 3 is the fields longtable) |
| `fig1_pipeline.py` | Fig. 1, schematic of acquisition, processing stages and release; all counts from the statistics |
| `fig2_coverage.py` … `fig7_consistency.py` | Figs. 2–7 |
| `fig8_usage.py` | Fig. 8, usage examples from the release event and calendar tables |
| `numbers.py` | `numbers.tex` (LaTeX macros for every number quoted in the text) and `numbers.json` (source and rounding of each) |
| `verify.py` | `acceptance.json`: cross-checks between figure evidence and the statistics |

Figures are written as PDF and 300 dpi PNG, each with its caption (`*.caption.txt`) and a JSON file of the
numbers it shows, including SHA-256 hashes of its inputs. Release files read directly are limited to
`dictionary.csv` (Table 3), `segments.parquet` and `gaps.parquet` (Fig. 4), and `refuels.parquet`,
`fc_runs.parquet`, `trips.parquet` and `daily.parquet` (Figs. 1, 6 and 8); see `common.Inputs.release`.

## Notes on interpretation

- Fig. 2 marks the first record of a replacement TBOX, not an inferred installation date; days without data stay blank.
- Fig. 3 shows the signed receipt-minus-stored lag, including negative values. Monthly log additions use the receipt month.
- Fig. 4 uses fixed interval bins and exact segment and gap distributions; GPS density is fixes per calendar day.
- Fig. 5 uses field-specific record denominators; missing cells are masked.
- Fig. 6 histograms clip values at the bin range given in `[stats]` of the release configuration.
- Fig. 7b shows per-device medians; box quartiles are across devices.
- Fig. 8c uses vehicle-months with at least 1,000 km for the vehicle quartiles.

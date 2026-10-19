# Data records (release v1.0)

The release package is written by stage 7 (`work/L7_release/`). The authoritative column list,
types, units and descriptions are in the generated `dictionary.csv`; row counts and checksums in
`MANIFEST.json`. This page describes the structure.

## Identity and time

- **Vehicle.** `vehicle_id` (V001 ...) is anonymous. A vehicle may carry several TBOX units over
  time (`unit` = 1, 2, ...; a new unit starts at a recorded TBOX replacement). TBOX numbers, plates
  and VINs are not released.
- **Operation records** are ordered by the server receipt time `time` (stored records: rounded to the nearest second; log additions: truncated to the second). Where a
  receive-log message is linked, `time_received_ms` adds milliseconds and `time_device` gives the
  device clock if it passed the checks (`time_device_status`).
- **GPS fixes** are ordered by device time `time`.

## Files

| file | grain | key columns |
|---|---|---|
| `vehicles.csv` | vehicle | units, period, records, GPS fixes, odometer and GPS distance, GPS quality shares |
| `operation/<vehicle>.parquet` | record | time, unit, source, measured fields, quality masks, `segment`, `gps_index`, `km_kind` |
| `gps/<vehicle>.parquet` | fix | time, unit, fix status, usable, step distance and speed, flags (coordinates per release policy) |
| `segments.parquet` | reporting segment | vehicle, segment, start, end, records, short |
| `gaps.parquet` | no-data gap | vehicle, after_segment, duration, log coverage label, SOC and odometer on both sides |
| `trips.parquet` | trip | vehicle, trip, start, end, active time, odometer and GPS distance, moving, hydrogen consumed, fuel-cell time and energy |
| `fc_runs.parquet` | fuel-cell run | vehicle, segment, start, end, on time, energy, mean / p95 power |
| `refuels.parquet` | refuelling | vehicle, before, after, pressures |
| `daily.parquet`, `monthly.parquet` | vehicle × day / month | records, active time, distances, fuel-cell time, trips, refuels |
| `dictionary.csv`, `field_bits.csv` | column / mask bit | description, unit and its basis; bit → field |
| `sample/` | one vehicle-day | CSV copies of operation and GPS rows |

## Quality information carried with the data

- Per record: missing / outside-encodable / all-ones masks per field, MinCellV ≤ MaxCellV
  violation, counters decreasing inside a segment, stored-vs-log mismatches, decoding-correction
  status, odometer interval kind (including `in_segment_delayed`), restored hydrogen counter
  (`h2_kg_restored`, `h2_increment`, `h2_kind`).
- Per fix: fix status (`valid`, `valid_assumed`, `no_fix`, `no_fix_inferred`, `static_while_moving`),
  usability, implausible speed, suspect device clock.
- Per vehicle (`vehicles.csv`): shares of no-fix, stuck and implausible GPS steps (no class labels).

## Known limitations (to be stated in Usage Notes)

- TotKM is 0 for all records before June 2025 (not reported); distances then come from GPS only.
- October 2025 – February 2026: transmission outages delayed records (receipt clock compressed,
  `in_segment_delayed`) and thinned GPS fixes fleet-wide.
- Units of several fields are inferred from scaling and value ranges (`unit_basis = inferred`).
- GPS coordinates: release policy pending.

# Methods ↔ code

| Methods subsection | Stage | Module(s) | Parameters (`configs/release_v1.toml`) |
|---|---|---|---|
| Data acquisition and export | — | export from the operator database (not reproducible without database access); L0 is the starting point | — |
| Source harmonisation | s1 | `stages/s1_harmonize.py`, `core/raw.py`, `core/dedup.py`, `core/gps_scale.py` | `[dataset]` window; `[s1_harmonize]` |
| Receive-log decoding | s2 | `stages/s2_logs.py`, `core/logs.py`, `core/dbc.py`, `core/messages.py` | `[s2_logs]`; field names from `[s1_harmonize]` |
| Timestamp reconciliation and log supplementation | s3 | `stages/s3_timeline.py`, `core/matching.py`, `core/clock.py`, `core/odometer.py`; review: `validation/s3_report.py` | `[dataset]` window; `[s3_timeline]` |
| Quality annotation (fields, continuity, GPS) | s4 | `stages/s4_quality.py`, `core/segments.py`, `core/field_quality.py`, `core/corrections.py`, `core/motion.py`; review: `validation/s4_report.py` | `[s4_quality]`; clock window and GPS speed from `[s3_timeline]` |
| Derived quantities (GPS fix status and track, odometer intervals, CAN–GPS alignment, runs, trips, refuelling, statistics) | s5 | `stages/s5_derived.py`, `core/derive.py`, `core/motion.py`; review: `validation/s5_report.py` | `[s5_derived]`; GPS speed limit from `[s3_timeline]` |
| Vehicle assembly (TBOX replacements) | s6 | `stages/s6_vehicles.py`, `core/vehicles.py` | `[s6_vehicles]`; private vehicle map (`local.toml [files] vehicle_map`) |
| Release files and dictionary | s7 | `stages/s7_release.py`, `core/dictionary.py` | `[s7_release]`; masks from `[s4_quality]` |
| Technical Validation statistics (figure inputs) | stats | `validation/stats.py` | `[stats]` |

## Stage 1 rules

1. **Window.** Keep `window_start ≤ time < window_end` on raw, naive timestamps.
2. **Device identity.** `sn` trimmed and upper-cased; per-device tables must contain only that device.
3. **Operation stream.** CAN tables and the legacy run table share the CAN schema (legacy lacks CAN-only fields, left null).
   Within a source, exact repeats of time + all measured fields are duplicates (first kept).
   A legacy record at a time also covered by CAN is retired as a match if all shared fields agree, otherwise as a conflict resolved in favour of CAN.
   Distinct records with the same timestamp in one source are all kept and counted in `same_time_variants`.
4. **Field removal.** `drop_fields` are removed from the operation stream after reconciliation.
5. **GPS stream.** Exact repeats removed. LAT/LON in the decimal-degree range on `gps_scale_restore_dates` are multiplied by 100 to the degree-minute encoding used elsewhere; any such row on another date stops the run.
6. **Accounting.** Every retired record is archived with its reason; input rows = kept + retired + out-of-window, per device and stream.

## Stage 2 rules

1. **Inputs.** Every log file is checked against the SHA-256 inventory (`L0_logs/_manifest.json`); the DBC against `dbc_sha256`.
2. **Blocks.** A log is split at header lines; topic blocks pair with the next message on the same thread. Bytes before the first header, truncated or non-finite JSON, and unknown JSON lines are rejected with their byte offsets. Nothing is joined across files.
3. **Identity.** The payload `sn` alone assigns the device; the topic is a cross-check (`topic_agrees`, `payload_sn_only`, `topic_sn_mismatch`). Invalid `sn` goes to `_unassigned`.
4. **Clocks.** Server-log time and payload time are both kept; neither is corrected.
5. **Frames.** `data` (10 values/frame, payload time) and `fault` (11 values/frame, own time) arrays are validated and decoded with the DBC (fixed Intel integer signals only). Unknown CAN IDs are counted, not dropped.
6. **Operation records.** One per `real_batch` message: signals of its `data` frames under qualified names and under matching operation-field names (case/underscore-insensitive); a signal with two different values in one message becomes null and is listed as a conflict.
7. **No filtering.** No deduplication, window restriction or re-timing; `event_id` = SHA-256 of (file SHA-256, path, byte offset), first 32 hex characters.

## Stage 3 rules

Two clocks exist for every log message: the server receipt clock (log time) and the device clock in the payload.
The stored operation `time` is the receipt clock rounded to the nearest second: for linked records, log receipt time minus stored time lies between −0.6 s and +1.0 s for all but 2 of 713,103 records (45 % negative; about 95 % within ±0.5 s). Log additions are given `time` = receipt time truncated to the second (rule 4), so their convention differs from stored records by at most 1 s.

1. **No re-timing.** Stored records keep `time`; none is moved or withdrawn.
2. **Correspondence.** A stored record and a log message correspond when their six core fields (FcVol, FcCur, SOC, TotKM, MaxCellV, MinCellV) and every auxiliary field observed by both agree at 6 decimals, and the correspondence is unique in both directions over the device. A linked record gains `time_received_ms` (log time of its message).
3. **Device clock checks** (in receipt order, per device): V1 both clocks parse; V2 `-max_reported_lead_s ≤ log − reported ≤ max_reported_lag_s`; V3 among V1+V2 messages the reported time lies strictly between those of the previous and next message (both required); V4 no other message has the same reported time with different content; identity `topic_agrees` or `payload_sn_only`. A linked record gains `time_device` only if its message passes; `time_device_status` is `verified` or the first failed check.
4. **Additions.** A log message is added when its core content occurs in no stored record, its receipt time lies in the window, its native quality is sound (good identity, normal frames, no unknown frames, no signal conflicts) and it is the first message with its (reported time, content). Its `time` is the receipt time truncated to the second (stored records are rounded; see above); `time_received_ms`, `time_device`/`time_device_status` (rule 3), `lag_s` and provenance (`event_id`, file index, byte offsets, log time) are kept with the operation fields. Every such message is published (release v1: `additions_require_verified_device_time = false`); a failed device clock leaves `time_device` null with the reason in `time_device_status`. Setting the switch to `true` would publish only additions with a verified device clock.
5. **Device-time axis.** A device time shared by two records (stored or added) is nulled for all of them (`device_time_collision`).
6. **Same-time variants** are recounted over stored records and published additions per `time`.
7. **GPS.** Stored GPS `time` is the device clock (96 % of log fixes received in the window equal a stored fix in device time and coordinates). Stored GPS rows are never changed.
   - *Correspondence:* a log fix and a stored fix correspond when device time, hemispheres and coordinates (rounded to `gps_coordinate_decimals`) agree; the stored fix gains `time_received_ms` from its earliest log fix.
   - *Additions:* a log fix received inside the window, with valid degree-minute coordinates and N/S, E/W letters, at a device time holding no stored fix, first of its (device time, coordinates), is added at its device time if its device clock passes rule 3 within the GPS stream.
   - *Pending, not published:* fixes whose device clock fails, and fixes at a device time that already holds a different stored fix.
8. **Diagnostics, not rules:** GPS implied speed (`gps_max_speed_kmh` within `gps_speed_max_gap_s`) against the stored track (fixes with a unique timestamp) and within the log stream; ordering of stored same-time groups by each new clock; odometer (TotKM) monotonicity against the stored timeline, among verified records on the device clock, and in receipt order within the log stream; threshold grid (`sensitivity_lead_s` × `sensitivity_lag_s`); an approximation of the earlier rule; the earlier candidate-pool definition (reported time in window) for regression.

## Stage 4 rules

Operation stream = stored records + published additions (receipt clock); GPS = stored fixes + GPS additions (device clock). No value is changed; flags and corrected columns are written beside the data.

1. **Reporting segments.** Records sorted by receipt time; a new segment starts after a gap longer than `segment_max_gap_s` (60 s). Segments shorter than `short_segment_s` (300 s) are flagged, never dropped. Segment counts for `gap_sensitivity_s` are reported.
2. **No-data gaps.** The interval between segments, labelled `no_log_files` (no log file covers it), `device_reported_in_logs` or `device_silent_in_logs`; SOC and odometer on both sides are kept (a rising odometer means data are missing while the vehicle moved).
3. **Field flags** (one bit per field): missing; outside the DBC-encodable range (bit length, factor, offset; the DBC declares no physical ranges); the all-ones "not available" code. Relations in `relations` (MinCellV ≤ MaxCellV); counters in `counters` that decrease inside a segment. Stuck values (constant runs ≥ `stuck_min_duration_s` inside a segment, zero and non-zero separately) and jumps (steps between records ≤ 60 s apart larger than `jump_range_share` of the device's p0.5–p99.5 range) are reported as statistics only, on the original values.
4. **Stored vs log values.** For stored records linked to a log message (stage 3) every field is compared with its DBC-decoded log value at 6 decimals.
5. **Decoding correction** (`decoding_corrections`). The historical parser displaced the nibbles of bytes D4–D5 for HighPPMNum and HyBottleP; both DBC values are recovered exactly from the two stored values into `HyBottleP_corrected` and `HighPPMNum_corrected` (originals kept). Additions are DBC-decoded and pass through. Verified against linked log values; a step-size and step-sign continuity check covers records without logs.
6. **GPS flags.** Encoding class, hemisphere letters, outside `gps_region_bbox`, implied speed above `gps_max_speed_kmh` to unique-time neighbours within `gps_speed_max_gap_s`, and a suspect device clock where a linked receipt time lies outside the stage-3 clock window.

## Stage 5 rules

Inputs: operation stream (stored + additions, receipt clock) with stage-4 segments and `HyBottleP_corrected`; GPS (stored + additions, device clock) with stage-4 flags; log GPS `result` via the stage-3 correspondence. No input value is changed; every output row points back to its source row (`origin`, `row`).

1. **Fix status.** Where a log message is linked, `result` decides (`valid` / `no_fix`). Otherwise a proxy: a run of fixes repeating one position is `no_fix_inferred` when the vehicle moved during it (the next fix implies ≥ `gps_no_fix_min_speed_kmh` from the run's first fix, or TotKM rose ≥ 1 km), else `valid_assumed`. A parked vehicle repeating its position stays valid. Checked against all fixes with known `result`.
2. **Stuck receiver.** In a trip with odometer distance ≥ `gps_static_min_km` whose GPS track covers less than `gps_static_share` of it, every otherwise valid fix becomes `static_while_moving`.
3. **Usable fix** = `valid` or `valid_assumed`, unique device time, device clock not suspect.
4. **GPS track.** Great-circle step distance and speed only between consecutive usable fixes ≤ `gps_track_max_gap_s` apart with no unusable fix between them (a tunnel breaks the track). Steps above the stage-3 GPS speed limit are excluded from distances. No altitude exists in the payload.
5. **Odometer intervals** (TotKM > 0, 1 km resolution, receipt order). Increment ≤ `odometer_max_speed_kmh`·Δt + 1 km: `in_segment`, `across_gap` (gap ≤ `odometer_gap_attribution_max_s`) or `across_long_gap` (reported, not attributed to a day or trip). A larger forward increment inside a segment is `in_segment_delayed`: a record delivered late after a transmission outage compresses the receipt clock; the distance is counted, no speed is derived from it. Larger forward increments across segments are `jump`; decreases `backward`. TotKM is 0 fleet-wide before June 2025.
5b. **Hydrogen counter.** TotH2Kg is an integer-kg cumulative counter that wraps after 655 (period `h2_counter_period_kg` = 655.36, i.e. 0.01 kg × 65,536). Per TBOX, readings ≤ 0 count as not reported; a drop of more than half a period between consecutive readings is one wrap; where the odometer advanced ≥ `h2_wrap_inference_min_km` between two readings, the number of wraps is set from the device's consumption rate on short steps × distance. `h2_kg_restored` = reading + period × wraps (cumulative within one TBOX); smaller decreases stay as `backward`. Consumption per trip, day and month sums forward steps over gaps ≤ `odometer_gap_attribution_max_s`. Diagnostics: kg per 100 km and kg per kWh of FcVol·FcCur over days with both distance and consumption (1/33.3 kg/kWh = 100 % LHV efficiency).
6. **Delayed-delivery check** (diagnostic). For each `in_segment_delayed` increment the longest gap between usable GPS fixes in [t(previous reading) − `delayed_check_window_s`, t + 60 s] and the GPS displacement across it relative to the increment; `in_segment` +1 km increments are the control.
7. **CAN–GPS alignment.** Each operation record (receipt time) takes the nearest usable fix (device time) within `alignment_tolerance_s`; at equal distance the earlier fix. Tolerance grid `alignment_grid_s` (nearest and backward), the reason for unmatched records, and same-clock variants (device–device on records with a verified device time; receipt–receipt) quantify the clock effect.
8. **Fuel-cell runs.** FcCur > `fc_on_current_a` inside one segment; on-time and FcVol·FcCur energy over record intervals ≤ 60 s.
9. **Refuelling.** `HyBottleP_corrected` rising ≥ `refuel_min_rise` between consecutive non-zero readings.
10. **Trips.** Segments joined across gaps ≤ `trip_merge_gap_s`, or across gaps ≤ `odometer_gap_attribution_max_s` whose odometer rise implies ≥ `trip_merge_min_speed_kmh` (driving through the gap). Kept whether or not the vehicle moved; `moving` = odometer or GPS-track distance ≥ `trip_moving_min_km`.
11. **Statistics.** Daily and monthly tables per device (receipt-clock calendar), including `km_delayed`.

## Stage 6 rules

1. **Vehicle map** (private; one row per TBOX installation): vehicle, installation order `unit`, validity interval [valid_from, valid_to), source and status. Only `confirmed` rows are applied; every cohort TBOX must appear; units of a vehicle are numbered 1..n, and the installations of a vehicle and of a TBOX never overlap (a TBOX moved between vehicles has one row per vehicle). Release v1: the plate–TBOX table compiled from the platform registry (two snapshots) and the operator ledger (TBOX code − 1000), reviewed against the data: ledger mileage at its reading date (≈ 2026-04-22), odometer continuity at each change, and TBOXes reporting on the same days with different odometer values (different vehicles). A TBOX split between vehicles is cut where its odometer restarts (or, for a TBOX that continues another vehicle, at its first odometer reading); a change takes effect at the new TBOX's first record after the old TBOX's last record, and earlier records of the new TBOX are treated as pre-installation and excluded.
2. **Assembly.** Per unit, the operation stream (stage-3 records with stage-4 flags and corrected columns and stage-5 alignment and odometer intervals) in receipt-time order and the GPS (stage-5 fix status and steps, stage-4 flags, decimal coordinates) in device-time order. Records outside the unit's interval are excluded and counted; nothing else is removed.
3. **Identifiers.** Segment and trip numbers are made unique per vehicle; the operation record's GPS link becomes a row index into the vehicle's GPS file. Derived tables are kept by their start time (a trip running past a removal is counted); daily rows overlapping the interval are summed per day and monthly statistics recomputed.
4. **Handover check.** Between consecutive units: last valid odometer before and first after, km per day (≤ `handover_max_km_per_day` is consistent), and the distance between the last and first usable GPS fixes.

The validity bounds above are telemetry-derived assignment boundaries, not field-recorded installation or removal times. Device reuse on another vehicle must be distinguished from replacement by a different device on the same vehicle. The three reused devices have approximately 20, 83 and 94 days between the observed usage periods; shared validity-bound timestamps do not imply same-day physical transfer.

**Current-build limitations (expression review, 2026-10-19; processing unchanged).** The whole-day overlap rule assigns the same 2025-08-21 device-day summary to V074 and V069: 68 operating records and 844 GPS fixes, with identical event and integration statistics, although the retained detail belongs only to V069. This is an unresolved aggregation error. Further calendar/detail GPS differences comprise 38 fixes preceding new units' first operating records by 1–44 s and 579 later fixes from V098's outgoing unit; their physical assignment is unresolved, so they are not a blanket deletion list. Three retained gaps connect different vehicles' usage periods of reused devices and cannot be read as continuous missing observation of the earlier vehicle. These issues require separate decisions; no boundary, filtering or aggregation rule has been changed by this documentation update.

## Stage 7 rules

1. **Fields.** Operation fields missing in ≥ `drop_missing_share` of all records (fleet-wide, stage-4 field statistics) are removed. `corrected_fields` are published with their corrected values under the original names. Provenance (database ids, source tables and rows, log files and byte offsets, TBOX numbers, plates) is removed; `source` (database / log_addition) and `unit` remain. Quality masks keep the stage-4 bit order (`field_bits.csv`); for corrected fields the missing, outside-encodable and all-ones bits are recomputed on the corrected values and the log-mismatch bit is cleared. `merged_fields` (one signal stored under two names: `hydncsu` ← `HYCSN`, renamed by the platform on 2025-06-09) fills the target where it is null, moving the source's quality bits to the target bit; the source is not published.
2. **GPS coordinates** follow `gps_coordinates` ("withheld", "full" or decimals; v1.0: 2 decimals). Fix status, usability, track steps, speeds and flags are always published; trip start/end cells only with coordinates.
3. **Files.** Per vehicle: `operation/`, `gps/`; fleet-wide: segments, gaps, trips, fc_runs, refuels, daily, monthly; `vehicles.csv` (period, counts, distances, GPS quality shares; no class labels), `dictionary.csv` (type, unit with its basis: protocol / provider / inferred / none, description, sparse and near-constant notes, usage caveats), `field_bits.csv`, a one-vehicle-day CSV sample, `README.md`, `MANIFEST.json` (rows and SHA-256 of every file).
4. **Invariants** (the run fails otherwise): stage-3 rows = released + excluded outside installation (operation and GPS); vehicles = stage 6; no TBOX number or plate in any value or file name; time order per vehicle unit; removed and merged-source fields absent; merged fields never present together.

## Statistics (figure inputs)

`hfcev run stats` scans the release once per vehicle and reads the stage manifests (TBOX → vehicle through the private map; outputs carry vehicle ids only). Tables and their planned figures are listed in `stats/v1.0/README.md`; histogram bins are fixed in `[stats]`. Figure scripts read only these tables.

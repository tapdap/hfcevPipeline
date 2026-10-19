"""Stage 4 — quality annotation: reporting segments, field flags and GPS flags. No value is changed.

Methods: "Quality annotation".

Operation stream = stored records + published additions (stage 3), on the receipt clock.
1. Reporting segments (`core.segments`): maximal runs whose consecutive receipt times differ
   by at most `segment_max_gap_s`; segments shorter than `short_segment_s` are flagged, not
   dropped. The interval between two segments is a no-data gap, labelled by log coverage:
   no_log_files / device_reported_in_logs / device_silent_in_logs, with odometer and SOC on
   both sides (a rising odometer across a gap means data are missing, not that the vehicle
   was parked).
2. Field flags (`core.field_quality`), one bit per field: missing, outside the DBC-encodable
   range, the all-ones "not available" code; relation MinCellV <= MaxCellV; counters that
   decrease inside a segment. Stuck values (constant runs) and jumps (steps above a share of
   the device's value range) are reported as statistics only.
3. Stored vs log values: for stored records linked to a log message (stage 3), every field
   is compared with its DBC-decoded log value (equal / differ / one side missing), with a
   scale hint for differences.
5. Decoding correction (`core.corrections`, if listed in `decoding_corrections`): HyBottleP and
   HighPPMNum of stored records are recovered from the historical parser's nibble displacement
   into `*_corrected` columns (originals kept); additions are DBC-decoded already. Verified
   against linked log values; a step-size continuity check covers periods without logs.
4. GPS flags (stored fixes + additions, device clock): encoding class, hemisphere letters,
   outside the region box, implied-speed jumps, and a suspect device clock where a linked
   receipt time shows a lag outside the stage-3 clock window.

Outputs in L4_quality/: operation_flags/, segments/, gaps/, gps_flags/, field_stats/ per device.
"""
from __future__ import annotations

import hashlib
import json
import re
from collections import Counter
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

from .. import io
from ..core import corrections, field_quality, gps_scale, motion, segments
from ..core.clock import NAT, to_us
from ..io import atomic_path, read_json, sha256_file
from . import s1_harmonize

NAME = "s4"
SECTION = "s4_quality"
PARAM_SECTIONS = ("s1_harmonize", "s3_timeline", SECTION)
CODE = (field_quality, segments, motion, gps_scale, corrections, io)
LAYER = "L4_quality"
MASKS = ("missing", "outside_encodable", "all_ones")
LOG_HOUR = re.compile(r"(\d{4}-\d{2}-\d{2})-(\d{2})\.\d+\.log$")
GAP_BINS_S = [0, 6, 10, 30, 60, 300, 3600, 86400, np.inf]
STEP_BINS = [0, 0.05, 0.5, 1, 5, 10, 50, np.inf]   # |step| of HyBottleP between consecutive records


def prepare(cfg, devices, force=False, workers=1, options=None) -> dict:
    params, s3p = cfg.release[SECTION], cfg.release["s3_timeline"]
    fields = s1_harmonize.operation_fields(cfg)
    encs = field_quality.encodings(cfg.file("dbc"), fields)
    hours = set()
    for item in read_json(cfg.layer("L0_logs") / "_manifest.json", []):
        m = LOG_HOUR.search(item["relative_path"])
        if m:
            hours.add(int(np.datetime64(f"{m[1]}T{m[2]}:00", "h").astype(np.int64)))
    s3_manifest = read_json(cfg.layer("L3_timeline") / "_manifest.json", {"devices": {}})["devices"]
    inputs = hashlib.sha256(json.dumps({d: r.get("sha256") for d, r in sorted(s3_manifest.items())},
                                       sort_keys=True).encode()).hexdigest()[:16]
    return dict(l2=str(cfg.layer("L2_logs")), l3=str(cfg.layer("L3_timeline")), out=str(cfg.layer(LAYER)),
                fields=fields, encodings={f: [e.__dict__ for e in v] for f, v in encs.items()},
                log_hours=sorted(hours), inputs=inputs, gap=params["segment_max_gap_s"],
                short=params["short_segment_s"], gap_grid=params["gap_sensitivity_s"],
                relations=params["relations"], counters=params["counters"], bbox=params["gps_region_bbox"],
                vmax=s3p["gps_max_speed_kmh"], vgap=s3p["gps_speed_max_gap_s"],
                max_lead=s3p["max_reported_lead_s"], max_lag=s3p["max_reported_lag_s"],
                corrections=list(params.get("decoding_corrections", [])),
                stuck_min_s=params["stuck_min_duration_s"], jump_share=params["jump_range_share"])


def _col(table, name, n) -> np.ndarray:
    if name not in table.column_names:
        return np.full(n, np.nan)
    return np.array([np.nan if v is None else v for v in table[name].to_pylist()], dtype=float)


def _read(path: Path, columns: list[str]) -> pa.Table:
    if not path.exists():
        return pa.table({})
    present = [c for c in columns if c in pq.read_schema(path).names]
    return pq.read_table(path, columns=present)


def _write(path: Path, table: pa.Table) -> None:
    with atomic_path(path) as tmp:
        pq.write_table(table, tmp, compression="zstd")


def _ts(us) -> pa.Array:
    arr = np.asarray(us, dtype=np.int64)
    return pa.array(np.where(arr == NAT, 0, arr), pa.timestamp("us"), mask=arr == NAT)


def _first_last(values: np.ndarray) -> tuple[float, float]:
    ok = values[np.isfinite(values) & (values > 0)]
    return (float(ok[0]), float(ok[-1])) if len(ok) else (np.nan, np.nan)


def process(device: str, ctx: dict) -> dict:
    out, fields, l3 = Path(ctx["out"]), ctx["fields"], Path(ctx["l3"])
    encs = {f: [field_quality.Encoding(**e) for e in v] for f, v in ctx["encodings"].items()}
    stored = _read(l3 / "operation" / f"{device}.parquet",
                   ["time", "same_time_variants", "match_event_id", "source_stream", *fields])
    added = _read(l3 / "additions" / f"{device}.parquet", ["time", "same_time_variants", "event_id", *fields])
    ns, na = stored.num_rows, added.num_rows if "time" in added.column_names else 0
    n = ns + na
    origin = np.concatenate([np.zeros(ns, np.int8), np.ones(na, np.int8)])
    row = np.concatenate([np.arange(ns), np.arange(na)]).astype(np.int64)
    t = np.concatenate([to_us(stored["time"].to_numpy(zero_copy_only=False)),
                        to_us(added["time"].to_numpy(zero_copy_only=False)) if na else np.array([], np.int64)])
    order = np.lexsort((row, origin, t))
    ts = t[order]
    seg_sorted = segments.split(ts, ctx["gap"])
    seg = np.empty(n, dtype=np.int64)
    seg[order] = seg_sorted
    values = {f: np.concatenate([_col(stored, f, ns), _col(added, f, na) if na else np.array([])]) for f in fields}

    # 2. field flags, per-field statistics by receipt month
    masks = {k: np.zeros(n, dtype=np.int64) for k in MASKS}
    month = t.astype("datetime64[us]").astype("datetime64[M]")
    months, month_idx = np.unique(month, return_inverse=True)
    stat_rows, totals = [], {}
    for j, f in enumerate(fields):
        fl = field_quality.flags(values[f], encs.get(f, []))
        for k in MASKS:
            masks[k] |= fl[k].astype(np.int64) << j
        counts = {k: np.bincount(month_idx, weights=fl[k], minlength=len(months)).astype(int) for k in fl}
        records = np.bincount(month_idx, minlength=len(months))
        for i, mo in enumerate(months):
            stat_rows.append(dict(month=str(mo), field=f, records=int(records[i]), **{k: int(c[i]) for k, c in counts.items()}))
        totals[f] = {k: int(v.sum()) for k, v in fl.items()} | dict(records=n, dbc_signal=bool(encs.get(f)))
    relation = np.zeros(n, dtype=bool)
    relation_counts = {}
    for low, high in (r for r in ctx["relations"] if r[0] in values and r[1] in values):
        bad = np.isfinite(values[low]) & np.isfinite(values[high]) & (values[low] > values[high])
        relation |= bad
        relation_counts[f"{low}<={high}"] = dict(tested=int((np.isfinite(values[low]) & np.isfinite(values[high])).sum()),
                                                violated=int(bad.sum()))
    backward = np.zeros(n, dtype=np.int64)
    backward_counts = {}
    for b, c in ((b, c) for b, c in enumerate(ctx["counters"]) if c in values):
        v = values[c][order]
        ok = np.flatnonzero(np.isfinite(v) & (v > 0))
        same_seg = seg_sorted[ok[1:]] == seg_sorted[ok[:-1]]
        later = ts[ok[1:]] > ts[ok[:-1]]
        drop = same_seg & later & (v[ok[1:]] < v[ok[:-1]])
        hit = order[ok[1:][drop]]
        backward[hit] |= 1 << b
        backward_counts[c] = dict(tested=int((same_seg & later).sum()), decreasing=int(drop.sum()))

    # 2b. stuck values and jumps: statistics only (no per-record flags)
    stuck_jump = {}
    for f in fields:
        v = values[f][order]
        stuck_jump[f] = dict(stuck=field_quality.stuck_runs(v, ts, seg_sorted, ctx["stuck_min_s"]),
                             jump=field_quality.jumps(v, ts, seg_sorted, ctx["gap"], ctx["jump_share"]))

    # 3. stored vs log-decoded values for linked stored records
    log_mismatch = np.zeros(n, dtype=np.int64)
    comparison = {}
    linked_event = stored["match_event_id"].to_pylist() if ns else []
    linked_rows = np.array([i for i, e in enumerate(linked_event) if e], dtype=np.int64)
    log_path = Path(ctx["l2"]) / "operation" / f"{device}.parquet"
    if len(linked_rows) and log_path.exists():
        log = pq.read_table(log_path, columns=["event_id", *[f for f in fields if f in pq.read_schema(log_path).names]])
        index = {e: i for i, e in enumerate(log["event_id"].to_pylist())}
        take = np.array([index[linked_event[r]] for r in linked_rows], dtype=np.int64)
        for j, f in enumerate(fields):
            lv = _col(log, f, log.num_rows)[take]
            sv = values[f][linked_rows]
            cmp = field_quality.compare(sv, lv)
            log_mismatch[linked_rows[cmp["differ"]]] |= 1 << j
            comparison[f] = {k: int(v.sum()) for k, v in cmp.items()}
            if comparison[f]["differ"]:
                comparison[f]["scale_hint"] = field_quality.scale_hint(sv[cmp["differ"]], lv[cmp["differ"]])

    # 1. segments and no-data gaps
    soc, km = values.get("SOC", np.full(n, np.nan))[order], values.get("TotKM", np.full(n, np.nan))[order]
    variants = np.concatenate([stored["same_time_variants"].to_numpy(zero_copy_only=False),
                               added["same_time_variants"].to_numpy(zero_copy_only=False) if na else np.array([])])[order]
    any_out = (masks["outside_encodable"] != 0)[order]
    any_ones = (masks["all_ones"] != 0)[order]
    bounds = np.flatnonzero(np.diff(np.concatenate([[-1], seg_sorted, [seg_sorted[-1] + 1 if n else 0]])))
    seg_rows = []
    for s, (a, b) in enumerate(zip(bounds[:-1], bounds[1:])):
        tt = ts[a:b]
        d = np.diff(tt) / 1e6
        d = d[d > 0]
        soc0, soc1 = _first_last(soc[a:b])
        km0, km1 = _first_last(km[a:b])
        start, end = int(tt[0]), int(tt[-1])
        seg_rows.append(dict(segment=s, start=start, end=end, duration_s=(end - start) / 1e6, records=int(b - a),
                             additions=int((origin[order][a:b] == 1).sum()),
                             median_interval_s=float(np.median(d)) if len(d) else np.nan,
                             share_intervals_le_6s=float((d <= 6).mean()) if len(d) else np.nan,
                             short=(end - start) / 1e6 < ctx["short"],
                             crosses_midnight=bool(start // 86_400_000_000 != end // 86_400_000_000),
                             same_time_rows=int((variants[a:b] > 1).sum()), rows_outside_encodable=int(any_out[a:b].sum()),
                             rows_all_ones=int(any_ones[a:b].sum()), soc_first=soc0, soc_last=soc1, km_first=km0,
                             km_last=km1))
    seg_df = pd.DataFrame(seg_rows)
    msg_path = Path(ctx["l2"]) / "messages" / f"{device}.parquet"
    msg_us = np.sort(to_us(pd.to_datetime(pq.read_table(msg_path, columns=["log_time"])["log_time"].to_pandas()
                                          .str.replace(",", ".", regex=False), format="ISO8601").to_numpy())) \
        if msg_path.exists() else np.array([], np.int64)
    hours = set(ctx["log_hours"])
    gap_rows = []
    for prev, nxt in zip(seg_rows[:-1], seg_rows[1:]):
        start, end = prev["end"], nxt["start"]
        cover = segments.hours_covered(start, end, hours)
        inside = int(np.searchsorted(msg_us, end, side="left") - np.searchsorted(msg_us, start, side="right"))
        label = "no_log_files" if cover == 0 else "device_reported_in_logs" if inside else "device_silent_in_logs"
        gap_rows.append(dict(after_segment=prev["segment"], start=start, end=end, duration_s=(end - start) / 1e6,
                             log_hours_covered=cover, device_log_messages=inside, label=label,
                             soc_before=prev["soc_last"], soc_after=nxt["soc_first"], km_before=prev["km_last"],
                             km_after=nxt["km_first"], km_delta=nxt["km_first"] - prev["km_last"]))
    gap_df = pd.DataFrame(gap_rows)

    # 5. decoding correction, its verification against logs and a continuity check
    correction = _correct(ctx, values, stored, ns, na, order, seg_sorted, ts, linked_rows, linked_event,
                          log if len(linked_rows) and log_path.exists() else None)

    # 4. GPS flags (device clock)
    gps_rec = _gps(device, ctx, out, l3)

    # outputs
    _write(out / "operation_flags" / f"{device}.parquet", pa.table({
        "origin": pa.array(np.where(origin == 0, "stored", "addition").tolist(), pa.string()), "row": pa.array(row),
        "time": _ts(t), "segment": pa.array(seg), **{f"{k}_mask": pa.array(v) for k, v in masks.items()},
        "relation_violation": pa.array(relation), "counter_backward_mask": pa.array(backward),
        "log_mismatch_mask": pa.array(log_mismatch), **correction.pop("columns")}))
    seg_table = pa.Table.from_pandas(seg_df, preserve_index=False) if len(seg_df) else pa.table({"segment": pa.array([], pa.int64())})
    for c in ("start", "end"):
        if c in seg_table.column_names:
            seg_table = seg_table.set_column(seg_table.schema.get_field_index(c), c, _ts(seg_df[c].to_numpy()))
    _write(out / "segments" / f"{device}.parquet", seg_table)
    gap_table = pa.Table.from_pandas(gap_df, preserve_index=False) if len(gap_df) else pa.table({"start": pa.array([], pa.int64())})
    for c in ("start", "end"):
        if len(gap_df):
            gap_table = gap_table.set_column(gap_table.schema.get_field_index(c), c, _ts(gap_df[c].to_numpy()))
    _write(out / "gaps" / f"{device}.parquet", gap_table)
    _write(out / "field_stats" / f"{device}.parquet", pa.Table.from_pylist(stat_rows) if stat_rows else pa.table({}))

    dur = seg_df["duration_s"].to_numpy() if len(seg_df) else np.array([])
    return dict(
        records=dict(total=n, stored=ns, additions=na),
        segments=dict(count=len(seg_rows), short=int(seg_df["short"].sum()) if len(seg_df) else 0,
                      crosses_midnight=int(seg_df["crosses_midnight"].sum()) if len(seg_df) else 0,
                      duration_s=float(dur.sum()), median_duration_s=float(np.median(dur)) if len(dur) else None,
                      count_by_gap={f"{g}s": segments.count(ts, g) for g in [ctx["gap"], *ctx["gap_grid"]]}),
        gaps=dict(count=len(gap_rows), by_label=dict(Counter(gap_df["label"])) if len(gap_df) else {},
                  duration_s_by_label={k: float(v) for k, v in gap_df.groupby("label")["duration_s"].sum().items()} if len(gap_df) else {},
                  odometer_rose=int((gap_df["km_delta"] > 0).sum()) if len(gap_df) else 0,
                  odometer_rose_by_label={k: int(v) for k, v in gap_df[gap_df["km_delta"] > 0].groupby("label").size().items()} if len(gap_df) else {}),
        intervals=dict(zip([f"<= {b}s" for b in GAP_BINS_S[1:]],
                           np.histogram(np.diff(np.unique(ts)) / 1e6, bins=GAP_BINS_S)[0].tolist())),
        fields=totals, relations=relation_counts, counters=backward_counts, log_comparison=comparison,
        correction=correction, stuck_jump=stuck_jump,
        gps=gps_rec,
        sha256={k: sha256_file(out / k / f"{device}.parquet")
                for k in ("operation_flags", "segments", "gaps", "gps_flags", "field_stats")})


def _correct(ctx, values, stored, ns, na, order, seg_sorted, ts, linked_rows, linked_event, log) -> dict:
    """HyBottleP/HighPPMNum nibble-swap correction for stored records (additions are DBC-decoded)."""
    n = ns + na
    name = "hybottlep_highppmnum_nibble_swap"
    if name not in ctx["corrections"] or "HyBottleP" not in values or "HighPPMNum" not in values:
        return dict(columns={}, applied=False)
    bp, hp = values["HyBottleP"], values["HighPPMNum"]
    fixed_bp, fixed_hp, ok = corrections.nibble_swap(bp[:ns], hp[:ns])
    corr_bp = np.concatenate([fixed_bp, bp[ns:]])
    corr_hp = np.concatenate([fixed_hp, hp[ns:]])
    source = np.array(stored["source_stream"].to_pylist() if "source_stream" in stored.column_names else ["can"] * ns,
                      dtype=object)
    status = np.array([("reconstructed" if k else "not_reconstructable") for k in ok] + ["dbc_decoded"] * na, dtype=object)
    basis = np.array([("verified_source" if s == "can" else f"{s}_source_unverified") for s in source]
                     + ["dbc_decoded"] * na, dtype=object)
    record = dict(applied=True, stored=ns, reconstructed=int(ok.sum()), not_reconstructable=int(ns - ok.sum()),
                  basis=dict(Counter(basis[:ns].tolist())))
    if log is not None:
        index = {e: i for i, e in enumerate(log["event_id"].to_pylist())}
        take = np.array([index[linked_event[r]] for r in linked_rows], dtype=np.int64)
        lbp, lhp = _col(log, "HyBottleP", log.num_rows)[take], _col(log, "HighPPMNum", log.num_rows)[take]
        both = np.isfinite(lbp) & np.isfinite(lhp)
        record["verification"] = dict(
            linked=int(len(linked_rows)), compared=int(both.sum()),
            corrected_equal=int((both & np.isclose(fixed_bp[linked_rows], lbp, atol=1e-6)
                                 & (fixed_hp[linked_rows] == lhp)).sum()),
            stored_equal_HyBottleP=int((both & np.isclose(bp[linked_rows], lbp, atol=1e-6)).sum()),
            stored_equal_HighPPMNum=int((both & (hp[linked_rows] == lhp)).sum()))
    else:
        record["verification"] = dict(linked=0, compared=0, corrected_equal=0, stored_equal_HyBottleP=0,
                                      stored_equal_HighPPMNum=0)
    # continuity: |step| between consecutive records of one segment (0 < dt <= 60 s), stored vs corrected
    year = ts.astype("datetime64[us]").astype("datetime64[Y]").astype(int) + 1970
    origin_sorted = np.concatenate([np.zeros(ns, np.int8), np.ones(na, np.int8)])[order]
    src_sorted = np.concatenate([source, np.array(["addition"] * na, dtype=object)])[order]
    linked = np.zeros(n, dtype=bool)
    linked[linked_rows] = True
    linked_sorted = linked[order]
    klass = np.where(origin_sorted == 1, "addition",
                     np.where(src_sorted == "can",
                              np.where(year <= 2024, "stored_can_2024",
                                       np.where(linked_sorted, "stored_can_2025_2026_linked", "stored_can_2025_2026_unlinked")),
                              "stored_" + src_sorted.astype(str)))
    pair = (seg_sorted[1:] == seg_sorted[:-1]) & (np.diff(ts) > 0) & (np.diff(ts) <= 60e6)
    continuity = {}
    for label, series in (("stored", bp[order]), ("corrected", corr_bp[order])):
        signed = np.diff(series)
        step = np.abs(signed)
        good = pair & np.isfinite(step)
        for k in np.unique(klass):
            sel = good & (klass[1:] == k)
            big = sel & (step > 5)
            continuity.setdefault(k, {})[label] = np.histogram(step[sel], bins=STEP_BINS)[0].tolist()
            continuity[k][label + "_large_steps_up_down"] = [int((big & (signed > 0)).sum()), int((big & (signed < 0)).sum())]
    record["continuity_step_histogram"] = dict(bins=[str(b) for b in STEP_BINS], classes=continuity)
    record["columns"] = {"HyBottleP_corrected": pa.array(corr_bp, pa.float64()),
                         "HighPPMNum_corrected": pa.array(corr_hp, pa.float64()),
                         "correction_status": pa.array(status.tolist(), pa.string()),
                         "correction_basis": pa.array(basis.tolist(), pa.string())}
    return record


def _gps(device: str, ctx: dict, out: Path, l3: Path) -> dict:
    cols = ["time", "LAT", "LATD", "LON", "LOND", "same_time_variants"]
    stored = _read(l3 / "gps" / f"{device}.parquet", cols + ["time_received_ms"])
    added = _read(l3 / "gps_additions" / f"{device}.parquet", cols)
    ns, na = stored.num_rows, (added.num_rows if "time" in added.column_names else 0)
    cat = lambda name, fill: np.concatenate([stored[name].to_numpy(zero_copy_only=False) if ns else np.array([]),
                                             added[name].to_numpy(zero_copy_only=False) if na else np.array([])]) \
        if name in stored.column_names else fill
    t = np.concatenate([to_us(stored["time"].to_numpy(zero_copy_only=False)) if ns else np.array([], np.int64),
                        to_us(added["time"].to_numpy(zero_copy_only=False)) if na else np.array([], np.int64)])
    n = ns + na
    lat_raw, lon_raw = cat("LAT", None).astype(float), cat("LON", None).astype(float)
    ns_l = (stored["LATD"].to_pylist() if ns else []) + (added["LATD"].to_pylist() if na else [])
    ew_l = (stored["LOND"].to_pylist() if ns else []) + (added["LOND"].to_pylist() if na else [])
    cls = gps_scale.classify(lat_raw, lon_raw) if n else np.array([], np.int8)
    letters_ok = np.array([(a or "").strip().upper() in ("N", "S") and (b or "").strip().upper() in ("E", "W")
                           for a, b in zip(ns_l, ew_l)], dtype=bool)
    lat, lon = motion.degrees(lat_raw, ns_l, "S", 90), motion.degrees(lon_raw, ew_l, "W", 180)
    la0, la1, lo0, lo1 = ctx["bbox"]
    valid = (cls == gps_scale.DMM_VALID) & letters_ok & np.isfinite(lat) & np.isfinite(lon)
    outside = valid & ~((lat >= la0) & (lat <= la1) & (lon >= lo0) & (lon <= lo1))
    variants = cat("same_time_variants", np.ones(n)).astype(np.int64)
    single = valid & (variants == 1)
    track = motion.track(t[single], lat[single], lon[single])
    speed_ok = np.full(n, np.nan)
    speed_ok[single] = motion.check(*track, t[single], lat[single], lon[single], ctx["vgap"], ctx["vmax"])
    suspect = np.zeros(n, dtype=bool)
    if ns and "time_received_ms" in stored.column_names:
        recv = to_us(stored["time_received_ms"].to_numpy(zero_copy_only=False).astype("datetime64[us]"))
        lag = np.where(recv != NAT, (recv - t[:ns]) / 1e6, np.nan)
        suspect[:ns] = np.isfinite(lag) & ((lag < -ctx["max_lead"]) | (lag > ctx["max_lag"]))
    _write(out / "gps_flags" / f"{device}.parquet", pa.table({
        "origin": pa.array(["stored"] * ns + ["addition"] * na, pa.string()),
        "row": pa.array(np.concatenate([np.arange(ns), np.arange(na)]).astype(np.int64)), "time": _ts(t),
        "coordinate_class": pa.array([gps_scale.CLASSES[c] for c in cls], pa.string()),
        "hemisphere_ok": pa.array(letters_ok), "outside_region": pa.array(outside),
        "speed_plausible": pa.array(speed_ok, pa.float64()), "device_clock_suspect": pa.array(suspect)}))
    unique_t = np.unique(t)
    return dict(fixes=n, stored=ns, additions=na,
                coordinate_class=dict(Counter(gps_scale.CLASSES[c] for c in cls)),
                hemisphere_bad=int((~letters_ok).sum()), outside_region=int(outside.sum()),
                speed=dict(tested=int(np.isfinite(speed_ok).sum()), implausible=int((speed_ok == 0).sum())),
                same_time_rows=int((variants > 1).sum()), device_clock_suspect=int(suspect.sum()),
                intervals=dict(zip([f"<= {b}s" for b in GAP_BINS_S[1:]],
                                   np.histogram(np.diff(unique_t) / 1e6, bins=GAP_BINS_S)[0].tolist())))

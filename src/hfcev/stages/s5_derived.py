"""Stage 5 — derived quantities: GPS fix status and track, odometer intervals, CAN-GPS alignment,
fuel-cell runs, trips, refuelling events and daily statistics. No input value is changed.

Methods: "Derived quantities".

Inputs: operation stream (stored + additions, receipt clock) from stage 3 with stage-4
segments and corrected HyBottleP; GPS (stored + additions, device clock) from stage 3 with
stage-4 flags; log GPS `result` (1 = fix, 0 = no fix) from stage 2.
1. GPS fix status: `result` where a log message is known; otherwise a frozen-position proxy
   (`core.derive.fix_proxy`), validated on fixes whose `result` is known.
2. GPS track: step distance and speed only between usable fixes (valid fix, unique device
   time, no suspect clock) within `gps_track_max_gap_s`; a no-fix stretch breaks the track.
   Horizontal (great-circle) distance; no altitude is available.
3. Hydrogen counter (TotH2Kg, integer kg) wraps at `h2_counter_period_kg`; restored per TBOX
   (`core.derive.counter_steps`), wraps hidden in data gaps counted from the odometer over
   >= `h2_wrap_inference_min_km`; consumption per day / trip from forward steps over gaps <=
   `odometer_gap_attribution_max_s`.
3b. Odometer (TotKM, 1 km resolution): qualified increments inside segments and across
   no-data gaps (<= `odometer_gap_attribution_max_s`). A forward increment inside a segment
   that is too fast on the receipt clock is `in_segment_delayed` (delayed delivery): counted
   as distance, never used for speed. Backward steps and jumps across segments are events.
4. CAN-GPS alignment: CAN receipt time vs GPS device time, nearest within
   `alignment_tolerance_s`; a tolerance grid, a backward variant and two same-clock
   variants (device-device, receipt-receipt) quantify the effect of the clock difference.
5. Fuel-cell runs: FcCur > `fc_on_current_a` inside a segment; duration, energy (FcVol x FcCur).
6. Trips: segments joined across gaps <= `trip_merge_gap_s`, or across gaps <=
   `odometer_gap_attribution_max_s` whose odometer rise implies driving through the gap
   (>= `trip_merge_min_speed_kmh`). `moving` = odometer or GPS-track distance >= `trip_moving_min_km`.
   GPS in a trip with odometer distance >= `gps_static_min_km` but GPS track below
   `gps_static_share` of it is `static_while_moving` (receiver stuck) and not usable.
7. Refuelling: corrected HyBottleP rising by >= `refuel_min_rise` between consecutive readings.
8. Daily and monthly statistics per device (receipt-clock calendar day / month).

Outputs in L5_derived/: gps_track/, alignment/, fc_runs/, trips/, refuels/, daily/, monthly/ per device.
"""
from __future__ import annotations

import hashlib
import json
from collections import Counter
from pathlib import Path

import numpy as np
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

from .. import io
from ..core import derive, motion
from ..core.clock import NAT, to_us
from ..io import atomic_path, read_json, sha256_file

NAME = "s5"
SECTION = "s5_derived"
PARAM_SECTIONS = ("s1_harmonize", "s3_timeline", "s4_quality", SECTION)
CODE = (derive, motion, io)
LAYER = "L5_derived"
DISTANCE_KINDS = ["in_segment", "in_segment_delayed", "across_gap"]
OP_COLUMNS = ["time", "time_device", "FcCur", "FcVol", "SOC", "TotKM", "TotH2Kg", "main_state"]
GPS_COLUMNS = ["time", "LAT", "LATD", "LON", "LOND", "same_time_variants", "time_received_ms"]


def prepare(cfg, devices, force=False, workers=1, options=None) -> dict:
    p = cfg.release[SECTION]
    s4 = read_json(cfg.layer("L4_quality") / "_manifest.json", {"devices": {}})["devices"]
    inputs = hashlib.sha256(json.dumps({d: r.get("sha256") for d, r in sorted(s4.items())},
                                       sort_keys=True).encode()).hexdigest()[:16]
    return dict(l2=str(cfg.layer("L2_logs")), l3=str(cfg.layer("L3_timeline")), l4=str(cfg.layer("L4_quality")),
                out=str(cfg.layer(LAYER)), inputs=inputs, **p,
                vmax=cfg.release["s3_timeline"]["gps_max_speed_kmh"])


def _read(path: Path, columns: list[str]) -> pa.Table:
    if not path.exists():
        return pa.table({})
    names = pq.read_schema(path).names
    return pq.read_table(path, columns=[c for c in columns if c in names])


def _num(table: pa.Table, name: str, n: int) -> np.ndarray:
    if name not in table.column_names:
        return np.full(n, np.nan)
    return np.array([np.nan if v is None else v for v in table[name].to_pylist()], dtype=float)


def _times(table: pa.Table, name: str, n: int) -> np.ndarray:
    if name not in table.column_names or not n:
        return np.full(n, NAT, dtype=np.int64)
    return to_us(table[name].to_numpy(zero_copy_only=False).astype("datetime64[us]"))


def _write(path: Path, table: pa.Table) -> None:
    with atomic_path(path) as tmp:
        pq.write_table(table, tmp, compression="zstd")


def _ts(us) -> pa.Array:
    arr = np.asarray(us, dtype=np.int64)
    return pa.array(np.where(arr == NAT, 0, arr), pa.timestamp("us"), mask=arr == NAT)


def _rate(a, b):
    return dict(n=int(b), hit=int(a), share=round(a / b, 6) if b else None)


def _hydrogen(rest, kind, kg, daily) -> dict:
    """Summary of the restored hydrogen counter: wraps, totals, and kg per 100 km and per kWh
    (FcVol x FcCur) over days with both odometer distance and attributed consumption."""
    valid = np.isfinite(rest)
    c = Counter(k for k in kind.tolist() if k)
    d = daily[(daily["km_qualified"] > 0) & (daily["h2_kg"] > 0)] if len(daily) else daily
    km, h, e = (float(d[col].sum()) if len(d) else 0.0 for col in ("km_qualified", "h2_kg", "energy_kwh_raw_units"))
    return dict(readings=int(valid.sum()), wraps=int(c.get("wrap", 0)), wraps_inferred=int(c.get("wrap_inferred", 0)),
                backward=int(c.get("backward", 0)), kg_restored_span=float(rest[valid][-1] - rest[valid][0]) if valid.any() else 0.0,
                kg_attributed=float(kg.sum()), days_with_both=int(len(d)), km_on_those_days=km, kg_on_those_days=h,
                kwh_on_those_days=e, kg_per_100km=round(h / km * 100, 3) if km > 0 else None,
                kg_per_kwh_raw_units=round(h / e, 5) if e > 0 else None)


def process(device: str, ctx: dict) -> dict:
    out, l3, l4 = Path(ctx["out"]), Path(ctx["l3"]), Path(ctx["l4"])

    # ---- operation stream (stored + additions) in receipt order, with stage-4 segments
    st = _read(l3 / "operation" / f"{device}.parquet", OP_COLUMNS)
    ad = _read(l3 / "additions" / f"{device}.parquet", OP_COLUMNS)
    ns, na = st.num_rows, (ad.num_rows if "time" in ad.column_names else 0)
    flags = _read(l4 / "operation_flags" / f"{device}.parquet", ["segment", "HyBottleP_corrected"])
    cat = lambda f: np.concatenate([_num(st, f, ns), _num(ad, f, na)])
    t = np.concatenate([_times(st, "time", ns), _times(ad, "time", na)])
    t_dev = np.concatenate([_times(st, "time_device", ns), _times(ad, "time_device", na)])
    origin = np.concatenate([np.zeros(ns, np.int8), np.ones(na, np.int8)])
    row = np.concatenate([np.arange(ns), np.arange(na)]).astype(np.int64)
    order = np.lexsort((row, origin, t))
    ts, tdev = t[order], t_dev[order]
    seg = flags["segment"].to_numpy()[order]
    fc_cur, fc_vol, soc = cat("FcCur")[order], cat("FcVol")[order], cat("SOC")[order]
    km, fault, h2 = cat("TotKM")[order], cat("main_state")[order], cat("TotH2Kg")[order]
    h2p = _num(flags, "HyBottleP_corrected", ns + na)[order]
    n = len(ts)

    # ---- GPS (stored + additions) in device-time order, with fix status
    gs = _read(l3 / "gps" / f"{device}.parquet", GPS_COLUMNS + ["match_event_id"])
    ga = _read(l3 / "gps_additions" / f"{device}.parquet", GPS_COLUMNS + ["result_raw"])
    gns, gna = gs.num_rows, (ga.num_rows if "time" in ga.column_names else 0)
    gflags = _read(l4 / "gps_flags" / f"{device}.parquet", ["device_clock_suspect"])
    gt = np.concatenate([_times(gs, "time", gns), _times(ga, "time", gna)])
    grecv = np.concatenate([_times(gs, "time_received_ms", gns), _times(ga, "time_received_ms", gna)])
    lat_raw = np.concatenate([_num(gs, "LAT", gns), _num(ga, "LAT", gna)])
    lon_raw = np.concatenate([_num(gs, "LON", gns), _num(ga, "LON", gna)])
    ns_l = (gs["LATD"].to_pylist() if gns else []) + (ga["LATD"].to_pylist() if gna else [])
    ew_l = (gs["LOND"].to_pylist() if gns else []) + (ga["LOND"].to_pylist() if gna else [])
    variants = np.concatenate([_num(gs, "same_time_variants", gns), _num(ga, "same_time_variants", gna)])
    suspect = gflags["device_clock_suspect"].to_numpy(zero_copy_only=False) if gflags.num_rows else np.zeros(0, bool)
    result = np.full(gns + gna, None, dtype=object)
    gps_log = Path(ctx["l2"]) / "gps" / f"{device}.parquet"
    if gns and gps_log.exists():
        lookup = dict(zip(*pq.read_table(gps_log, columns=["event_id", "result_raw"]).to_pydict().values()))
        result[:gns] = [lookup.get(e) for e in gs["match_event_id"].to_pylist()]
    if gna:
        result[gns:] = ga["result_raw"].to_pylist()
    gorder = np.argsort(gt, kind="stable")
    gts = gt[gorder]
    lat = motion.degrees(lat_raw, ns_l, "S", 90)[gorder]
    lon = motion.degrees(lon_raw, ew_l, "W", 180)[gorder]
    res = result[gorder]
    km_ok = np.isfinite(km) & (km > 0)
    proxy = derive.fix_proxy(gts, lat, lon, ctx["gps_no_fix_min_speed_kmh"], ts[km_ok], km[km_ok])
    known = np.array([r in ("0", "1") for r in res], dtype=bool)
    status = np.where(known, np.where(res == "1", derive.VALID, derive.NO_FIX),
                      np.where(proxy, derive.NO_FIX_INFERRED, derive.VALID_ASSUMED)).astype(object)
    repeat = np.r_[False, (lat[1:] == lat[:-1]) & (lon[1:] == lon[:-1])]
    proxy_check = dict(known=int(known.sum()),
                       result0=int((known & (res == "0")).sum()),
                       proxy_on_result0=int((known & (res == "0") & proxy).sum()),
                       proxy_on_result1=int((known & (res == "1") & proxy).sum()),
                       result0_repeating=int((known & (res == "0") & repeat).sum()),
                       # no-fix fixes the proxy would keep: repeated position (vehicle not moving by the rule) or not
                       result0_missed_repeating=int((known & (res == "0") & ~proxy & repeat).sum()),
                       result0_missed_new_position=int((known & (res == "0") & ~proxy & ~repeat).sum()))
    usable = (np.isin(status, [derive.VALID, derive.VALID_ASSUMED]) & (variants[gorder] == 1)
              & ~suspect[gorder] & np.isfinite(lat) & np.isfinite(lon))
    step_km, speed, step_s = derive.track_steps(gts, lat, lon, usable, ctx["gps_track_max_gap_s"])
    plausible = np.isfinite(speed) & (speed <= ctx["vmax"])

    # ---- odometer intervals
    km_inc, km_kind, km_prev = derive.odometer_steps(ts, km, seg, ctx["odometer_max_speed_kmh"],
                                               ctx["odometer_gap_attribution_max_s"])
    qualified = np.isin(km_kind, DISTANCE_KINDS)

    # ---- hydrogen counter (TotH2Kg, integer kg, wraps at h2_counter_period_kg): restored per TBOX
    h2_rest, h2_inc, h2_kind = derive.counter_steps(h2, np.where(km > 0, km, np.nan), ctx["h2_counter_period_kg"],
                                                    ctx["h2_wrap_inference_min_km"])
    h2_valid = np.flatnonzero(np.isfinite(h2_rest))
    h2_prev_t = np.full(n, NAT, dtype=np.int64)
    if len(h2_valid) > 1:
        h2_prev_t[h2_valid[1:]] = ts[h2_valid[:-1]]
    # consumption attributed to a day / trip: forward steps over gaps <= odometer_gap_attribution_max_s
    h2_attr = np.isin(h2_kind, ["increment", "wrap", "wrap_inferred"]) & (h2_prev_t != NAT) & \
        ((ts - h2_prev_t) / 1e6 <= ctx["odometer_gap_attribution_max_s"])
    h2_kg = np.where(h2_attr, h2_inc, 0.0)

    # ---- trips: segments joined across short gaps or qualified odometer rises
    gaps = _read(l4 / "gaps" / f"{device}.parquet", ["after_segment", "duration_s", "km_delta"]).to_pandas()
    seg_ids = np.unique(seg)
    trip_of = {s: 0 for s in seg_ids}
    trip = 0
    gap_by_seg = {int(r.after_segment): r for r in gaps.itertuples()} if len(gaps) else {}
    for s in seg_ids[1:]:
        g = gap_by_seg.get(int(s) - 1)
        # an odometer rise joins only when it implies driving through the gap (not a parked vehicle)
        hours = g.duration_s / 3600 if g is not None else np.nan
        joined = g is not None and (g.duration_s <= ctx["trip_merge_gap_s"] or
                                    (g.duration_s <= ctx["odometer_gap_attribution_max_s"]
                                     and np.isfinite(g.km_delta) and g.km_delta >= 1
                                     and ctx["trip_merge_min_speed_kmh"] * hours <= g.km_delta
                                     <= ctx["odometer_max_speed_kmh"] * hours + 1))
        trip += 0 if joined else 1
        trip_of[s] = trip
    trip_id = np.array([trip_of[s] for s in seg])

    # ---- GPS that stays put while the odometer advances (receiver stuck or jittering in place)
    static = np.zeros(len(gts), dtype=bool)
    trip_ids = np.unique(trip_id)
    tstart = np.array([ts[trip_id == k][0] for k in trip_ids]) if n else np.array([], np.int64)
    tend = np.array([ts[trip_id == k][-1] for k in trip_ids]) if n else np.array([], np.int64)
    for k, a_t, b_t in zip(trip_ids, tstart, tend):
        km_trip = float(np.nansum(np.where((trip_id == k) & qualified, km_inc, 0)))
        sel = (gts >= a_t - 60e6) & (gts <= b_t)
        gkm = float(np.nansum(np.where(sel & np.isfinite(speed) & (speed <= ctx["vmax"]), step_km, 0)))
        if km_trip >= ctx["gps_static_min_km"] and gkm < ctx["gps_static_share"] * km_trip:
            static |= sel
    status = np.where(static & usable, "static_while_moving", status).astype(object)
    usable &= ~static
    step_km, speed, step_s = derive.track_steps(gts, lat, lon, usable, ctx["gps_track_max_gap_s"])
    plausible = np.isfinite(speed) & (speed <= ctx["vmax"])

    # ---- delayed-delivery check: GPS (device clock) around each in_segment_delayed increment
    delayed_check = derive.gap_check(ts, km_inc, km_kind == "in_segment_delayed", km_prev, gts[usable], lat[usable],
                                     lon[usable], ctx["delayed_check_window_s"])
    ctrl = np.flatnonzero((km_kind == "in_segment") & (km_inc >= 1))
    ctrl_mask = np.zeros(n, dtype=bool)
    ctrl_mask[ctrl[np.linspace(0, len(ctrl) - 1, min(len(ctrl), 2000)).astype(int)] if len(ctrl) else []] = True
    delayed_check["control"] = derive.gap_check(ts, km_inc, ctrl_mask, km_prev, gts[usable], lat[usable], lon[usable],
                                                ctx["delayed_check_window_s"])

    # ---- CAN-GPS alignment (CAN receipt clock vs GPS device clock; same-clock variants)
    g_use = np.flatnonzero(usable)
    gu_t = gts[g_use]
    idx, off = derive.align(ts, gu_t, ctx["alignment_tolerance_s"], "nearest")
    any_gps, _ = derive.align(ts, gts, ctx["alignment_tolerance_s"], "nearest")
    unmatched = dict(gps_unusable_within_tolerance=int(((idx == -1) & (any_gps != -1)).sum()),
                     no_gps_within_tolerance=int(((idx == -1) & (any_gps == -1)).sum()))
    grid = {}
    for mode in ("nearest", "backward"):
        for tol in ctx["alignment_grid_s"]:
            i2, _ = derive.align(ts, gu_t, tol, mode)
            grid[f"{mode}:{tol}s"] = _rate(int((i2 >= 0).sum()), n)
    has_dev = tdev != NAT
    i_dev, _ = derive.align(tdev[has_dev], gu_t, ctx["alignment_tolerance_s"], "nearest")
    recv_ok = grecv[gorder][g_use] != NAT
    r_order = np.argsort(grecv[gorder][g_use][recv_ok], kind="stable")
    gr_t = grecv[gorder][g_use][recv_ok][r_order]
    i_rec, _ = derive.align(ts, gr_t, ctx["alignment_tolerance_s"], "nearest")
    both = (idx[has_dev] >= 0) & (i_dev >= 0)
    diff_km = np.array([])
    if both.any():
        a, b = g_use[idx[has_dev][both]], g_use[i_dev[both]]
        diff_km = motion.haversine_km(lat[a], lon[a], lat[b], lon[b])
    clocks = dict(receipt_vs_device=_rate(int((idx >= 0).sum()), n),
                  device_vs_device=_rate(int((i_dev >= 0).sum()), int(has_dev.sum())),
                  receipt_vs_device_on_same_rows=_rate(int((idx[has_dev] >= 0).sum()), int(has_dev.sum())),
                  receipt_vs_receipt=_rate(int((i_rec >= 0).sum()), n),
                  same_fix_chosen=_rate(int((g_use[idx[has_dev][both]] == g_use[i_dev[both]]).sum()) if both.any() else 0,
                                        int(both.sum())),
                  chosen_fix_distance_km=dict(zip(["p50", "p90", "p99"],
                                                  np.percentile(diff_km, [50, 90, 99]).round(4).tolist()))
                  if len(diff_km) else {})

    # ---- fuel-cell runs
    dt_next = np.r_[np.diff(ts) / 1e6, np.nan]
    dt_ok = (dt_next > 0) & (dt_next <= 60) & np.r_[seg[1:] == seg[:-1], False]
    on = np.isfinite(fc_cur) & (fc_cur > ctx["fc_on_current_a"])
    power_kw = np.where(np.isfinite(fc_vol) & np.isfinite(fc_cur), fc_vol * fc_cur / 1000, np.nan)
    run_rows = []
    for a, b in derive.runs(on, seg):
        span = slice(a, b)
        dur = float(np.nansum(np.where(dt_ok[span] & on[span], dt_next[span], 0)))
        energy = float(np.nansum(np.where(dt_ok[span], power_kw[span] * dt_next[span] / 3600, 0)))
        s_ok = soc[span][np.isfinite(soc[span])]
        run_rows.append(dict(segment=int(seg[a]), start=int(ts[a]), end=int(ts[b - 1]), records=b - a, on_s=dur,
                             energy_kwh_raw_units=energy, mean_power_kw=float(np.nanmean(power_kw[span])),
                             p95_power_kw=float(np.nanpercentile(power_kw[span], 95)) if np.isfinite(power_kw[span]).any() else np.nan,
                             soc_start=float(s_ok[0]) if len(s_ok) else np.nan, soc_end=float(s_ok[-1]) if len(s_ok) else np.nan))
    runs_df = pd.DataFrame(run_rows)

    # ---- refuelling events (corrected HyBottleP, consecutive non-zero readings)
    pi = np.flatnonzero(np.isfinite(h2p) & (h2p > 0))
    rise = h2p[pi[1:]] - h2p[pi[:-1]] if len(pi) > 1 else np.array([])
    ev = np.flatnonzero(rise >= ctx["refuel_min_rise"])
    refuels = pd.DataFrame(dict(before=ts[pi[:-1]][ev] if len(pi) > 1 else [], after=ts[pi[1:]][ev] if len(pi) > 1 else [],
                                p_before=h2p[pi[:-1]][ev] if len(pi) > 1 else [], p_after=h2p[pi[1:]][ev] if len(pi) > 1 else [],
                                across_segments=(seg[pi[1:]][ev] != seg[pi[:-1]][ev]) if len(pi) > 1 else []))

    trip_rows = []
    bounds = np.flatnonzero(np.diff(np.r_[-1, trip_id, trip_id[-1] + 1 if n else 0]))
    run_trip = trip_id[np.searchsorted(ts, runs_df["start"].to_numpy(), side="left")] if len(runs_df) else np.array([])
    ref_trip = trip_id[np.searchsorted(ts, refuels["after"].to_numpy(), side="left")] if len(refuels) else np.array([])
    for k, (a, b) in enumerate(zip(bounds[:-1], bounds[1:])):
        span = slice(a, b)
        start, end = int(ts[a]), int(ts[b - 1])
        active = float(np.nansum(np.where(dt_ok[span], dt_next[span], 0)))
        in_trip = (gts >= start - 60e6) & (gts <= end)
        s_ok, p_ok = soc[span][np.isfinite(soc[span])], h2p[span][np.isfinite(h2p[span]) & (h2p[span] > 0)]
        first_fix = np.flatnonzero(in_trip & usable)
        trip_rows.append(dict(
            trip=k, start=start, end=end, span_s=(end - start) / 1e6, active_s=active,
            segments=int(len(np.unique(seg[span]))), records=b - a,
            km_qualified=float(np.nansum(np.where(qualified[span] & (np.arange(a, b) > a), km_inc[span], 0))),
            h2_kg=float(np.sum(np.where(np.arange(a, b) > a, h2_kg[span], 0))),
            fc_on_s=float(runs_df.loc[run_trip == k, "on_s"].sum()) if len(runs_df) else 0.0,
            fc_runs=int((run_trip == k).sum()), energy_kwh_raw_units=float(runs_df.loc[run_trip == k, "energy_kwh_raw_units"].sum()) if len(runs_df) else 0.0,
            soc_start=float(s_ok[0]) if len(s_ok) else np.nan, soc_end=float(s_ok[-1]) if len(s_ok) else np.nan,
            soc_min=float(s_ok.min()) if len(s_ok) else np.nan, soc_max=float(s_ok.max()) if len(s_ok) else np.nan,
            h2_pressure_start=float(p_ok[0]) if len(p_ok) else np.nan, h2_pressure_end=float(p_ok[-1]) if len(p_ok) else np.nan,
            refuels=int((ref_trip == k).sum()), fault_state_records=int((fault[span] == ctx["fault_state_code"]).sum()),
            gps_fixes=int(in_trip.sum()), gps_usable=int((in_trip & usable).sum()),
            gps_no_fix=int((in_trip & np.isin(status, [derive.NO_FIX, derive.NO_FIX_INFERRED])).sum()),
            gps_km=float(np.nansum(np.where(in_trip & plausible, step_km, 0))),
            gps_covered_s=float(np.nansum(np.where(in_trip & plausible, step_s, 0))),
            start_cell=f"{np.round(lat[first_fix[0]], ctx['grid_decimals'])},{np.round(lon[first_fix[0]], ctx['grid_decimals'])}" if len(first_fix) else None,
            end_cell=f"{np.round(lat[first_fix[-1]], ctx['grid_decimals'])},{np.round(lon[first_fix[-1]], ctx['grid_decimals'])}" if len(first_fix) else None))
    trips_df = pd.DataFrame(trip_rows)
    if len(trips_df):
        # TotKM is not reported before mid-2025: the GPS track then decides
        trips_df.insert(trips_df.columns.get_loc("km_qualified") + 1, "moving",
                        (trips_df["km_qualified"] >= ctx["trip_moving_min_km"])
                        | (trips_df["gps_km"] >= ctx["trip_moving_min_km"]))

    # ---- daily statistics (receipt-clock day of each record / interval start)
    day = ts.astype("datetime64[us]").astype("datetime64[D]")
    gday = gts.astype("datetime64[us]").astype("datetime64[D]")
    daily = pd.DataFrame({"day": day, "records": 1, "active_s": np.where(dt_ok, dt_next, 0),
                          "km_qualified": np.where(qualified, km_inc, 0),
                          "km_delayed": np.where(km_kind == "in_segment_delayed", km_inc, 0), "h2_kg": h2_kg, "fc_on_s": np.where(dt_ok & on, dt_next, 0),
                          "energy_kwh_raw_units": np.where(dt_ok, np.nan_to_num(power_kw) * np.nan_to_num(dt_next) / 3600, 0),
                          "aligned": idx >= 0}).groupby("day").sum()
    gd = pd.DataFrame({"day": gday, "gps_fixes": 1, "gps_usable": usable,
                       "gps_no_fix": np.isin(status, [derive.NO_FIX, derive.NO_FIX_INFERRED]),
                       "gps_km": np.where(plausible, step_km, 0)}).groupby("day").sum()
    daily = daily.join(gd, how="outer").fillna(0)
    for label, frame, col in (("fc_starts", runs_df, "start"), ("trips", trips_df, "start"), ("refuels", refuels, "after")):
        counts = pd.Series(frame[col].to_numpy().astype("datetime64[us]").astype("datetime64[D]") if len(frame) else [],
                           dtype="datetime64[s]").value_counts()
        daily[label] = counts.reindex(daily.index).fillna(0).astype(int) if len(counts) else 0
    daily = daily.reset_index()
    month = pd.to_datetime(daily["day"]).dt.to_period("M").astype(str) if len(daily) else pd.Series([], dtype=str)
    monthly = daily.drop(columns="day").assign(month=month, days=1).groupby("month").sum().reset_index()

    # ---- outputs
    gorigin = np.concatenate([np.zeros(gns, np.int8), np.ones(gna, np.int8)])[gorder]
    grow = np.concatenate([np.arange(gns), np.arange(gna)]).astype(np.int64)[gorder]
    _write(out / "gps_track" / f"{device}.parquet", pa.table({
        "origin": pa.array(np.where(gorigin == 0, "stored", "addition").tolist(), pa.string()), "row": pa.array(grow),
        "time": _ts(gts), "fix_status": pa.array(status.tolist(), pa.string()),
        "fix_source": pa.array(np.where(known, "result", "proxy").tolist(), pa.string()),
        "usable": pa.array(usable), "step_km": pa.array(step_km), "speed_kmh": pa.array(speed)}))
    _write(out / "alignment" / f"{device}.parquet", pa.table({
        "origin": pa.array(np.where(origin[order] == 0, "stored", "addition").tolist(), pa.string()),
        "row": pa.array(row[order]), "time": _ts(ts),
        "gps_origin": pa.array([None if i < 0 else ("stored" if gorigin[g_use[i]] == 0 else "addition") for i in idx], pa.string()),
        "gps_row": pa.array([None if i < 0 else int(grow[g_use[i]]) for i in idx], pa.int64()),
        "offset_s": pa.array(off), "status": pa.array(np.where(idx >= 0, "matched", np.where(idx == -2, "ambiguous", "no_fix_within_tolerance")).tolist(), pa.string()),
        "km_increment": pa.array(km_inc), "km_kind": pa.array(km_kind.tolist(), pa.string()),
        "h2_kg_restored": pa.array(h2_rest), "h2_increment": pa.array(h2_inc), "h2_kind": pa.array(h2_kind.tolist(), pa.string()),
        "h2_attributed": pa.array(h2_attr)}))
    for name, frame in (("fc_runs", runs_df), ("trips", trips_df), ("refuels", refuels), ("daily", daily),
                        ("monthly", monthly)):
        table = pa.Table.from_pandas(frame, preserve_index=False) if len(frame) else pa.table({"empty": pa.array([], pa.int8())})
        for c in ("start", "end", "before", "after"):
            if c in table.column_names:
                table = table.set_column(table.schema.get_field_index(c), c, _ts(frame[c].to_numpy()))
        _write(out / name / f"{device}.parquet", table)

    off_ok = off[np.isfinite(off)]
    return dict(
        records=n, gps_fixes=int(len(gts)),
        fix_status=dict(Counter(status.tolist())), fix_source=dict(Counter(np.where(known, "result", "proxy").tolist())),
        proxy_check=proxy_check,
        gps_track=dict(usable=int(usable.sum()), steps=int(np.isfinite(speed).sum()), implausible=int((np.isfinite(speed) & ~plausible).sum()),
                       km=float(np.nansum(np.where(plausible, step_km, 0)))),
        delayed_check=delayed_check,
        hydrogen=_hydrogen(h2_rest, h2_kind, h2_kg, daily),
        odometer=dict(Counter(k for k in km_kind.tolist() if k)) | dict(
            km_in_segment=float(np.nansum(np.where(km_kind == "in_segment", km_inc, 0))),
            km_in_segment_delayed=float(np.nansum(np.where(km_kind == "in_segment_delayed", km_inc, 0))),
            km_across_gap=float(np.nansum(np.where(km_kind == "across_gap", km_inc, 0))),
            km_across_long_gap=float(np.nansum(np.where(km_kind == "across_long_gap", km_inc, 0)))),
        alignment=dict(main=_rate(int((idx >= 0).sum()), n), ambiguous=int((idx == -2).sum()), unmatched=unmatched,
                       offset_s=dict(zip(["p5", "p50", "p95"], np.percentile(off_ok, [5, 50, 95]).round(2).tolist())) if len(off_ok) else {},
                       grid=grid, clocks=clocks,
                       gps_reuse=int(pd.Series(idx[idx >= 0]).value_counts().gt(1).sum()) if (idx >= 0).any() else 0),
        fc_runs=dict(count=len(run_rows), on_s=float(runs_df["on_s"].sum()) if len(runs_df) else 0.0,
                     energy_kwh_raw_units=float(runs_df["energy_kwh_raw_units"].sum()) if len(runs_df) else 0.0),
        trips=dict(count=len(trip_rows), moving=int(trips_df["moving"].sum()) if len(trips_df) else 0,
                   segments=int(len(seg_ids)),
                   span_over_24h=int((trips_df["span_s"] > 86400).sum()) if len(trips_df) else 0,
                   active_h=float(trips_df["active_s"].sum() / 3600) if len(trips_df) else 0.0,
                   km_qualified=float(trips_df["km_qualified"].sum()) if len(trips_df) else 0.0,
                   gps_km=float(trips_df["gps_km"].sum()) if len(trips_df) else 0.0),
        refuels=dict(count=int(len(refuels)), across_segments=int(refuels["across_segments"].sum()) if len(refuels) else 0,
                     rise_hist=np.histogram(rise[rise > 0], bins=[0, 1, 2, 5, 10, 15, 20, 30, np.inf])[0].tolist() if len(rise) else []),
        days=int(len(daily)),
        sha256={k: sha256_file(out / k / f"{device}.parquet")
                for k in ("gps_track", "alignment", "fc_runs", "trips", "refuels", "daily", "monthly")})

"""Figure-ready statistics from the release and the stage manifests (no figures are drawn here).

Runs like a stage (one vehicle per worker, then `finalize`); outputs in stats/v<version>/.
Every figure and table of the paper reads only these files.

Per vehicle, from the release files:
- intervals:      vehicle x month x stream x interval bin (receipt clock / GPS device clock, within a unit)
- field_quality:  vehicle x month x field: records, missing, zero, outside encodable, all-ones, log mismatch
- hist1d/hist2d:  value histograms on fixed bins (`[stats]`), 2-D pairs while the fuel cell is on
- gps_quality:    vehicle x month: fix status counts, usable fixes, implausible steps
- clock_lag:      fleet histograms of receipt minus stored / verified device time
- odometer_daily: vehicle x unit x day: positive cumulative odometer min/max and records
Fleet level (finalize), from release tables and stage manifests (TBOX -> vehicle via the private map;
outputs carry vehicle ids only):
- accounting, coverage, consistency_daily, trip_distance, alignment_tolerance, clock, delayed_check,
  handover, correction, segments_summary, fleet_daily, vehicles, key_numbers.json, README.md (figure <-> table map)
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
from ..io import atomic_path, read_json, sha256_file, write_json

NAME = "stats"
SECTION = "stats"
PARAM_SECTIONS = (SECTION,)
CODE = (io,)
LAYER = "stats/v1.0"
INTERVAL_BINS = [0, 1, 2, 5, 6, 10, 30, 60, 300, 3600, 86400, np.inf]
PER_VEHICLE = ("intervals", "field_quality", "hist1d", "hist2d", "gps_quality", "clock_lag", "odometer_daily", "log_additions_monthly", "clock_lag_signed", "unit_span", "stack_voltage", "clock_integration")
RECEIPT_LAG_BINS = np.linspace(0, 2, 41)
DEVICE_LAG_BINS = np.concatenate((-np.geomspace(.001, 60, 25)[::-1], [0], np.geomspace(.001, 86400, 65)))
DURATION_BINS = np.concatenate(([0], np.geomspace(1, 86400, 65)))
CORRECTION_COUNTS = ("stored", "reconstructed", "not_reconstructable", "compared", "corrected_equal",
                     "stored_equal_HyBottleP", "stored_equal_HighPPMNum")


def prepare(cfg, devices, force=False, workers=1, options=None) -> dict:
    rel = cfg.layer("L7_release")
    s7 = read_json(rel / "_manifest.json", {"devices": {}})["devices"]
    bits = pd.read_csv(rel / "field_bits.csv")
    inputs = hashlib.sha256(json.dumps({d: r.get("sha256") for d, r in sorted(s7.items())}, sort_keys=True).encode()
                            ).hexdigest()[:16]
    return dict(rel=str(rel), out=str(cfg.layer(LAYER)), devices=sorted(s7), inputs=inputs,
                bits=[(int(r.bit), r.field) for r in bits.itertuples() if not r.field.startswith("counter:") and r.released],
                **cfg.release[SECTION])


def _write(path: Path, frame: pd.DataFrame) -> None:
    table = pa.Table.from_pandas(frame, preserve_index=False) if len(frame.columns) else pa.table({"empty": pa.array([], pa.int8())})
    with atomic_path(path) as tmp:
        pq.write_table(table, tmp, compression="zstd")


def _intervals(frame: pd.DataFrame, stream: str) -> pd.DataFrame:
    if len(frame) < 2:
        return pd.DataFrame()
    t = frame["time"].to_numpy().astype("datetime64[us]").astype(np.int64)
    same_unit = frame["unit"].to_numpy()[1:] == frame["unit"].to_numpy()[:-1]
    dt = np.diff(t)[same_unit] / 1e6
    month = frame["time"].dt.to_period("M").astype(str).to_numpy()[1:][same_unit]
    b = np.digitize(dt, INTERVAL_BINS[1:-1], right=True)
    g = pd.DataFrame({"month": month, "bin": b}).value_counts().rename("count").reset_index()
    g["bin_left_s"] = [INTERVAL_BINS[i] for i in g["bin"]]
    g["bin_right_s"] = [INTERVAL_BINS[i + 1] for i in g["bin"]]
    return g.drop(columns="bin").assign(stream=stream)


def _hist(values: np.ndarray, spec) -> np.ndarray:
    lo, hi, step = spec
    edges = np.arange(lo, hi + step / 2, step)
    v = values[np.isfinite(values)]
    return np.histogram(np.clip(v, lo, hi - step / 2), bins=edges)[0], edges


def _binned(values: np.ndarray, edges: np.ndarray) -> pd.DataFrame:
    """Left-closed bins, last finite bin right-closed; retain both overflow tails."""
    v = values[np.isfinite(values)]
    b = np.searchsorted(edges, v, side="right")
    b[v == edges[-1]] = len(edges) - 1
    bounds = np.concatenate(([-np.inf], edges, [np.inf]))
    return pd.DataFrame({"bin_left": bounds[:-1], "bin_right": bounds[1:],
                         "count": np.bincount(b, minlength=len(bounds) - 1)})


def _clock_lag(op: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for kind, column, edges in (("receipt_minus_time", "time", RECEIPT_LAG_BINS),
                                ("receipt_minus_device", "time_device", DEVICE_LAG_BINS)):
        lag = (op["time_received_ms"] - op[column]).dt.total_seconds().to_numpy()
        rows.append(_binned(lag, edges).assign(kind=kind))
    return pd.concat(rows, ignore_index=True)[["kind", "bin_left", "bin_right", "count"]]


def _correction(s4: dict, dv: dict) -> pd.DataFrame:
    rows = []
    for dev, (vid, _) in dv.items():
        c = s4[dev]["correction"]
        counts = {**c, **c.get("verification", {})}
        rows.append(dict(vehicle_id=vid, **{k: int(counts[k]) if c["applied"] else 0 for k in CORRECTION_COUNTS}))
    return pd.DataFrame(rows, columns=["vehicle_id", *CORRECTION_COUNTS]).groupby("vehicle_id", as_index=False).sum()


def _segments_summary(rel: Path) -> pd.DataFrame:
    rows = []
    segments = pd.read_parquet(rel / "segments.parquet")
    gaps = pd.read_parquet(rel / "gaps.parquet")
    if segments.empty:
        segments = pd.DataFrame(columns=["duration_s"])
    if gaps.empty:
        gaps = pd.DataFrame(columns=["label", "duration_s"])
    groups = [("segments", "all", segments), *(("gaps", label, g) for label, g in gaps.groupby("label"))]
    for kind, label, frame in groups:
        v = frame["duration_s"].to_numpy(dtype=float)
        hist = _binned(v, DURATION_BINS)
        b = np.searchsorted(DURATION_BINS, v, side="right")
        b[v == DURATION_BINS[-1]] = len(DURATION_BINS) - 1
        ok = np.isfinite(v)
        hist["duration_s"] = np.bincount(b[ok], weights=v[ok], minlength=len(hist))
        rows.append(hist.assign(kind=kind, label=label))
    return pd.concat(rows, ignore_index=True)[["kind", "label", "bin_left", "bin_right", "count", "duration_s"]]


def _clock_integration(op: pd.DataFrame, fc_on_a: float) -> pd.DataFrame:
    """Fuel-cell on-time and stack energy integrated with the receipt clock and with the device clock,
    over the same consecutive record pairs (same installation and segment, both with a verified device
    time). Stage-5 rule for each clock: interval to the next record, 0 < dt <= 60 s, fuel cell on when
    FcCur > fc_on_a at the first record."""
    need = {"time", "time_device", "unit", "segment", "FcCur", "FcVol"}
    if not need <= set(op.columns) or len(op) < 2:
        return pd.DataFrame()
    t = op["time"].to_numpy().astype("datetime64[us]").astype(np.int64) / 1e6
    td = op["time_device"].to_numpy().astype("datetime64[us]").astype(np.int64) / 1e6
    has = op["time_device"].notna().to_numpy()
    pair = has[:-1] & has[1:] & (op["unit"].to_numpy()[1:] == op["unit"].to_numpy()[:-1]) \
        & (op["segment"].to_numpy()[1:] == op["segment"].to_numpy()[:-1])
    dr, dd = np.diff(t)[pair], np.diff(td)[pair]
    cur, vol = op["FcCur"].to_numpy(dtype=float)[:-1][pair], op["FcVol"].to_numpy(dtype=float)[:-1][pair]
    on = np.isfinite(cur) & (cur > fc_on_a)
    kw = np.where(np.isfinite(cur) & np.isfinite(vol), cur * vol / 1000, 0.0)
    out = dict(pairs=int(pair.sum()), pairs_fc_on=int(on.sum()), pairs_diff_over_2s=int((np.abs(dr - dd) > 2).sum()))
    for name, d in (("receipt", dr), ("device", dd)):
        ok = (d > 0) & (d <= 60)
        out[f"on_s_{name}"] = float(np.sum(np.where(ok & on, d, 0)))
        out[f"kwh_{name}"] = float(np.sum(np.where(ok & on, kw * d / 3600, 0)))
    return pd.DataFrame([out])


def _stack_voltage(op: pd.DataFrame, month: pd.Series, spec: dict) -> pd.DataFrame:
    """Monthly stack-voltage quartiles per installation at a fixed operating point (current and coolant
    outlet temperature windows, inclusive)."""
    if not {"FcVol", "FcCur", "CoolOutActTemp"} <= set(op.columns):
        return pd.DataFrame()
    (i0, i1), (t0, t1) = spec["current_a"], spec["coolant_out_c"]
    sel = op["FcCur"].between(i0, i1) & op["CoolOutActTemp"].between(t0, t1) & op["FcVol"].notna()
    g = pd.DataFrame({"unit": op.loc[sel, "unit"], "month": month[sel], "v": op.loc[sel, "FcVol"].astype(float)})
    if g.empty:
        return pd.DataFrame()
    return g.groupby(["unit", "month"])["v"].agg(records="size", v_p25=lambda x: x.quantile(.25), v_median="median",
                                                 v_p75=lambda x: x.quantile(.75)).reset_index()


def process(vehicle: str, ctx: dict) -> dict:
    rel, out = Path(ctx["rel"]), Path(ctx["out"])
    op = pd.read_parquet(rel / "operation" / f"{vehicle}.parquet")
    gps = pd.read_parquet(rel / "gps" / f"{vehicle}.parquet")
    month = op["time"].dt.to_period("M").astype(str)

    # field quality from the published masks and values
    rows = []
    masks = {k: op[f"{k}_mask"].to_numpy(dtype=np.int64) for k in ("missing", "outside_encodable", "all_ones", "log_mismatch")}
    for bit, field in ctx["bits"]:
        if field not in op:
            continue
        frame = pd.DataFrame({"month": month, "records": 1,
                              **{k: (m >> bit) & 1 for k, m in masks.items()},
                              "zero": (op[field].to_numpy(dtype=float) == 0).astype(int)})
        g = frame.groupby("month").sum().reset_index()
        g.insert(1, "field", field)
        rows.append(g)
    tables = {"field_quality": pd.concat(rows, ignore_index=True) if rows else pd.DataFrame()}
    tables["intervals"] = pd.concat([_intervals(op, "operation"), _intervals(gps, "gps")], ignore_index=True)

    tables["clock_lag"] = _clock_lag(op)
    lag = (op["time_received_ms"] - op["time"]).dt.total_seconds().to_numpy()
    tables["clock_lag_signed"] = _binned(lag, np.linspace(-2, 2, 81))
    additions = []
    for stream, frame in (("operation", op), ("gps", gps)):
        added = frame.loc[frame["source"] == "log_addition", "time_received_ms"]
        months = added.dt.to_period("M").astype(str).value_counts().rename_axis("month").rename("count").reset_index()
        additions.append(months.assign(stream=stream))
    tables["log_additions_monthly"] = pd.concat(additions, ignore_index=True)
    tables["unit_span"] = op.groupby("unit")["time"].agg(first_time="min", last_time="max").reset_index()
    km = op.loc[op["TotKM"] > 0, ["unit", "time", "TotKM"]].copy()
    km["day"] = km["time"].dt.to_period("D").astype(str)
    tables["odometer_daily"] = km.groupby(["unit", "day"])["TotKM"].agg(
        km_min="min", km_max="max", records="size").reset_index()

    tables["stack_voltage"] = _stack_voltage(op, month, ctx["stack_voltage"])
    tables["clock_integration"] = _clock_integration(op, ctx["fc_on_current_a"])

    # histograms
    h1 = []
    for field, spec in ctx["hist1d"].items():
        if field in op:
            counts, edges = _hist(op[field].to_numpy(dtype=float), spec)
            h1.append(pd.DataFrame({"field": field, "bin_left": edges[:-1], "bin_right": edges[1:], "count": counts}))
    tables["hist1d"] = pd.concat(h1, ignore_index=True) if h1 else pd.DataFrame()
    on = op["FcCur"].to_numpy(dtype=float) > ctx["fc_on_current_a"] if "FcCur" in op else np.zeros(len(op), bool)
    values = {c: op[c].to_numpy(dtype=float) for c in op.columns if op[c].dtype.kind in "fi"}
    if "FcVol" in values and "FcCur" in values:
        values["fc_power_kw"] = values["FcVol"] * values["FcCur"] / 1000
    h2 = []
    for name, (x, xs, y, ys) in ctx["hist2d"].items():
        if x not in values or y not in values:
            continue
        xv, yv = values[x][on], values[y][on]
        ok = np.isfinite(xv) & np.isfinite(yv)
        xe, ye = np.arange(xs[0], xs[1] + xs[2] / 2, xs[2]), np.arange(ys[0], ys[1] + ys[2] / 2, ys[2])
        c, _, _ = np.histogram2d(np.clip(xv[ok], xs[0], xs[1] - xs[2] / 2), np.clip(yv[ok], ys[0], ys[1] - ys[2] / 2), bins=[xe, ye])
        i, j = np.nonzero(c)
        h2.append(pd.DataFrame({"pair": name, "x": x, "y": y, "x_left": xe[i], "y_left": ye[j], "count": c[i, j].astype(np.int64)}))
    tables["hist2d"] = pd.concat(h2, ignore_index=True) if h2 else pd.DataFrame()

    # GPS quality by month
    if len(gps):
        gm = gps["time"].dt.to_period("M").astype(str)
        q = pd.crosstab(gm, gps["fix_status"]).add_prefix("fix_")
        q["fixes"] = gm.value_counts()
        q["usable"] = gps.groupby(gm)["usable"].sum()
        q["steps"] = gps.groupby(gm)["speed_kmh"].count()
        q["implausible_steps"] = (gps["speed_kmh"] > 150).groupby(gm).sum()
        tables["gps_quality"] = q.rename_axis("month").reset_index()
    else:
        tables["gps_quality"] = pd.DataFrame()
    for name, frame in tables.items():
        if len(frame):
            frame.insert(0, "vehicle_id", vehicle)
        _write(out / "_parts" / name / f"{vehicle}.parquet", frame)
    return dict(records=len(op), gps_fixes=len(gps), sha256={k: sha256_file(out / "_parts" / k / f"{vehicle}.parquet") for k in tables})


# ---------------------------------------------------------------- finalize

def _concat(out: Path, name: str, vehicles: list[str]) -> pd.DataFrame:
    parts = [pd.read_parquet(out / "_parts" / name / f"{v}.parquet") for v in vehicles]
    parts = [p for p in parts if len(p) and "empty" not in p]
    return pd.concat(parts, ignore_index=True) if parts else pd.DataFrame()


def _device_vehicle(cfg) -> dict:
    m = pd.read_csv(cfg.file("vehicle_map"), dtype=str)
    m = m[m["status"].str.strip() == "confirmed"].copy()
    # device-level counts of a TBOX moved between vehicles go to its first installation
    m["order"] = pd.to_datetime(m["valid_from"].replace("", None), errors="coerce").fillna(pd.Timestamp.min)
    m = m.sort_values(["tbox", "order"]).drop_duplicates("tbox")
    return {r.tbox: (r.vehicle_id, int(r.unit)) for r in m.itertuples()}


def finalize(cfg, ctx: dict, records: dict, log=print) -> dict:
    out, rel = Path(ctx["out"]), Path(ctx["rel"])
    vehicles = sorted(records)
    for name in PER_VEHICLE:
        frame = _concat(out, name, vehicles)
        if name in ("hist1d", "hist2d", "clock_lag", "clock_lag_signed") and len(frame):  # fleet totals; per-vehicle detail is not needed for figures
            keys = [c for c in frame.columns if c not in ("vehicle_id", "count")]
            frame = frame.groupby(keys, dropna=False)["count"].sum().reset_index()
        if name == "log_additions_monthly":
            frame = frame.reindex(columns=["vehicle_id", "month", "count", "stream"])
        if name == "odometer_daily":
            frame = frame.reindex(columns=["vehicle_id", "unit", "day", "km_min", "km_max", "records"])
        _write(out / f"{name}.parquet", frame)

    dv = _device_vehicle(cfg)
    s1 = read_json(cfg.layer("L1_harmonized") / "_manifest.json")["devices"]
    s3 = read_json(cfg.layer("L3_timeline") / "_manifest.json")["devices"]
    s4 = read_json(cfg.layer("L4_quality") / "_manifest.json")["devices"]
    s5 = read_json(cfg.layer("L5_derived") / "_manifest.json")["devices"]
    s6 = read_json(cfg.layer("L6_vehicles") / "_manifest.json")["devices"]

    _write(out / "correction.parquet", _correction(s4, dv))
    _write(out / "segments_summary.parquet", _segments_summary(rel))

    # accounting (Fig. 2 / Table 3): rows per processing step and stream, per vehicle
    acc = Counter()
    for dev, (vid, unit) in dv.items():
        c, g, r3 = s1[dev]["can_run"], s1[dev]["gps"], s3[dev]
        steps = {("operation", "0_input"): c["input_rows"], ("operation", "1_null_time"): c["null_time"],
                 ("operation", "1_outside_window"): c["before_window"] + c["after_window"],
                 **{("operation", f"1_retired_{k}"): v for k, v in c["retired_reasons"].items()},
                 ("operation", "2_harmonized"): c["rows"], ("operation", "3_log_additions"): r3["additions"]["rows_released"],
                 ("gps", "0_input"): g["input_rows"], ("gps", "1_null_time"): g["null_time"],
                 ("gps", "1_outside_window"): g["before_window"] + g["after_window"], ("gps", "1_retired_duplicate"): g["duplicate_rows"],
                 ("gps", "2_harmonized"): g["rows"], ("gps", "3_log_additions"): r3["gps"]["published"],
                 ("gps", "3_log_pending_not_published"): r3["gps"]["pending_same_time_different_coords"]
                 + sum(v for k, v in r3["gps"].get("pool_clock_status", {}).items() if k != "verified"),
                 **{("log_messages", f"disposition_{k}"): v for k, v in r3["disposition"].items()},
                 **{("log_gps_fixes", f"disposition_{k}"): v for k, v in r3["gps"]["disposition"].items()}}
        for (stream, step), n in steps.items():
            acc[(vid, stream, step)] += int(n)
    for vid, r in s6.items():
        for u in r["units"]:
            acc[(vid, "operation", "4_excluded_outside_installation")] += u["excluded_before_installation"] + u["excluded_after_removal"]
            acc[(vid, "gps", "4_excluded_outside_installation")] += u["gps_excluded_before_installation"] + u["gps_excluded_after_removal"]
    for vid in vehicles:
        acc[(vid, "operation", "5_released")] += records[vid]["records"]
        acc[(vid, "gps", "5_released")] += records[vid]["gps_fixes"]
    accounting = pd.DataFrame([dict(vehicle_id=v, stream=s, step=k, rows=n) for (v, s, k), n in sorted(acc.items())])
    _write(out / "accounting.parquet", accounting)

    # coverage, consistency, usage (from release daily / trips)
    daily = pd.read_parquet(rel / "daily.parquet")
    daily["active_h"] = daily["active_s"] / 3600
    daily["fc_on_h"] = daily["fc_on_s"] / 3600
    _write(out / "coverage.parquet", daily[["vehicle_id", "day", "records", "active_h", "gps_fixes", "km_qualified", "fc_on_h", "units"]])
    cons = daily[["vehicle_id", "day", "km_qualified", "km_delayed", "gps_km", "records", "aligned", "gps_fixes", "gps_usable"]].copy()
    cons["aligned_share"] = cons["aligned"] / cons["records"].where(cons["records"] > 0)
    _write(out / "consistency_daily.parquet", cons)
    trips = pd.read_parquet(rel / "trips.parquet")
    td = trips[["vehicle_id", "trip", "start", "active_s", "km_qualified", "gps_km", "gps_covered_s", "moving"]].copy()
    td["well_covered"] = (td["km_qualified"] >= ctx["well_covered_min_km"]) & (td["gps_covered_s"] >= ctx["well_covered_share"] * td["active_s"])
    td["gps_to_odometer"] = td["gps_km"] / td["km_qualified"].where(td["km_qualified"] > 0)
    _write(out / "trip_distance.parquet", td)
    fleet = daily.groupby("day").agg(vehicles=("vehicle_id", "nunique"), records=("records", "sum"), active_h=("active_h", "sum"),
                                     km=("km_qualified", "sum"), km_delayed=("km_delayed", "sum"), gps_km=("gps_km", "sum"),
                                     fc_on_h=("fc_on_h", "sum"), trips=("trips", "sum"), refuels=("refuels", "sum")).reset_index()
    _write(out / "fleet_daily.parquet", fleet)

    # per-device manifests aggregated to vehicles (alignment grid, clock checks, delayed check)
    grid, clock, delayed = Counter(), [], []
    for dev, (vid, unit) in dv.items():
        a = s5[dev]["alignment"]
        for key, v in a["grid"].items():
            mode, tol = key.split(":")
            grid[(vid, mode, int(tol[:-1]), "hit")] += v["hit"]
            grid[(vid, mode, int(tol[:-1]), "n")] += v["n"]
        st = s3[dev]["stored"]
        clock.append(dict(vehicle_id=vid, unit=unit, stored_records=st["rows"], linked_to_log=st["linked"],
                          time_device_verified=st["time_device_status"].get("verified", 0),
                          **{f"log_clock_{k}": v for k, v in s3[dev]["clock"]["status"].items()},
                          log_messages=s3[dev]["clock"]["messages"], additions=s3[dev]["additions"]["rows_released"],
                          additions_time_device=s3[dev]["additions"]["time_device"],
                          gps_stored_linked=s3[dev]["gps"]["stored_linked"], gps_additions=s3[dev]["gps"]["published"]))
        d = s5[dev].get("delayed_check", {})
        if d:
            for which, x in (("delayed", d), ("control", d.get("control", {}))):
                delayed.append(dict(vehicle_id=vid, unit=unit, kind=which, increments=x.get("n", 0), with_gps=x.get("with_gps", 0),
                                    gap_over_120s=x.get("gap_over_120s", 0), **{f"gap_s_{k}": v for k, v in x.get("gap_s", {}).items()},
                                    **{f"displacement_ratio_{k}": v for k, v in x.get("displacement_to_odometer", {}).items()}))
    g = pd.Series(grid).unstack()
    g.index.names = ["vehicle_id", "mode", "tolerance_s"]
    _write(out / "alignment_tolerance.parquet", g.reset_index())
    _write(out / "clock.parquet", pd.DataFrame(clock).fillna(0))
    _write(out / "delayed_check.parquet", pd.DataFrame(delayed))
    _write(out / "handover.parquet", pd.DataFrame([dict(vehicle_id=v, **h) for v, r in s6.items() for h in r["handovers"]]))
    vt = pd.read_csv(rel / "vehicles.csv")
    _write(out / "vehicles.parquet", vt)

    numbers = _key_numbers(cfg, rel, vt, accounting, td, fleet, s4, s5)
    write_json(out / "key_numbers.json", numbers)
    (out / "README.md").write_text(README, encoding="utf-8")
    log(f"stats: {len(vehicles)} vehicles -> {out}")
    return numbers


def _hydrogen(rel: Path, s5: dict) -> dict:
    """Restored hydrogen counter: wraps (per TBOX, stage 5) and consumption over released vehicle-days
    with both odometer distance and attributed consumption; per-vehicle rates for >= 1000 km / kWh."""
    d = pd.read_parquet(rel / "daily.parquet", columns=["vehicle_id", "km_qualified", "h2_kg", "energy_kwh"])
    both = d[(d["km_qualified"] > 0) & (d["h2_kg"] > 0)]
    per = both.groupby("vehicle_id")[["km_qualified", "h2_kg", "energy_kwh"]].sum()
    km, kg, kwh = (float(both[c].sum()) for c in ("km_qualified", "h2_kg", "energy_kwh"))
    q = lambda x: {k: float(x.quantile(v)) for k, v in (("p10", .1), ("p50", .5), ("p90", .9))} if len(x) else {}
    h5 = [r.get("hydrogen") or {} for r in s5.values()]
    return dict(kg_attributed=float(d["h2_kg"].sum()), wraps=int(sum(h.get("wraps", 0) for h in h5)),
                wraps_inferred=int(sum(h.get("wraps_inferred", 0) for h in h5)), backward=int(sum(h.get("backward", 0) for h in h5)),
                vehicle_days_with_both=int(len(both)), km=km, kg=kg, kwh=kwh,
                fleet_kg_per_100km=kg / km * 100 if km else None, fleet_kg_per_kwh=kg / kwh if kwh else None,
                vehicle_kg_per_100km=q((per["h2_kg"] / per["km_qualified"] * 100)[per["km_qualified"] >= 1000]),
                vehicle_kg_per_kwh=q((per["h2_kg"] / per["energy_kwh"])[per["energy_kwh"] >= 1000]))


def _clock_totals(out: Path) -> dict:
    p = out / "clock_integration.parquet"
    c = pd.read_parquet(p) if p.exists() else pd.DataFrame()
    if c.empty or "pairs" not in c:
        return {}
    t = c.drop(columns=["vehicle_id"], errors="ignore").sum()
    return dict({k: float(v) for k, v in t.items()},
                on_time_ratio=float(t["on_s_receipt"] / t["on_s_device"]) if t["on_s_device"] else None,
                energy_ratio=float(t["kwh_receipt"] / t["kwh_device"]) if t["kwh_device"] else None,
                vehicles=int(c["vehicle_id"].nunique()) if "vehicle_id" in c else 0)


def _refuel_intervals(rel: Path) -> dict:
    r = pd.read_parquet(rel / "refuels.parquet")
    if r.empty:
        return {}
    r = r.sort_values(["vehicle_id", "before"])
    span_h = (r["after"] - r["before"]).dt.total_seconds() / 3600
    since_prev_h = (r["before"] - r.groupby("vehicle_id")["after"].shift()).dt.total_seconds() / 3600
    return dict(events=int(len(r)), across_segments=int(r["across_segments"].sum()),
                interval_median_h=float(span_h.median()), interval_over_24h=int((span_h > 24).sum()),
                next_within_1h=int((since_prev_h < 1).sum()))


def _key_numbers(cfg, rel, vt, accounting, td, fleet, s4, s5) -> dict:
    man = read_json(rel / "MANIFEST.json")
    a = accounting.groupby(["stream", "step"])["rows"].sum()
    wc = td[td["well_covered"]]["gps_to_odometer"]
    return dict(
        vehicles=int(len(vt)), tbox_installations=int(vt["units"].sum()), vehicles_with_tbox_change=int((vt["units"] > 1).sum()),
        period=[str(vt["first_time"].min()), str(vt["last_time"].max())], active_vehicle_days=int(vt["active_days"].sum()),
        operation_records=int(vt["records"].sum()), gps_fixes=int(vt["gps_fixes"].sum()),
        log_additions_operation=int(a.get(("operation", "3_log_additions"), 0)), log_additions_gps=int(a.get(("gps", "3_log_additions"), 0)),
        records_with_device_time=int(vt["records_device_time"].sum()),
        odometer_km=float(vt["odometer_km"].sum()), odometer_km_delayed=float(vt["odometer_km_delayed"].sum()),
        gps_track_km=float(vt["gps_track_km"].sum()), fc_on_hours=float(vt["fc_on_hours"].sum()),
        trips=int(vt["trips"].sum()), trips_moving=int(vt["trips_moving"].sum()), refuels=int(vt["refuels"].sum()),
        fields_released=int(sum(1 for f in pd.read_csv(rel / "field_bits.csv").itertuples() if f.released and not f.field.startswith("counter:"))),
        fields_removed=man["dropped_fields"], gps_coordinates=man["gps_coordinates"],
        segments=int(pq.ParquetFile(rel / "segments.parquet").metadata.num_rows),
        segments_stage4_all_tbox=int(sum(r["segments"]["count"] for r in s4.values())),
        hydrogen=_hydrogen(rel, s5),
        gps_proxy_check={k: int(sum(r.get("proxy_check", {}).get(k, 0) for r in s5.values()))
                         for k in ("known", "result0", "proxy_on_result0", "proxy_on_result1", "result0_repeating",
                                   "result0_missed_repeating", "result0_missed_new_position")},
        clock_integration=_clock_totals(cfg.layer(LAYER)),
        refuels_intervals=_refuel_intervals(rel),
        trip_gps_to_odometer_well_covered=dict(trips=int(len(wc)), p10=float(wc.quantile(.1)), p50=float(wc.median()), p90=float(wc.quantile(.9)),
                                               candidates=int((td["km_qualified"] >= cfg.release[SECTION]["well_covered_min_km"]).sum()),
                                               km_share=float(td.loc[td["well_covered"], "km_qualified"].sum()
                                                              / td.loc[td["km_qualified"] >= cfg.release[SECTION]["well_covered_min_km"], "km_qualified"].sum()))
        if len(wc) else {},
        max_active_vehicles_per_day=int(fleet["vehicles"].max()) if len(fleet) else 0,
        release_bytes=int(sum(f["bytes"] for f in man["files"])))


README = """# Figure-ready statistics

Every paper figure/table reads only these files (no raw data, no coordinates).

| file | grain | planned use |
|---|---|---|
| accounting.parquet | vehicle x stream x processing step | Fig. 1 processing flow; Table 3 record accounting |
| vehicles.parquet | vehicle | Table 1 fleet overview |
| coverage.parquet | vehicle x day | Fig. 2 coverage heat map |
| fleet_daily.parquet | day | Fig. 2 margin (active vehicles), Fig. 8 usage |
| intervals.parquet | vehicle x month x stream x interval bin | Fig. 4 sampling intervals and gaps |
| field_quality.parquet | vehicle x month x field | Fig. 5 field completeness / flags; Table 2 fields |
| hist1d.parquet, hist2d.parquet | field x bin (fleet) | Fig. 6 physical plausibility (V-I, cell voltages, power tracking) |
| clock.parquet | vehicle x TBOX unit | Fig. 3b two-clock reconciliation (device-time checks, additions) |
| alignment_tolerance.parquet | vehicle x mode x tolerance | Fig. 3d CAN-GPS alignment vs tolerance |
| consistency_daily.parquet, trip_distance.parquet | vehicle x day / trip | Fig. 7a GPS vs odometer distance |
| delayed_check.parquet | vehicle x unit x kind | Fig. 7b delayed delivery: GPS gap and displacement vs odometer jump |
| gps_quality.parquet | vehicle x month | Fig. 5b GPS fix status |
| handover.parquet | TBOX change | Fig. 7c: odometer continuity across TBOX replacements |
| clock_lag.parquet | kind x lag bin (fleet) | Fig. 3a receipt minus stored / verified device time |
| correction.parquet | vehicle | Fig. 6e decoding correction counts and log agreement |
| odometer_daily.parquet | vehicle x unit x receipt day | Fig. 7c positive TotKM min/max and record count |
| segments_summary.parquet | kind x label x duration bin (fleet) | Fig. 4b segment/gap counts and summed duration |
| log_additions_monthly.parquet | vehicle x receipt month x stream | Fig. 3c released log additions |
| clock_lag_signed.parquet | lag bin (fleet) | Fig. 3a signed receipt minus stored lag, -2 to 2 s in 0.05 s bins |
| unit_span.parquet | vehicle x unit | Fig. 2 installation stream first/last receipt times |
| stack_voltage.parquet | vehicle x unit x month | Technical Validation: stack voltage across TBOX handovers (vehicle identity check; numbers.tex) |
| key_numbers.json | - | numbers quoted in the text |

## Figure extensions

`log_additions_monthly` counts released rows with source=log_addition using time_received_ms
for BOTH streams. Counts are after installation exclusions; no month is inferred from missing
receipt times. `clock_lag_signed` retains negative lags at 0.05 s resolution in [-2, 2],
with the same boundary/overflow conventions as clock_lag. `unit_span` contains first_time and
last_time of released operation records per unit, not independently verified installation dates.
The existing delayed_check table only contains per-device quartiles: an event-level ECDF cannot
be recovered. Figure 7 uses unit-level quartile summaries instead, without pooling quantiles.

## Added tables

`clock_lag`: columns `kind`, `bin_left`, `bin_right`, `count`; all bounds are seconds.
`receipt_minus_time` uses records with both receipt and stored timestamps; finite bins span
0–2 s in 0.05 s steps. `receipt_minus_device` requires both receipt and verified device
timestamps; negative edges are -geomspace(0.001, 60, 25), positive edges are
geomspace(0.001, 86400, 65), with zero between them. This preserves negative and zero lags.
Bins are [left, right), except the last finite bin includes its right edge. Separate tails
retain values below/above the finite range; null timestamp pairs are excluded. Counts of
empty bins are zero. The 0–1 s share means 0 <= lag < 1 s divided by all receipt-minus-time
counts, including overflow. The release includes both database and log-addition records.

`correction`: `vehicle_id` plus `stored`, `reconstructed`, `not_reconstructable`, `compared`,
`corrected_equal`, `stored_equal_HyBottleP`, `stored_equal_HighPPMNum`. Each agreement rate
uses `compared` as denominator (undefined when zero); `corrected_equal` requires BOTH corrected
fields to agree. Counts come from stage 4, before installation exclusions; a device moved
between vehicles is assigned to its first installation, following `_device_vehicle`.
A correction not applied contributes zero counts; stored = reconstructed + not_reconstructable
for applied corrections. These are manifest counts, not release-record counts.

`odometer_daily`: `vehicle_id`, `unit`, `day`, `km_min`, `km_max`, `records`, using only
TotKM > 0. Days use the unchanged, naive receipt clock. Min/max are cumulative readings (km),
not daily distance; absent days are omitted, not filled or interpolated.

`segments_summary`: `kind` (segments/gaps), `label` (all for segments; release label for gaps),
`bin_left`, `bin_right`, `count`, `duration_s` (sum within bin). Duration edges are zero followed
by geomspace(1, 86400, 65), with the same boundary and overflow conventions as clock_lag.
Summing bins gives counts and total duration by kind/label, including zero-length segments.

`clock_integration`: one row per vehicle: `pairs` of consecutive released records (same installation and
segment) that both carry a verified device time; fuel-cell on-time (`on_s_*`) and stack energy (`kwh_*`)
integrated over these pairs with the receipt clock and with the device clock (stage-5 rule: 0 < dt <= 60 s,
FcCur above `fc_on_current_a` at the first record); `pairs_diff_over_2s` counts pairs whose two intervals
differ by more than 2 s.

`stack_voltage`: `vehicle_id`, `unit`, `month` (receipt clock), `records`, `v_p25`, `v_median`, `v_p75`
of `FcVol` over released records with `FcCur` and `CoolOutActTemp` inside `[stats] stack_voltage`
(inclusive bounds). Months without such records are omitted; no minimum count is applied here.
"""

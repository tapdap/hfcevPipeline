"""Stage 6 — vehicles: TBOX installations assembled into vehicles.

Methods: "Vehicle assembly".

A vehicle may carry several TBOXes over time (replacements). The private vehicle map (local
`[files] vehicle_map`, see `core.vehicles`) gives each TBOX its vehicle, installation order
(`unit`) and validity interval. Per vehicle and unit:
1. Operation stream in receipt order (stored records + log additions from stage 3) with the
   stage-4 flags and corrected columns and the stage-5 alignment and odometer intervals.
2. GPS in device-time order (stored + additions) with decimal coordinates, stage-4 flags and
   stage-5 fix status and track steps.
3. Records outside the unit's validity interval are excluded (e.g. bench tests of a TBOX before
   its installation); records outside every installation of a TBOX are counted once, on its first
   installation. A TBOX moved between vehicles contributes to each vehicle within its interval.
   Units of a vehicle, and installations of a TBOX, never overlap in time.
4. Segment and trip numbers are made unique per vehicle; derived tables (segments, gaps, trips,
   fuel-cell runs, refuels, daily) are concatenated; monthly statistics are recomputed.
5. Handover check between consecutive units: odometer (last reading before, first after, km per
   day <= `handover_max_km_per_day`) and GPS position on both sides.

Outputs in L6_vehicles/<table>/<vehicle_id>.parquet. Only the anonymous vehicle id and the unit
number identify a vehicle; TBOX numbers and plates stay in the private map.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import asdict
from pathlib import Path

import numpy as np
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

from .. import io
from ..core import motion, vehicles
from ..core.clock import NAT, to_us
from ..io import atomic_path, read_json, sha256_file
from . import s1_harmonize

NAME = "s6"
SECTION = "s6_vehicles"
PARAM_SECTIONS = ("s1_harmonize", SECTION)
CODE = (vehicles, motion, io)
LAYER = "L6_vehicles"
CLOCK_COLUMNS = ["time_received_ms", "time_device", "time_device_status", "same_time_variants"]
FLAG_COLUMNS = ["segment", "missing_mask", "outside_encodable_mask", "all_ones_mask", "relation_violation",
                "counter_backward_mask", "log_mismatch_mask", "HyBottleP_corrected", "HighPPMNum_corrected",
                "correction_status"]
GPS_FLAG_COLUMNS = ["coordinate_class", "hemisphere_ok", "outside_region", "speed_plausible", "device_clock_suspect"]
TABLES = ("operation", "gps", "segments", "gaps", "trips", "fc_runs", "refuels", "daily", "monthly")
# derived per-device tables: (layer, name, time column used for the validity interval)
DERIVED = (("L4_quality", "segments", "start"), ("L4_quality", "gaps", "start"), ("L5_derived", "trips", "start"),
           ("L5_derived", "fc_runs", "start"), ("L5_derived", "refuels", "after"), ("L5_derived", "daily", "day"))


def prepare(cfg, devices, force=False, workers=1, options=None) -> dict:
    vmap_path = cfg.file("vehicle_map")
    units = vehicles.read_map(vmap_path, cfg.cohort)
    s5 = read_json(cfg.layer("L5_derived") / "_manifest.json", {"devices": {}})["devices"]
    inputs = hashlib.sha256(json.dumps({"s5": {d: r.get("sha256") for d, r in sorted(s5.items())},
                                        "map": sha256_file(vmap_path)}, sort_keys=True).encode()).hexdigest()[:16]
    tbox_units = {t: [asdict(u) for u in us] for t, us in vehicles.by_tbox(units).items()}
    return dict(l3=str(cfg.layer("L3_timeline")), l4=str(cfg.layer("L4_quality")), l5=str(cfg.layer("L5_derived")),
                out=str(cfg.layer(LAYER)), fields=s1_harmonize.operation_fields(cfg), inputs=inputs,
                vehicles={v: [asdict(u) for u in us] for v, us in units.items()}, tbox_units=tbox_units,
                devices=sorted(units),
                **cfg.release[SECTION])


def _table(path: Path, columns: list[str] | None = None) -> pd.DataFrame:
    if not path.exists():
        return pd.DataFrame(columns=columns or [])
    names = pq.read_schema(path).names
    cols = None if columns is None else [c for c in columns if c in names]
    return pq.read_table(path, columns=cols).to_pandas().reindex(columns=columns) if columns else pq.read_table(path).to_pandas()


def _us(series: pd.Series) -> np.ndarray:
    if not len(series):
        return np.array([], dtype=np.int64)
    return to_us(pd.to_datetime(series).to_numpy().astype("datetime64[us]"))


def _interleave(origin: np.ndarray, row: np.ndarray, stored: pd.DataFrame, added: pd.DataFrame) -> pd.DataFrame:
    """Rows of `stored`/`added` in the order given by (origin, row) pointers."""
    is_s = origin == "stored"
    parts = pd.concat([stored.iloc[row[is_s]], added.iloc[row[~is_s]]], ignore_index=True)
    pos = np.r_[np.flatnonzero(is_s), np.flatnonzero(~is_s)]
    return parts.iloc[np.argsort(pos, kind="stable")].reset_index(drop=True)


def _write(path: Path, frame: pd.DataFrame) -> None:
    table = pa.Table.from_pandas(frame, preserve_index=False) if len(frame.columns) else pa.table({"empty": pa.array([], pa.int8())})
    with atomic_path(path) as tmp:
        pq.write_table(table, tmp, compression="zstd")


def _unit(u: dict, ctx: dict, gps_base: int) -> dict:
    dev, fields = u["tbox"], ctx["fields"]
    l3, l4, l5 = Path(ctx["l3"]), Path(ctx["l4"]), Path(ctx["l5"])
    inst = vehicles.Installation(**u)

    # ---- operation stream (receipt order as in stage 4)
    flags = _table(l4 / "operation_flags" / f"{dev}.parquet")
    align = flags[["origin", "row"]].merge(_table(l5 / "alignment" / f"{dev}.parquet").drop(columns="time"),
                                           on=["origin", "row"], how="left", validate="one_to_one")
    if len(align) != len(flags) or align["km_kind"].isna().any():
        raise ValueError(f"{dev}: stage-4 flags and stage-5 alignment rows differ")
    cols = CLOCK_COLUMNS + fields
    op = _interleave(flags["origin"].to_numpy(), flags["row"].to_numpy(),
                     _table(l3 / "operation" / f"{dev}.parquet", cols), _table(l3 / "additions" / f"{dev}.parquet", cols))
    op.insert(0, "time", flags["time"].to_numpy())
    op.insert(1, "source", np.where(flags["origin"].to_numpy() == "stored", "database", "log_addition"))
    for c in FLAG_COLUMNS:
        op[c] = flags[c].to_numpy() if c in flags else None
    op["gps_offset_s"] = align["offset_s"].to_numpy()
    op["km_increment"] = align["km_increment"].to_numpy()
    op["km_kind"] = align["km_kind"].to_numpy()
    for c in ("h2_kg_restored", "h2_increment", "h2_kind", "h2_attributed"):
        op[c] = align[c].to_numpy() if c in align else None
    seg_max = int(flags["segment"].max()) if len(flags) else -1

    # ---- GPS (device-time order as in stage 5)
    track = _table(l5 / "gps_track" / f"{dev}.parquet")
    gflags = _table(l4 / "gps_flags" / f"{dev}.parquet")
    gflags = track[["origin", "row"]].merge(gflags, on=["origin", "row"], how="left", validate="one_to_one")
    gcols = ["LAT", "LATD", "LON", "LOND", "time_received_ms", "same_time_variants"]
    raw = _interleave(track["origin"].to_numpy(), track["row"].to_numpy(),
                      _table(l3 / "gps" / f"{dev}.parquet", gcols), _table(l3 / "gps_additions" / f"{dev}.parquet", gcols))
    gps = pd.DataFrame({"time": track["time"].to_numpy(),
                        "source": np.where(track["origin"].to_numpy() == "stored", "database", "log_addition"),
                        "time_received_ms": raw["time_received_ms"].to_numpy(),
                        "lat": motion.degrees(raw["LAT"].to_numpy(), raw["LATD"].tolist(), "S", 90),
                        "lon": motion.degrees(raw["LON"].to_numpy(), raw["LOND"].tolist(), "W", 180),
                        "same_time_variants": raw["same_time_variants"].to_numpy()})
    for c in GPS_FLAG_COLUMNS:
        gps[c] = gflags[c].to_numpy()
    for c in ("fix_status", "fix_source", "usable", "step_km", "speed_kmh"):
        gps[c] = track[c].to_numpy()

    # ---- time order (receipt clock / device clock, stable) and validity interval
    perm = np.argsort(_us(op["time"]), kind="stable")
    op, align = op.iloc[perm].reset_index(drop=True), align.iloc[perm].reset_index(drop=True)
    gperm = np.argsort(_us(gps["time"]), kind="stable")
    gps = gps.iloc[gperm].reset_index(drop=True)
    sorted_pos = np.empty(len(gperm), dtype=np.int64)
    sorted_pos[gperm] = np.arange(len(gperm))  # track row -> position in time order
    t_op, t_gps = _us(op["time"]), _us(gps["time"])
    keep, gkeep = vehicles.inside(t_op, inst), vehicles.inside(t_gps, inst)
    gindex = np.full(len(gps), -1, dtype=np.int64)
    gindex[gkeep] = gps_base + np.arange(int(gkeep.sum()))
    key = pd.Series(np.arange(len(track)), index=pd.MultiIndex.from_arrays([track["origin"], track["row"]]))
    linked = align["gps_row"].notna().to_numpy()
    link = np.full(len(op), -1, dtype=np.int64)
    if linked.any():
        pos = key.reindex(pd.MultiIndex.from_arrays([align["gps_origin"][linked], align["gps_row"][linked].astype(np.int64)])).to_numpy()
        link[linked] = gindex[sorted_pos[pos.astype(np.int64)]]
    op["gps_index"] = pd.array(np.where(link >= 0, link, 0), dtype="Int64")
    op.loc[link < 0, "gps_index"] = pd.NA
    op.loc[link < 0, "gps_offset_s"] = np.nan
    # records of this TBOX outside all of its installations, counted once (on its first installation)
    installs = [vehicles.Installation(**x) for x in ctx["tbox_units"][dev]]
    first = installs[0] == inst
    out_op = ~np.logical_or.reduce([vehicles.inside(t_op, x) for x in installs]) if first else np.zeros(len(op), bool)
    out_gps = ~np.logical_or.reduce([vehicles.inside(t_gps, x) for x in installs]) if first else np.zeros(len(gps), bool)
    before = out_op & (inst.valid_from != NAT) & (t_op < inst.valid_from)
    gbefore = out_gps & (inst.valid_from != NAT) & (t_gps < inst.valid_from)
    acct = dict(unit=inst.unit, source=inst.source, tbox_installations=len(installs), records_in=len(op),
                records=int(keep.sum()), excluded_before_installation=int(before.sum()),
                excluded_after_removal=int((out_op & ~before).sum()),
                gps_in=len(gps), gps=int(gkeep.sum()), gps_excluded_before_installation=int(gbefore.sum()),
                gps_excluded_after_removal=int((out_gps & ~gbefore).sum()),
                first=str(op["time"][keep].min()) if keep.any() else None, last=str(op["time"][keep].max()) if keep.any() else None)

    derived = {}
    for layer, name, tcol in DERIVED:
        frame = _table(Path(ctx[{"L4_quality": "l4", "L5_derived": "l5"}[layer]]) / name / f"{dev}.parquet")
        if "empty" in frame.columns or not len(frame):
            derived[name] = pd.DataFrame()
            continue
        t = _us(frame[tcol])
        if name == "daily":  # a day belongs to the unit if it overlaps the interval
            ok = ((inst.valid_from == NAT) | (t + 86_400_000_000 > inst.valid_from)) & ((inst.valid_to == NAT) | (t < inst.valid_to))
        else:
            ok = vehicles.inside(t, inst)
        derived[name] = frame[ok].reset_index(drop=True)
        if name == "trips":  # trips kept by start time but ending after removal keep stage-5 values
            acct["trips_ending_after_removal"] = int((ok & ~vehicles.inside(_us(frame["end"]), inst)).sum())
    return dict(op=op[keep].reset_index(drop=True), gps=gps[gkeep].reset_index(drop=True), derived=derived,
                seg_max=seg_max, acct=acct)


def _edge(op: pd.DataFrame, gps: pd.DataFrame, last: bool) -> dict:
    pick = (lambda s: s.iloc[-1]) if last else (lambda s: s.iloc[0])
    km = op[np.isfinite(op["TotKM"].astype(float)) & (op["TotKM"].astype(float) > 0)] if "TotKM" in op else op.iloc[:0]
    g = gps[gps["usable"].astype(bool) & gps["lat"].notna()]
    return dict(t=int(_us(pd.Series([pick(km["time"])]))[0]) if len(km) else NAT, km=float(pick(km["TotKM"])) if len(km) else np.nan,
                gps_t=int(_us(pd.Series([pick(g["time"])]))[0]) if len(g) else NAT,
                lat=float(pick(g["lat"])) if len(g) else np.nan, lon=float(pick(g["lon"])) if len(g) else np.nan)


def process(vehicle: str, ctx: dict) -> dict:
    out = Path(ctx["out"])
    ops, gpss, derived, accts, handovers = [], [], {n: [] for _, n, _ in DERIVED}, [], []
    seg_off = trip_off = gps_base = 0
    prev = None
    for u in ctx["vehicles"][vehicle]:
        r = _unit(u, ctx, gps_base)
        op, gps, d = r["op"], r["gps"], r["derived"]
        op["segment"] = op["segment"] + seg_off
        op.insert(1, "unit", u["unit"])
        gps.insert(1, "unit", u["unit"])
        for name, frame in d.items():
            if not len(frame):
                continue
            frame.insert(0, "unit", u["unit"])
            for c in ("segment", "after_segment"):
                if c in frame:
                    frame[c] = frame[c] + seg_off
            if name == "trips":
                frame["trip"] = frame["trip"] + trip_off
            derived[name].append(frame)
        if len(d["trips"]):
            trip_off = int(d["trips"]["trip"].max()) + 1
        seg_off += r["seg_max"] + 1
        gps_base += len(gps)
        if prev is not None and (len(op) or len(gps)):
            handovers.append(dict(from_unit=prev[0], to_unit=u["unit"],
                                  **vehicles.handover(prev[1], _edge(op, gps, last=False), ctx["handover_max_km_per_day"])))
        if len(op) or len(gps):
            prev = (u["unit"], _edge(op, gps, last=True))
        ops.append(op)
        gpss.append(gps)
        accts.append(r["acct"])

    op = pd.concat(ops, ignore_index=True)
    gps = pd.concat(gpss, ignore_index=True)
    tables = {"operation": op, "gps": gps}
    for name, parts in derived.items():
        tables[name] = pd.concat(parts, ignore_index=True) if parts else pd.DataFrame()
    daily = tables["daily"]
    if len(daily):
        daily["day"] = pd.to_datetime(daily["day"])
        num = [c for c in daily.columns if c not in ("unit", "day")]
        units = daily.groupby("day")["unit"].agg(lambda s: ",".join(str(x) for x in sorted(set(s))))
        daily = daily.groupby("day")[num].sum().join(units.rename("units")).reset_index()
        monthly = daily.drop(columns=["units"]).assign(month=daily["day"].dt.to_period("M").astype(str), days=1)
        tables["daily"], tables["monthly"] = daily, monthly.drop(columns="day").groupby("month").sum().reset_index()
    else:
        tables["monthly"] = pd.DataFrame()
    for name in TABLES:
        frame = tables[name]
        if len(frame):
            frame.insert(0, "vehicle_id", vehicle)
        _write(out / name / f"{vehicle}.parquet", frame)
    return dict(units=accts, handovers=handovers, records=len(op), gps_fixes=len(gps),
                trips=len(tables["trips"]), segments=len(tables["segments"]),
                sha256={k: sha256_file(out / k / f"{vehicle}.parquet") for k in TABLES})

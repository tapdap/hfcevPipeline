"""Stage 7 — release: the published files, data dictionary, manifest and invariants.

Methods: "Release files" / Data Records.

Per vehicle (from stage 6):
1. Operation stream: fields whose fleet-wide missing share is >= `drop_missing_share` are
   removed; fields in `corrected_fields` are published with their corrected values under the
   original name (the defective stored values are not published); fields in `withheld_fields` are not
   published (nor the derived columns in `withheld_columns`), and the quality-mask bits of corrected fields
   (missing, outside encodable, all-ones) are recomputed on the corrected values; their
   stored-vs-log mismatch bit is cleared (corrected values equal the log values, stage 4); provenance columns (database
   rows, log files and offsets, TBOX numbers) are removed. Quality masks keep the stage-4 bit
   order (`field_bits.csv`). Fields in `merged_fields` (one signal stored under two names) are
   filled from their source columns where null, with the source's quality bits; the two never
   co-occur (checked), and the source columns are not published.
2. GPS: coordinates according to `gps_coordinates` ("withheld", "full" or a number of decimals);
   fix status, usability, track steps and flags are always published.
3. Derived tables (segments, gaps, trips, fuel-cell runs, refuels, daily, monthly) as fleet-wide
   files with a vehicle column; trip start/end cells follow the coordinate policy.
Finally (once per run): vehicles.csv, dictionary.csv, field_bits.csv, a CSV sample, README.md,
MANIFEST.json (rows and SHA-256 of every file) and invariants.json (row conservation from
stage 3 to the release, no TBOX number or plate in any file, time order, dropped and merged-source
fields absent, merged fields disjoint).
A failed invariant stops the run.
"""
from __future__ import annotations

import glob
import hashlib
import json
import re
from pathlib import Path

import numpy as np
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

from .. import io
from ..core import dictionary, field_quality
from ..io import atomic_path, read_json, sha256_file, write_json
from . import s1_harmonize

NAME = "s7"
SECTION = "s7_release"
PARAM_SECTIONS = ("s1_harmonize", "s4_quality", SECTION)
CODE = (dictionary, field_quality, io)
LAYER = "L7_release"
META = ["vehicle_id", "unit", "time", "time_received_ms", "time_device", "time_device_status", "source",
        "same_time_variants", "segment"]
FLAGS = ["missing_mask", "outside_encodable_mask", "all_ones_mask", "relation_violation", "counter_backward_mask",
         "log_mismatch_mask", "correction_status", "gps_index", "gps_offset_s", "km_increment", "km_kind",
         "h2_kg_restored", "h2_increment", "h2_kind", "h2_attributed"]
DERIVED = ("segments", "gaps", "trips", "fc_runs", "refuels", "daily", "monthly")
MASKS = ("missing", "outside_encodable", "all_ones", "log_mismatch")
IDENTIFIERS = re.compile(r"QLKJ\d|晋[A-Z]")


def _field_shares(l4: Path, fields: list[str]) -> pd.DataFrame:
    parts = [pd.read_parquet(f) for f in sorted(glob.glob(str(l4 / "field_stats" / "*.parquet")))]
    s = pd.concat(parts).groupby("field")[["records", "missing", "zero"]].sum().reindex(fields)
    s["missing_share"] = s["missing"] / s["records"]
    s["zero_share"] = s["zero"] / (s["records"] - s["missing"]).where(lambda x: x > 0)
    return s


def prepare(cfg, devices, force=False, workers=1, options=None) -> dict:
    p = {"merged_fields": {}, "withheld_fields": [], "withheld_columns": {}, **cfg.release[SECTION]}
    fields = s1_harmonize.operation_fields(cfg)
    shares = _field_shares(cfg.layer("L4_quality"), fields)
    merged = {t: [f for f in src if f in fields] for t, src in p["merged_fields"].items() if t in fields}
    merged = {t: src for t, src in merged.items() if src}
    p["merged_fields"] = merged
    sources = [f for src in merged.values() for f in src]
    for target, src in merged.items():  # the merged column is present where either name is (disjoint)
        for f in src:
            shares.loc[target, "missing"] -= shares.loc[f, "records"] - shares.loc[f, "missing"]
            shares.loc[target, "zero"] += shares.loc[f, "zero"]
    shares["missing_share"] = shares["missing"] / shares["records"]
    shares["zero_share"] = shares["zero"] / (shares["records"] - shares["missing"]).where(lambda x: x > 0)
    drop = [f for f in fields if f not in sources and shares.loc[f, "missing_share"] >= p["drop_missing_share"]]
    s6 = read_json(cfg.layer("L6_vehicles") / "_manifest.json", {"devices": {}})["devices"]
    groups = {}
    try:
        vmap = pd.read_csv(cfg.file("vehicle_map"), dtype=str)
        if "operator_group" in vmap:
            groups = vmap.groupby("vehicle_id")["operator_group"].first().dropna().to_dict()
    except KeyError:
        pass
    inputs = hashlib.sha256(json.dumps({d: r.get("sha256") for d, r in sorted(s6.items())}, sort_keys=True).encode()
                            ).hexdigest()[:16]
    encs = field_quality.encodings(cfg.file("dbc"), p["corrected_fields"])
    corrected_encodings = {f: [(e.length, e.signed, e.factor, e.offset) for e in encs[f]] for f in p["corrected_fields"]}
    return dict(l6=str(cfg.layer("L6_vehicles")), out=str(cfg.layer(LAYER)), fields=fields, drop=drop,
                corrected_encodings=corrected_encodings,
                kept=[f for f in fields if f not in drop and f not in sources and f not in p["withheld_fields"]],
                merged_sources=sources, shares=shares.reset_index().to_dict("records"),
                groups=groups, s6={v: dict(units=r["units"], handovers=r["handovers"]) for v, r in s6.items()},
                devices=sorted(s6), inputs=inputs, version=cfg.release["dataset"]["version"], **p)


def _coords(frame: pd.DataFrame, policy: str, cols: list[str]) -> pd.DataFrame:
    if policy == "full":
        return frame
    if policy == "withheld":
        return frame.drop(columns=[c for c in cols if c in frame])
    return frame.assign(**{c: frame[c].round(int(policy)) for c in cols if c in frame and frame[c].dtype.kind == "f"})


def merge_fields(op: pd.DataFrame, merged: dict, fields: list[str]) -> int:
    """Fill each target column from its source columns where the target is null, moving the source's
    quality bits to the target's bit (in place). Returns the number of rows where both were present."""
    overlap = 0
    for target, sources in merged.items():
        for src in (s for s in sources if s in op and target in op):
            values = pd.to_numeric(op[src], errors="coerce")
            overlap += int((op[target].notna() & values.notna()).sum())
            fill = (op[target].isna() & values.notna()).to_numpy()
            tbit, sbit = np.int64(1) << fields.index(target), np.int64(1) << fields.index(src)
            for k in MASKS:
                if f"{k}_mask" in op:
                    m = op[f"{k}_mask"].to_numpy(dtype=np.int64)
                    op[f"{k}_mask"] = np.where(fill, (m & ~tbit) | np.where((m & sbit) != 0, tbit, 0), m)
            op[target] = op[target].astype(float).where(~fill, values)
    return overlap


def _write(path: Path, frame: pd.DataFrame) -> None:
    table = pa.Table.from_pandas(frame, preserve_index=False) if len(frame.columns) else pa.table({"empty": pa.array([], pa.int8())})
    with atomic_path(path) as tmp:
        pq.write_table(table, tmp, compression="zstd")


def _leaks(frame: pd.DataFrame) -> int:
    n = 0
    for c in frame.columns:
        if frame[c].dtype == object:
            n += sum(bool(IDENTIFIERS.search(str(v))) for v in frame[c].dropna().unique())
    return n


def _monotonic(frame: pd.DataFrame) -> bool:
    return bool(frame.groupby("unit")["time"].apply(lambda s: s.is_monotonic_increasing).all()) if len(frame) else True


def process(vehicle: str, ctx: dict) -> dict:
    l6, out = Path(ctx["l6"]), Path(ctx["out"])
    op = pd.read_parquet(l6 / "operation" / f"{vehicle}.parquet")
    for f in ctx["corrected_fields"]:
        if f in op and f"{f}_corrected" in op:
            op[f] = op[f"{f}_corrected"]
            # quality bits of a corrected field describe the published (corrected) values
            bit = np.int64(1) << ctx["fields"].index(f)
            encs = [field_quality.Encoding(*e) for e in ctx["corrected_encodings"][f]]
            fl = field_quality.flags(op[f].to_numpy(dtype=float), encs)
            for k in ("missing", "outside_encodable", "all_ones"):
                col = f"{k}_mask"
                if col in op:
                    op[col] = (op[col].to_numpy(dtype=np.int64) & ~bit) | np.where(fl[k], bit, 0)
            if "log_mismatch_mask" in op:
                op["log_mismatch_mask"] = op["log_mismatch_mask"].to_numpy(dtype=np.int64) & ~bit
    overlap = merge_fields(op, ctx["merged_fields"], ctx["fields"])
    op = op[[c for c in META + ctx["kept"] + FLAGS if c in op]]
    gps = _coords(pd.read_parquet(l6 / "gps" / f"{vehicle}.parquet"), ctx["gps_coordinates"], ["lat", "lon"])
    _write(out / "operation" / f"{vehicle}.parquet", op)
    _write(out / "gps" / f"{vehicle}.parquet", gps)
    tables = {}
    for name in DERIVED:
        frame = pd.read_parquet(l6 / name / f"{vehicle}.parquet")
        if "empty" in frame:
            frame = pd.DataFrame()
        frame = frame.drop(columns=[c for c in ctx["withheld_columns"].get(name, []) if c in frame])
        if name == "trips":
            frame = frame.drop(columns=[c for c in ("start_cell", "end_cell") if c in frame]) \
                if ctx["gps_coordinates"] == "withheld" else frame
        tables[name] = frame
        _write(out / "_parts" / name / f"{vehicle}.parquet", frame)

    n_gps = max(len(gps), 1)
    status = gps["fix_status"].value_counts() if len(gps) else pd.Series(dtype=int)
    steps = gps["speed_kmh"].notna().sum() if len(gps) else 0
    m, t = tables["monthly"], tables["trips"]
    summary = dict(
        vehicle_id=vehicle, operator_group=ctx["groups"].get(vehicle), units=len(ctx["s6"][vehicle]["units"]),
        first_time=str(op["time"].min()) if len(op) else None, last_time=str(op["time"].max()) if len(op) else None,
        active_days=int(len(tables["daily"])), records=len(op), records_log_additions=int((op["source"] == "log_addition").sum()),
        records_device_time=int(op["time_device"].notna().sum()), gps_fixes=len(gps),
        odometer_km=float(m["km_qualified"].sum()) if len(m) else 0.0,
        odometer_km_delayed=float(m["km_delayed"].sum()) if len(m) else 0.0,
        h2_kg=float(m["h2_kg"].sum()) if len(m) and "h2_kg" in m else 0.0,
        gps_track_km=float(m["gps_km"].sum()) if len(m) else 0.0,
        fc_on_hours=float(m["fc_on_s"].sum() / 3600) if len(m) else 0.0,
        trips=len(t), trips_moving=int(t["moving"].sum()) if len(t) else 0, refuels=len(tables["refuels"]),
        records_with_odometer=int((op["TotKM"] > 0).sum()) if "TotKM" in op else 0,
        gps_usable_share=round(float(gps["usable"].sum()) / n_gps, 4) if len(gps) else None,
        gps_no_fix_share=round(float(status.get("no_fix", 0) + status.get("no_fix_inferred", 0)) / n_gps, 4) if len(gps) else None,
        gps_stuck_share=round(float(status.get("static_while_moving", 0)) / n_gps, 4) if len(gps) else None,
        gps_implausible_step_share=round(float((gps["speed_kmh"] > 150).sum()) / max(steps, 1), 4) if len(gps) else None,
        aligned_share=round(float(op["gps_index"].notna().mean()), 4) if len(op) else None)
    return dict(summary=summary, records=len(op), gps_fixes=len(gps),
                checks=dict(identifier_values=_leaks(op) + _leaks(gps) + sum(_leaks(f) for f in tables.values()),
                            operation_time_ordered=_monotonic(op), gps_time_ordered=_monotonic(gps),
                            dropped_fields_present=[f for f in ctx["drop"] + ctx["merged_sources"] + ctx["withheld_fields"] if f in op],
                            merged_overlap=overlap),
                sha256={k: sha256_file(out / k / f"{vehicle}.parquet") for k in ("operation", "gps")})


# ---------------------------------------------------------------- finalize (once per run)

def _dictionary(ctx: dict, out: Path) -> pd.DataFrame:
    rows = []
    shares = {r["field"]: r for r in ctx["shares"]}

    def schema(path):
        files = sorted(path.glob("*.parquet")) if path.is_dir() else [path]
        for f in files:
            names = pq.read_schema(f)
            if "empty" not in names.names:
                return names
        return None

    op_schema = schema(out / "operation")
    for name in op_schema.names:
        desc, unit, basis = dictionary.FIELDS.get(name, (dictionary.OPERATION.get(name, ""), "", ""))
        note = ""
        if name in shares:
            s = shares[name]
            if s["missing_share"] >= ctx["sparse_missing_share"]:
                note = f"sparse: {100 * s['missing_share']:.1f}% missing"
            elif s["zero_share"] is not None and s["zero_share"] == s["zero_share"] and s["zero_share"] >= ctx["constant_zero_share"]:
                note = f"near-constant: {100 * s['zero_share']:.1f}% of present values are 0"
        rows.append(dict(table="operation", column=name, type=str(op_schema.field(name).type), unit=unit,
                         unit_basis=basis, description=desc, note=note, caveat=dictionary.CAVEATS.get(name, "")))
    gps_schema = schema(out / "gps")
    for name in gps_schema.names:
        rows.append(dict(table="gps", column=name, type=str(gps_schema.field(name).type), unit="",
                         unit_basis="", description=dictionary.GPS.get(name, ""), note=""))
    for name in DERIVED:
        sch = schema(out / f"{name}.parquet")
        for col in (sch.names if sch else []):
            rows.append(dict(table=name, column=col, type=str(sch.field(col).type), unit="", unit_basis="",
                             description=dictionary.DERIVED.get(col, ""), note=""))
    return pd.DataFrame(rows).fillna({"caveat": ""})


def finalize(cfg, ctx: dict, records: dict, log=print) -> dict:
    out = Path(ctx["out"])
    vehicles = sorted(records)
    for name in DERIVED:
        parts = [pd.read_parquet(out / "_parts" / name / f"{v}.parquet") for v in vehicles]
        parts = [p for p in parts if len(p) and "empty" not in p]
        _write(out / f"{name}.parquet", pd.concat(parts, ignore_index=True) if parts else pd.DataFrame())
    vt = pd.DataFrame([records[v]["summary"] for v in vehicles])
    vt.to_csv(out / "vehicles.csv", index=False)
    pd.DataFrame([dict(bit=j, field=f, released=f in ctx["kept"]) for j, f in enumerate(ctx["fields"])]
                 + [dict(bit=k, field=f"counter:{c}", released=True) for k, c in enumerate(cfg.release["s4_quality"]["counters"])]
                 ).to_csv(out / "field_bits.csv", index=False)
    dic = _dictionary(ctx, out)
    dic.to_csv(out / "dictionary.csv", index=False)

    # sample: the busiest day of the first vehicle
    v0 = vehicles[0]
    op = pd.read_parquet(out / "operation" / f"{v0}.parquet")
    day = op["time"].dt.date.value_counts().idxmax() if len(op) else None
    (out / "sample").mkdir(exist_ok=True)
    if day is not None:
        op[op["time"].dt.date == day].to_csv(out / "sample" / f"operation_{v0}_{day}.csv", index=False)
        g = pd.read_parquet(out / "gps" / f"{v0}.parquet")
        g[g["time"].dt.date == day].to_csv(out / "sample" / f"gps_{v0}_{day}.csv", index=False)

    # invariants
    s4 = read_json(cfg.layer("L4_quality") / "_manifest.json")["devices"]
    s5 = read_json(cfg.layer("L5_derived") / "_manifest.json")["devices"]
    s6 = read_json(cfg.layer("L6_vehicles") / "_manifest.json")["devices"]
    units = [u for v in s6.values() for u in v["units"]]
    l3_records = sum(r["records"]["total"] for r in s4.values())
    l3_gps = sum(r["gps_fixes"] for r in s5.values())
    excluded = sum(u["excluded_before_installation"] + u["excluded_after_removal"] for u in units)
    gps_excluded = sum(u["gps_excluded_before_installation"] + u["gps_excluded_after_removal"] for u in units)
    released, released_gps = sum(r["records"] for r in records.values()), sum(r["gps_fixes"] for r in records.values())
    names = [str(p.relative_to(out)) for p in out.rglob("*") if p.is_file()]
    checks = dict(
        operation_conservation=dict(stage3=l3_records, released=released, excluded_outside_installation=excluded,
                                    ok=l3_records == released + excluded),
        gps_conservation=dict(stage3=l3_gps, released=released_gps, excluded_outside_installation=gps_excluded,
                              ok=l3_gps == released_gps + gps_excluded),
        vehicles_match_stage6=sorted(s6) == vehicles,
        identifier_values=sum(r["checks"]["identifier_values"] for r in records.values()),
        identifier_file_names=sum(bool(IDENTIFIERS.search(n)) for n in names),
        time_ordered=all(r["checks"]["operation_time_ordered"] and r["checks"]["gps_time_ordered"] for r in records.values()),
        dropped_fields_absent=not any(r["checks"]["dropped_fields_present"] for r in records.values()),
        merged_fields_disjoint=sum(r["checks"].get("merged_overlap", 0) for r in records.values()) == 0,
        undescribed_columns=sorted(f"{t}.{c}" for t, c in dic.loc[dic["description"] == "", ["table", "column"]].itertuples(index=False)))
    ok = (checks["operation_conservation"]["ok"] and checks["gps_conservation"]["ok"] and checks["vehicles_match_stage6"]
          and checks["identifier_values"] == 0 and checks["identifier_file_names"] == 0 and checks["time_ordered"]
          and checks["dropped_fields_absent"] and checks["merged_fields_disjoint"])
    checks["ok"] = bool(ok)
    write_json(out / "invariants.json", checks)

    # README first: the manifest below lists it with its checksum
    (out / "README.md").write_text(_readme(ctx, vt, checks, dic), encoding="utf-8")
    files = sorted(p for p in out.rglob("*") if p.is_file() and not str(p.relative_to(out)).startswith(("_parts", "_manifest"))
                   and p.name != "MANIFEST.json")
    listing = []
    for p in files:
        item = dict(path=str(p.relative_to(out)), bytes=p.stat().st_size, sha256=sha256_file(p))
        if p.suffix == ".parquet":
            item["rows"] = pq.ParquetFile(p).metadata.num_rows
        listing.append(item)
    write_json(out / "MANIFEST.json", dict(dataset=cfg.release["dataset"]["name"], version=ctx["version"],
                                           vehicles=len(vehicles), dropped_fields=ctx["drop"],
                                           merged_fields=ctx["merged_fields"], withheld_fields=ctx["withheld_fields"],
                                           gps_coordinates=ctx["gps_coordinates"], files=listing))
    if not ok:
        raise RuntimeError(f"release invariants failed: {json.dumps(checks)}")
    log(f"release v{ctx['version']}: {len(vehicles)} vehicles, {released:,} records, {released_gps:,} GPS fixes; invariants ok")
    return checks


def _readme(ctx: dict, vt: pd.DataFrame, checks: dict, dic: pd.DataFrame) -> str:
    coords = {"withheld": "withheld (not part of this build)", "full": "full precision"}.get(
        ctx["gps_coordinates"], f"rounded to {ctx['gps_coordinates']} decimals")
    return "\n".join([
        f"# HFCEV fleet telemetry — release v{ctx['version']} (build)", "",
        f"- vehicles: {len(vt)} (TBOX installations: {int(vt['units'].sum())})",
        f"- operation records: {int(vt['records'].sum()):,}; GPS fixes: {int(vt['gps_fixes'].sum()):,}",
        f"- period: {vt['first_time'].min()} to {vt['last_time'].max()} (server receipt time)",
        f"- GPS coordinates: {coords}",
        f"- fields removed (>= {100 * ctx['drop_missing_share']:.0f}% missing fleet-wide): {', '.join(ctx['drop'])}",
        *([f"- fields not published (undocumented vendor codes): {', '.join(ctx['withheld_fields'])}"] if ctx["withheld_fields"] else []),
        *([f"- fields merged (one signal under two names): "
           + "; ".join(f"{t} <- {', '.join(s)}" for t, s in ctx["merged_fields"].items())] if ctx["merged_fields"] else []),
        f"- invariants: {'all passed' if checks['ok'] else 'FAILED'} (invariants.json)", "",
        "## Files", "",
        "| path | content |", "|---|---|",
        "| `vehicles.csv` | one row per vehicle: period, counts, distances, GPS quality shares |",
        "| `operation/<vehicle>.parquet` | operation stream in receipt-time order with quality flags |",
        "| `gps/<vehicle>.parquet` | GPS fixes in device-time order with fix status and track steps |",
        "| `segments.parquet`, `gaps.parquet` | reporting segments and the no-data gaps between them |",
        "| `trips.parquet`, `fc_runs.parquet`, `refuels.parquet` | trips, fuel-cell runs, refuelling events |",
        "| `daily.parquet`, `monthly.parquet` | per-vehicle daily and monthly statistics |",
        "| `dictionary.csv`, `field_bits.csv` | column descriptions and units; bit order of the quality masks |",
        "| `sample/` | one vehicle-day as CSV |",
        "| `MANIFEST.json` | rows and SHA-256 of every file |", "",
        "## Field notes", "",
        "Values that are easily misread (also in the `caveat` column of `dictionary.csv`):", "",
        *[f"- `{r.column}`: {r.caveat}" for r in dic[(dic["table"] == "operation") & (dic["caveat"] != "")].itertuples()],
        "- `time` (operation stream): after a transmission outage, delayed records arrive in a burst, so differences of "
        "`time` do not measure acquisition intervals there; use `time_device` where verified, or records with "
        "`same_time_variants` = 1 inside segments, for transient analyses.", ""])

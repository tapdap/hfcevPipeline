"""Stage 1 — harmonise raw sources into one operation stream and one GPS stream per device.

Methods: "Source harmonisation".

Operation stream (`can_run`)
    Per-device CAN tables and the legacy run table are put on the CAN schema,
    restricted to the study window, and reconciled with `core.dedup.operation_dispositions`:
    exact repeats are removed and legacy records overlapping CAN are retired in favour
    of CAN. The approved `drop_fields` are then removed.
GPS stream (`gps`)
    Per-device GPS tables are restricted to the window, exact repeats removed, and
    coordinates stored at 1/100 scale on audited dates are restored (`core.gps_scale`).

Kept records retain their original values and order keys (source_file, source_row) and
gain `same_time_variants`. Every retired record is archived with its reason.

Outputs in L1_harmonized/:
    can_run/<device>.parquet, gps/<device>.parquet
    retired/{can_run,gps}/<device>.parquet, gps_scale_changes/<device>.parquet
"""
from __future__ import annotations

import re
import shutil
from collections import Counter
from pathlib import Path

import numpy as np
import pyarrow as pa
import pyarrow.compute as pc
import pyarrow.parquet as pq

from .. import io, sources
from ..core import dedup, gps_scale, raw
from ..core.raw import content_fields, daily_counts, month_keys, to_unified, unified_schema, window_filter
from ..io import ChunkWriter, atomic_path, read_json, sha256_file, write_json
from ..sources import L0Inventory

NAME = "s1"
SECTION = "s1_harmonize"
PARAM_SECTIONS = (SECTION,)
CODE = (raw, dedup, gps_scale, io, sources)
LAYER = "L1_harmonized"
LEGACY_STAGING = "_staging/legacy_run"
BATCH = 131072


# ---------------------------------------------------------------- prepare (once per run)

def operation_fields(cfg) -> list[str]:
    """Measured fields of the released operation stream (CAN schema minus provenance and drop_fields)."""
    params = cfg.release[SECTION]
    schema = unified_schema(L0Inventory(cfg.layer("L0_raw")).first_schema("can"))
    return [f for f in content_fields(schema, params["provenance_fields"]) if f not in params["drop_fields"]]


def prepare(cfg, devices, force=False, workers=1, options=None) -> dict:
    params, window = cfg.release[SECTION], cfg.release["dataset"]
    bad = [d for d in devices if not re.fullmatch(params["device_pattern"], d)]
    if bad:
        raise ValueError(f"Cohort IDs do not match device_pattern: {bad[:5]}")
    l0 = L0Inventory(cfg.layer("L0_raw"))
    provenance = params["provenance_fields"]
    can_schema = unified_schema(l0.first_schema("can"))
    gps_schema = unified_schema(l0.first_schema("gps"))
    legacy = l0.record(params["legacy_run_file"])
    business = content_fields(can_schema, provenance)
    shared = content_fields(pq.read_schema(l0.root / legacy["path"]), provenance)
    if not set(shared) <= set(business):
        raise ValueError("Legacy run fields must be a subset of the CAN fields")
    if not set(params["drop_fields"]) <= set(business):
        raise ValueError("drop_fields must be existing operation fields")

    out = cfg.layer(LAYER)
    legacy_dir = _stage_legacy(l0, legacy, can_schema, devices, out / LEGACY_STAGING, force)
    return dict(l0_root=str(l0.root), out=str(out), legacy_dir=str(legacy_dir),
                can_schema=can_schema, gps_schema=gps_schema, business=business, shared=shared,
                gps_business=content_fields(gps_schema, provenance),
                start=window["window_start"], end=window["window_end"],
                drop_fields=list(params["drop_fields"]), restore_dates=list(params["gps_scale_restore_dates"]))


def _stage_legacy(l0, record, schema, devices, directory: Path, force) -> Path:
    """Split the all-device legacy run table into one unified-schema file per cohort device.

    Reused when the source fingerprint, schema and device set are unchanged.
    """
    key = {"source": record["path"], "sha256": l0.fingerprints[record["path"]]["sha256"], "schema": str(schema)}
    receipt = read_json(directory / "_receipt.json")
    if not force and receipt and receipt["key"] == key and set(devices) <= set(receipt["devices"]):
        return directory

    wanted = pa.array(sorted(devices))
    tmp = directory.with_name(directory.name + ".tmp")
    shutil.rmtree(tmp, ignore_errors=True)
    tmp.mkdir(parents=True)
    writers, counts, offset = {}, Counter(), 0
    pf = l0.verified_file(record)
    try:
        for batch in pf.iter_batches(batch_size=BATCH, use_threads=False):
            raw = pa.Table.from_batches([batch])
            unified = to_unified(raw, schema, "run", record["path"], offset)
            offset += len(raw)
            unified = unified.filter(pc.is_in(unified["tbox"], value_set=wanted))
            for device in pc.unique(unified["tbox"]).to_pylist():
                part = unified.filter(pc.equal(unified["tbox"], device))
                writers.setdefault(device, ChunkWriter(tmp / f"{device}.parquet")).write(part)
                counts[device] += len(part)
    finally:
        for w in writers.values():
            w.close()
    if offset != record["rows"]:
        raise ValueError("Legacy run table was not read completely")
    write_json(tmp / "_receipt.json", {"key": key, "raw_rows": offset,
                                       "devices": {d: counts.get(d, 0) for d in sorted(devices)}})
    shutil.rmtree(directory, ignore_errors=True)
    tmp.rename(directory)
    return directory


# ---------------------------------------------------------------- process (per device)

def process(device: str, ctx: dict) -> dict:
    l0, out = L0Inventory(ctx["l0_root"]), Path(ctx["out"])
    return {"can_run": _operation(device, l0, out, ctx), "gps": _gps(device, l0, out, ctx)}


def _load(l0, stream: str, device: str, schema) -> tuple[list[pa.Table], list[dict]]:
    """Unified tables for every per-device source file, with fingerprints verified."""
    tables, sources = [], []
    for record in l0.device_records(stream, device):
        raw = l0.verified_file(record).read(use_threads=False)
        table = to_unified(raw, schema, stream, record["path"], 0)
        if not pc.all(pc.equal(table["tbox"], device)).as_py() and len(table):
            raise ValueError(f"{record['path']} contains records of another device")
        tables.append(table)
        sources.append({"file": record["path"], "rows": len(table)})
    return tables, sources


def _by_month(table: pa.Table):
    months = month_keys(table)
    for month in sorted(set(months.to_pylist())):
        yield month, table.filter(pc.equal(months, month))


def _with_variants(main: pa.Table) -> pa.Table:
    variants = dedup.same_time_variants(main["time"].to_pandas())
    return main.append_column("same_time_variants", pa.array(variants))


def _write_archive(path: Path, tables: list[pa.Table]) -> int:
    rows = sum(len(t) for t in tables)
    if rows:
        with atomic_path(path) as tmp:
            pq.write_table(pa.concat_tables(tables), tmp, compression="zstd")
    else:
        path.unlink(missing_ok=True)
    return rows


def _check_output(path: Path, device: str, rows: int) -> None:
    table = pq.read_table(path, columns=["time", "tbox"])
    times = table["time"].cast(pa.int64()).to_numpy()
    if len(table) != rows or table["time"].null_count or not np.all(times[1:] >= times[:-1]):
        raise AssertionError(f"{path}: row count or time order check failed")
    if rows and not pc.all(pc.equal(table["tbox"], device)).as_py():
        raise AssertionError(f"{path}: foreign device rows")


def _operation(device, l0, out: Path, ctx) -> dict:
    schema = ctx["can_schema"]
    tables, sources = _load(l0, "can", device, schema)
    staged = Path(ctx["legacy_dir"]) / f"{device}.parquet"
    if staged.exists():
        tables.append(pq.read_table(staged).cast(schema))
        sources.append({"file": "legacy run (staged)", "rows": len(tables[-1])})
    table = pa.concat_tables(tables) if tables else schema.empty_table()
    input_rows = len(table)
    table, dropped = window_filter(table, ctx["start"], ctx["end"])

    columns = ["time"] + ctx["business"] + ["source_stream", "source_file", "source_row"]
    final = out / "can_run" / f"{device}.parquet"
    full = final.with_name(f".{device}.before_projection.parquet")
    final.parent.mkdir(parents=True, exist_ok=True)
    reasons, retired, streams, variant_rows, kept = Counter(), [], Counter(), 0, 0
    writer = ChunkWriter(full)
    try:
        for _, month in _by_month(table):
            keep, reason = dedup.operation_dispositions(month.select(columns).to_pandas(), ctx["business"], ctx["shared"])
            gone = np.flatnonzero(reason != "")
            if len(gone):
                retired.append(month.take(pa.array(gone)).append_column("retired_reason", pa.array(reason[gone].tolist())))
                reasons.update(reason[gone].tolist())
            main = _with_variants(month.take(pa.array(keep)))
            writer.write(main)
            for r in pc.value_counts(main["source_stream"]).to_pylist():
                streams[r["values"]] += r["counts"]
            variant_rows += pc.sum(pc.greater(main["same_time_variants"], 1)).as_py() or 0
            kept += len(main)
        if not writer.close():
            pq.write_table(_with_variants(schema.empty_table()), full, compression="zstd")
        before_projection = sha256_file(full)
        # Field removal is a separate approved decision applied to the complete stream.
        merged = pq.ParquetFile(full).read(use_threads=False)
        with atomic_path(final) as tmp:
            pq.write_table(merged.drop_columns(ctx["drop_fields"]), tmp, compression="zstd")
    finally:
        full.unlink(missing_ok=True)

    retired_rows = _write_archive(out / "retired" / "can_run" / f"{device}.parquet", retired)
    if input_rows != kept + retired_rows + sum(dropped.values()):
        raise AssertionError(f"{device}: operation rows not conserved")
    _check_output(final, device, kept)
    return dict(rows=kept, can_rows=streams.get("can", 0), run_rows=streams.get("run", 0),
                input_rows=input_rows, **dropped, retired_reasons=dict(sorted(reasons.items())),
                duplicate_rows=retired_rows - reasons[dedup.RUN_CONFLICT],
                old_run_conflict_rows=reasons[dedup.RUN_CONFLICT],
                retained_same_time_variant_rows=variant_rows, sources=sources,
                sha256=sha256_file(final), sha256_before_projection=before_projection,
                daily=daily_counts(pq.read_table(final, columns=["time"])))


def _gps(device, l0, out: Path, ctx) -> dict:
    schema = ctx["gps_schema"]
    tables, sources = _load(l0, "gps", device, schema)
    table = pa.concat_tables(tables) if tables else schema.empty_table()
    input_rows = len(table)
    table, dropped = window_filter(table, ctx["start"], ctx["end"])

    columns = ["time"] + ctx["gps_business"] + ["source_file", "source_row"]
    final = out / "gps" / f"{device}.parquet"
    retired, changes, changed_days, variant_rows, kept = [], [], Counter(), 0, 0
    with atomic_path(final) as tmp:
        writer = ChunkWriter(tmp)
        for _, month in _by_month(table):
            keep, gone = dedup.gps_dispositions(month.select(columns).to_pandas(), ctx["gps_business"])
            if len(gone):
                retired.append(month.take(pa.array(gone)).append_column(
                    "retired_reason", pa.array(["gps_exact_duplicate"] * len(gone))))
            main = _with_variants(month.take(pa.array(keep)))
            restored, positions, days = gps_scale.restore_scale(main, ctx["restore_dates"])
            if len(positions):
                original = main.take(pa.array(positions))
                changes.append(original.append_column("restored_LAT", restored["LAT"].take(pa.array(positions)))
                                       .append_column("restored_LON", restored["LON"].take(pa.array(positions))))
                changed_days.update(days)
            writer.write(restored)
            variant_rows += pc.sum(pc.greater(main["same_time_variants"], 1)).as_py() or 0
            kept += len(main)
        if not writer.close():
            pq.write_table(_with_variants(schema.empty_table()), tmp, compression="zstd")

    retired_rows = _write_archive(out / "retired" / "gps" / f"{device}.parquet", retired)
    restored_rows = _write_archive(out / "gps_scale_changes" / f"{device}.parquet", changes)
    if input_rows != kept + retired_rows + sum(dropped.values()):
        raise AssertionError(f"{device}: GPS rows not conserved")
    _check_output(final, device, kept)
    return dict(rows=kept, input_rows=input_rows, **dropped, duplicate_rows=retired_rows,
                retained_same_time_variant_rows=variant_rows, scale_restored_rows=restored_rows,
                scale_restored_days=dict(sorted(changed_days.items())), sources=sources,
                sha256=sha256_file(final), daily=daily_counts(pq.read_table(final, columns=["time"])))

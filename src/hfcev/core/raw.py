"""Bring raw source tables onto one schema with row-level provenance. Values are never altered."""
from __future__ import annotations

from datetime import datetime

import numpy as np
import pyarrow as pa
import pyarrow.compute as pc

MISSING_DEVICE = "__MISSING_TBOX__"
# Appended to every source schema. `tbox` holds the device ID; the name is kept from the raw export.
PROVENANCE_COLUMNS = [
    pa.field("tbox", pa.string()),
    pa.field("source_stream", pa.string()),
    pa.field("source_file", pa.string()),
    pa.field("source_row", pa.int64()),
]


def unified_schema(reference: pa.Schema) -> pa.Schema:
    return pa.schema(list(reference) + PROVENANCE_COLUMNS)


def content_fields(schema: pa.Schema, provenance: list[str]) -> list[str]:
    """Measured (business) fields: everything that is not extraction provenance."""
    extra = {f.name for f in PROVENANCE_COLUMNS}
    return [n for n in schema.names if n not in provenance and n not in extra]


def device_ids(sn: pa.ChunkedArray | pa.Array) -> pa.Array:
    """Trimmed, upper-cased `sn`; leading zeros are preserved. Missing values get a sentinel."""
    ids = pc.utf8_upper(pc.utf8_trim_whitespace(sn))
    missing = pc.or_(pc.is_null(ids), pc.fill_null(pc.equal(ids, ""), False))
    return pc.if_else(missing, MISSING_DEVICE, ids)


def retype_all_null(table: pa.Table, schema: pa.Schema, relative: str) -> pa.Table:
    """Align column types with `schema`.

    The only tolerated difference is a string column that the reference stores as a
    number and that is entirely null in this table (an empty column typed by the dump).
    Anything else needs an explicit parsing decision and raises.
    """
    if table.schema.field("time").type != pa.timestamp("us"):
        raise TypeError(f"{relative}: time must be timestamp[us]")
    for field in table.schema:
        if field.name not in schema.names:
            raise TypeError(f"{relative}: unexpected field {field.name}")
        target = schema.field(field.name).type
        if field.type == target:
            continue
        numeric = pa.types.is_integer(target) or pa.types.is_floating(target)
        if not (pa.types.is_string(field.type) and numeric):
            raise TypeError(f"{relative}: unexpected type for {field.name}")
        if table[field.name].null_count != len(table):
            raise ValueError(f"{relative}: non-null strings in numeric field {field.name}")
        index = table.schema.get_field_index(field.name)
        table = table.set_column(index, field.name, pa.nulls(len(table), target))
    return table


def to_unified(table: pa.Table, schema: pa.Schema, stream: str, relative: str, first_row: int) -> pa.Table:
    """Unified-schema copy of a raw table; fields absent from the source become null.

    `source_row` is the zero-based row position in the raw file (`first_row` offsets a batch).
    """
    table = retype_all_null(table, schema, relative)
    n = len(table)
    provided = {
        "tbox": device_ids(table["sn"]),
        "source_stream": pa.array([stream] * n, pa.string()),
        "source_file": pa.array([relative] * n, pa.string()),
        "source_row": pa.array(np.arange(first_row, first_row + n, dtype=np.int64)),
    }
    arrays = []
    for field in schema:
        if field.name in provided:
            arrays.append(provided[field.name])
        elif field.name in table.column_names:
            arrays.append(table[field.name])
        else:
            arrays.append(pa.nulls(n, field.type))
    return pa.Table.from_arrays(arrays, schema=schema)


def window_filter(table: pa.Table, start: str, end: str) -> tuple[pa.Table, dict]:
    """Keep rows with start <= time < end; count what is dropped and why."""
    lo, hi = (pa.scalar(datetime.fromisoformat(s), pa.timestamp("us")) for s in (start, end))
    t = table["time"]
    counts = {
        "null_time": t.null_count,
        "before_window": pc.sum(pc.fill_null(pc.less(t, lo), False)).as_py() or 0,
        "after_window": pc.sum(pc.fill_null(pc.greater_equal(t, hi), False)).as_py() or 0,
    }
    keep = pc.fill_null(pc.and_(pc.greater_equal(t, lo), pc.less(t, hi)), False)
    return table.filter(keep), counts


def month_keys(table: pa.Table) -> pa.Array:
    return pc.strftime(table["time"], format="%Y-%m")


def daily_counts(table: pa.Table) -> dict[str, int]:
    counts = pc.value_counts(pc.strftime(table["time"], format="%Y-%m-%d")).to_pylist()
    return {r["values"]: r["counts"] for r in sorted(counts, key=lambda r: r["values"])}

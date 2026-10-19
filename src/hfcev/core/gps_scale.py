"""GPS coordinate encoding: classification and restoration of 1/100-scaled values.

Raw LAT/LON are degree-minute numbers (DDMM.mmmm / DDDMM.mmmm). On audited dates
some rows were stored divided by 100 (they look like decimal degrees). Those rows,
and only those, are multiplied by 100. No angle conversion is performed here.
"""
from __future__ import annotations

from collections import Counter

import numpy as np
import pyarrow as pa
import pyarrow.compute as pc

CLASSES = (
    "nonfinite", "zero_pair", "single_axis_zero", "dd_range",
    "large_pair_dmm_valid", "large_pair_scaled100_only", "mixed_axis_scale", "out_of_range",
)
DD_RANGE, DMM_VALID = 3, 4


def dmm_absolute(value, maximum):
    """|DDMM.mmmm| -> degrees, NaN when minutes >= 60 or degrees exceed `maximum`."""
    value = np.abs(np.asarray(value, dtype=float))
    with np.errstate(invalid="ignore"):
        degrees = np.floor(value / 100.0)
        minutes = value - degrees * 100.0
        converted = degrees + minutes / 60.0
        valid = np.isfinite(value) & (minutes < 60.0) & (converted <= maximum)
    return np.where(valid, converted, np.nan)


def classify(lat, lon) -> np.ndarray:
    """Encoding class index (see CLASSES) per coordinate pair."""
    lat, lon = np.abs(lat), np.abs(lon)
    labels = np.full(len(lat), 7, dtype=np.int8)
    finite = np.isfinite(lat) & np.isfinite(lon)
    zero_pair = finite & (lat == 0) & (lon == 0)
    one_zero = finite & ((lat == 0) ^ (lon == 0))
    usable = finite & ~zero_pair & ~one_zero
    small_lat, small_lon = lat <= 90, lon <= 180
    labels[usable & small_lat & small_lon] = DD_RANGE
    large = usable & ~small_lat & ~small_lon
    scaled_valid = (lat / 100 <= 90) & (lon / 100 <= 180)
    dmm_valid = np.isfinite(dmm_absolute(lat, 90)) & np.isfinite(dmm_absolute(lon, 180))
    labels[large & scaled_valid] = 5
    labels[large & dmm_valid] = DMM_VALID
    labels[usable & (small_lat ^ small_lon)] = 6
    labels[one_zero] = 2
    labels[zero_pair] = 1
    labels[~finite] = 0
    return labels


def _direction(column) -> np.ndarray:
    return pc.utf8_upper(pc.utf8_trim_whitespace(pc.fill_null(column, ""))).to_numpy(zero_copy_only=False)


def restore_scale(table: pa.Table, allowed_dates: list[str]):
    """Multiply LAT/LON by 100 on rows in the `dd_range` class.

    Raises if such a row falls outside `allowed_dates`, has invalid hemisphere
    letters, or would not become a valid degree-minute pair.
    Returns (table, positions changed, {date: rows changed}).
    """
    lat = table["LAT"].to_numpy(zero_copy_only=False)
    lon = table["LON"].to_numpy(zero_copy_only=False)
    mask = classify(lat, lon) == DD_RANGE
    selected = np.flatnonzero(mask)
    if not len(selected):
        return table, selected, {}
    times = table["time"].to_numpy(zero_copy_only=False).astype("datetime64[us]")
    days = Counter(times[mask].astype("datetime64[D]").astype(str).tolist())
    if not set(days) <= set(allowed_dates):
        raise ValueError(f"Small-scale GPS rows outside audited dates: {sorted(set(days) - set(allowed_dates))}")
    if not (classify(lat[mask] * 100.0, lon[mask] * 100.0) == DMM_VALID).all():
        raise ValueError("Restored GPS values are not valid degree-minute pairs")
    if not (np.isin(_direction(table["LATD"])[mask], ["N", "S"]).all()
            and np.isin(_direction(table["LOND"])[mask], ["E", "W"]).all()):
        raise ValueError("Small-scale GPS rows have invalid hemisphere letters")
    arrow_mask = pa.array(mask)
    for name in ("LAT", "LON"):
        field = table.schema.field(name)
        scaled = pc.if_else(arrow_mask, pc.multiply(table[name], pa.scalar(100.0, field.type)), table[name])
        table = table.set_column(table.schema.get_field_index(name), field, scaled)
    return table, selected, dict(days)

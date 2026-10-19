"""Vehicle identity: TBOX installations assembled into vehicles.

A vehicle map lists one row per TBOX installation: device, vehicle, installation order on the
vehicle (`unit`) and validity interval [valid_from, valid_to); empty bounds are open. A TBOX moved
between vehicles has one row per vehicle with non-overlapping intervals. Only rows with status
`confirmed` are applied. The map is private (it holds plates and TBOX numbers);
outputs carry only the anonymous vehicle id and the unit number.
"""
from __future__ import annotations

import csv
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from .clock import NAT
from .motion import haversine_km

REQUIRED = ("vehicle_id", "tbox", "unit", "valid_from", "valid_to", "status")


@dataclass(frozen=True)
class Installation:
    vehicle_id: str
    tbox: str
    unit: int
    valid_from: int  # microseconds since epoch, NAT = open
    valid_to: int
    source: str


def _us(text: str) -> int:
    text = (text or "").strip()
    if not text or text.lower() in ("nat", "nan", "none"):
        return NAT
    return int(np.datetime64(text, "us").astype(np.int64))


def read_map(path: Path, devices: list[str] | tuple[str, ...]) -> dict[str, list[Installation]]:
    """Confirmed installations per vehicle, ordered by unit; checks the map against the cohort."""
    with Path(path).open(encoding="utf-8-sig", newline="") as f:
        rows = list(csv.DictReader(f))
    missing = [c for c in REQUIRED if rows and c not in rows[0]]
    if missing:
        raise ValueError(f"vehicle map lacks columns {missing}")
    rows = [r for r in rows if r["status"].strip() == "confirmed"]
    tboxes = [r["tbox"].strip() for r in rows]
    if set(tboxes) != set(devices):
        raise ValueError(f"vehicle map and cohort differ: {sorted(set(tboxes) ^ set(devices))[:5]}")
    out: dict[str, list[Installation]] = {}
    for r in rows:
        out.setdefault(r["vehicle_id"].strip(), []).append(Installation(
            r["vehicle_id"].strip(), r["tbox"].strip(), int(r["unit"]), _us(r["valid_from"]), _us(r["valid_to"]),
            r.get("source", "").strip()))
    for tbox, units in by_tbox(out).items():
        for a, b in zip(units[:-1], units[1:]):
            if a.valid_to == NAT or b.valid_from == NAT or a.valid_to > b.valid_from:
                raise ValueError(f"TBOX {tbox}: installations on {a.vehicle_id} and {b.vehicle_id} overlap")
    for vid, units in out.items():
        units.sort(key=lambda u: u.unit)
        if [u.unit for u in units] != list(range(1, len(units) + 1)):
            raise ValueError(f"{vid}: units must be numbered 1..n")
        for a, b in zip(units[:-1], units[1:]):
            if a.valid_to == NAT or b.valid_from == NAT or a.valid_to > b.valid_from:
                raise ValueError(f"{vid}: installations {a.unit} and {b.unit} need ordered, non-overlapping bounds")
    return out


def by_tbox(units_by_vehicle: dict[str, list[Installation]]) -> dict[str, list[Installation]]:
    """Installations of each TBOX in time order (a TBOX moved between vehicles has several)."""
    out: dict[str, list[Installation]] = {}
    for units in units_by_vehicle.values():
        for u in units:
            out.setdefault(u.tbox, []).append(u)
    for units in out.values():
        units.sort(key=lambda u: (u.valid_from != NAT, u.valid_from))
    return out


def inside(t_us: np.ndarray, inst: Installation) -> np.ndarray:
    """Times within the installation interval (NAT times are outside)."""
    ok = t_us != NAT
    if inst.valid_from != NAT:
        ok &= t_us >= inst.valid_from
    if inst.valid_to != NAT:
        ok &= t_us < inst.valid_to
    return ok


def handover(prev: dict, nxt: dict, max_km_per_day: float) -> dict:
    """Continuity across a TBOX change: odometer and GPS position on both sides.

    prev/nxt: dict(last_t/first_t, km, lat, lon, gps_t) of the last valid reading of the earlier
    unit and the first of the later one (NaN / NAT when unavailable).
    """
    gap_d = (nxt["t"] - prev["t"]) / 8.64e10 if NAT not in (prev["t"], nxt["t"]) else np.nan
    dkm = nxt["km"] - prev["km"]
    rate = dkm / max(gap_d, 1 / 24) if np.isfinite(dkm) and np.isfinite(gap_d) else np.nan
    gps_d = (nxt["gps_t"] - prev["gps_t"]) / 8.64e10 if NAT not in (prev["gps_t"], nxt["gps_t"]) else np.nan
    disp = float(haversine_km(prev["lat"], prev["lon"], nxt["lat"], nxt["lon"])) \
        if np.isfinite([prev["lat"], prev["lon"], nxt["lat"], nxt["lon"]]).all() else np.nan
    consistent = bool(np.isfinite(rate) and dkm >= 0 and rate <= max_km_per_day) if np.isfinite(dkm) else None
    return dict(gap_days=None if np.isnan(gap_d) else round(gap_d, 3), km_before=_f(prev["km"]), km_after=_f(nxt["km"]),
                km_delta=_f(dkm), km_per_day=_f(rate), odometer_consistent=consistent,
                gps_gap_days=None if np.isnan(gps_d) else round(gps_d, 3), gps_displacement_km=_f(disp))


def _f(x):
    return None if x is None or not np.isfinite(x) else round(float(x), 3)

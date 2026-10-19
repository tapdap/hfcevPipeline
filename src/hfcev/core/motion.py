"""Plausibility of GPS fixes from implied speed (a quantity no timing rule uses).

Coordinates are degree-minute numbers (DDMM.mmmm / DDDMM.mmmm) with hemisphere letters.
A fix passes when the speeds implied by its nearest neighbours before and after (strictly
earlier / later in time, each within `max_gap_s`) are both at most `max_speed_kmh`.
"""
from __future__ import annotations

import numpy as np

from .gps_scale import dmm_absolute

EARTH_KM = 6371.0


def degrees(raw, hemisphere, negative: str, maximum: float) -> np.ndarray:
    """Signed decimal degrees, NaN where the degree-minute value is invalid."""
    value = dmm_absolute(np.asarray(raw, dtype=float), maximum)
    letters = np.array([(h or "").strip().upper() for h in hemisphere], dtype=object)
    return np.where(letters == negative, -value, value)


def haversine_km(lat1, lon1, lat2, lon2) -> np.ndarray:
    p1, p2 = np.radians(lat1), np.radians(lat2)
    a = np.sin((p2 - p1) / 2) ** 2 + np.cos(p1) * np.cos(p2) * np.sin(np.radians(lon2 - lon1) / 2) ** 2
    return 2 * EARTH_KM * np.arcsin(np.sqrt(np.clip(a, 0, 1)))


def _speeds_ok(t0, a0, o0, t1, a1, o1, max_speed_kmh) -> np.ndarray:
    hours = np.where(t1 > t0, (t1 - t0) / 3.6e9, np.nan)
    return haversine_km(a0, o0, a1, o1) / hours <= max_speed_kmh


def check(ref_t, ref_lat, ref_lon, t, lat, lon, max_gap_s: float, max_speed_kmh: float) -> np.ndarray:
    """Fixes against a reference track sorted by time: 1.0 pass, 0.0 fail, NaN not testable."""
    t = np.asarray(t, dtype=np.int64)
    out = np.full(len(t), np.nan)
    if len(ref_t) < 2 or not len(t):
        return out
    before = np.searchsorted(ref_t, t, side="left") - 1
    after = np.searchsorted(ref_t, t, side="right")
    b, a = np.clip(before, 0, len(ref_t) - 1), np.clip(after, 0, len(ref_t) - 1)
    gap = max_gap_s * 1e6
    testable = ((before >= 0) & (after < len(ref_t)) & np.isfinite(lat) & np.isfinite(lon)
                & (t - ref_t[b] <= gap) & (ref_t[a] - t <= gap))
    ok = (_speeds_ok(ref_t[b], ref_lat[b], ref_lon[b], t, lat, lon, max_speed_kmh)
          & _speeds_ok(t, lat, lon, ref_t[a], ref_lat[a], ref_lon[a], max_speed_kmh))
    out[testable] = ok[testable].astype(float)
    return out


def track(t, lat, lon) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Valid fixes sorted by time, usable as a reference for `check`."""
    t = np.asarray(t, dtype=np.int64)
    ok = np.isfinite(lat) & np.isfinite(lon)
    order = np.argsort(t[ok], kind="stable")
    return t[ok][order], np.asarray(lat)[ok][order], np.asarray(lon)[ok][order]

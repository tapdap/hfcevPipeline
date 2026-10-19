"""Independent plausibility test for a record's time: the odometer must not run backwards.

A record placed at time t with odometer value x passes if the nearest reference records
strictly before and after t (valid, positive odometer) satisfy before <= x <= after.
The test uses a quantity that plays no part in any timing rule.
"""
from __future__ import annotations

import numpy as np


def reference(times_us: np.ndarray, km: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    km = np.asarray(km, dtype=float)
    ok = np.isfinite(km) & (km > 0)
    order = np.argsort(times_us[ok], kind="stable")
    return times_us[ok][order], km[ok][order]


def check(ref_t: np.ndarray, ref_km: np.ndarray, t: np.ndarray, km, max_gap_s: float | None = None) -> np.ndarray:
    """1.0 pass, 0.0 fail, NaN not testable (no neighbour on a side, invalid value, gap too wide)."""
    km = np.asarray(km, dtype=float)
    out = np.full(len(t), np.nan)
    if not len(ref_t) or not len(t):
        return out
    before = np.searchsorted(ref_t, t, side="left") - 1
    after = np.searchsorted(ref_t, t, side="right")
    testable = (before >= 0) & (after < len(ref_t)) & np.isfinite(km) & (km > 0)
    b, a = np.clip(before, 0, len(ref_t) - 1), np.clip(after, 0, len(ref_t) - 1)
    if max_gap_s is not None:
        testable &= ((t - ref_t[b]) <= max_gap_s * 1e6) & ((ref_t[a] - t) <= max_gap_s * 1e6)
    passed = (ref_km[b] <= km) & (km <= ref_km[a])
    out[testable] = passed[testable].astype(float)
    return out


def summary(values: np.ndarray) -> dict:
    tested = values[~np.isnan(values)]
    return dict(tested=int(len(tested)), passed=int(tested.sum()))


def sequence_check(km) -> np.ndarray:
    """Odometer of each record against its valid neighbours in a given order (e.g. receipt order):
    1.0 pass, 0.0 fail, NaN not testable."""
    km = np.asarray(km, dtype=float)
    out = np.full(len(km), np.nan)
    valid = np.flatnonzero(np.isfinite(km) & (km > 0))
    if len(valid) < 3:
        return out
    v = km[valid]
    passed = (v[:-2] <= v[1:-1]) & (v[1:-1] <= v[2:])
    out[valid[1:-1]] = passed.astype(float)
    return out

"""Derived quantities: GPS fix status, GPS track steps, odometer intervals, CAN-GPS alignment, runs.

All functions take arrays already sorted by their time axis unless stated otherwise and
return arrays aligned with their inputs. No input value is changed.
"""
from __future__ import annotations

import numpy as np

from .motion import haversine_km

NO_FIX, NO_FIX_INFERRED, VALID, VALID_ASSUMED = "no_fix", "no_fix_inferred", "valid", "valid_assumed"


def fix_proxy(t_us: np.ndarray, lat: np.ndarray, lon: np.ndarray, min_speed_kmh: float,
              odo_t: np.ndarray | None = None, odo_km: np.ndarray | None = None) -> np.ndarray:
    """True for fixes that repeat a frozen position while the vehicle evidently moved.

    A fix repeating the previous fix's position belongs to a frozen run anchored at the
    first fix of the run. The vehicle moved during the run when (a) the next fix after the
    run lies so far away that it was reached at >= min_speed_kmh since the anchor, or
    (b) the odometer (odo_t sorted, odo_km valid readings) advanced between the anchor and
    the end of the run. The repeats are then inferred to carry no fix (e.g. in a tunnel,
    or a GPS receiver stuck at one position). Input sorted by device time.
    """
    def odometer_rise(a_us, b_us):
        if odo_t is None or not len(odo_t):
            return 0.0
        i, j = np.searchsorted(odo_t, [a_us, b_us], side="right") - 1
        return float(odo_km[j] - odo_km[i]) if i >= 0 and j >= 0 else 0.0

    n = len(t_us)
    out = np.zeros(n, dtype=bool)
    if n < 3:
        return out
    repeat = np.zeros(n, dtype=bool)
    repeat[1:] = (lat[1:] == lat[:-1]) & (lon[1:] == lon[:-1]) & (t_us[1:] > t_us[:-1])
    i = 1
    while i < n:
        if not repeat[i]:
            i += 1
            continue
        anchor, j = i - 1, i
        while j < n and repeat[j]:
            j += 1
        moved = odometer_rise(t_us[anchor], t_us[j - 1]) >= 1.0
        if not moved and j < n:  # j = first fix after the frozen run
            hours = (t_us[j] - t_us[anchor]) / 3.6e9
            moved = hours > 0 and haversine_km(lat[anchor], lon[anchor], lat[j], lon[j]) / hours >= min_speed_kmh
        if moved:
            out[i:j] = True
        i = j
    return out


def track_steps(t_us: np.ndarray, lat: np.ndarray, lon: np.ndarray, usable: np.ndarray, max_gap_s: float):
    """Step distance (km), speed (km/h) and duration (s) from the previous usable fix, NaN where no step.

    A step needs both fixes usable, 0 < dt <= max_gap_s and no unusable fix between them
    (so a no-fix stretch such as a tunnel breaks the track instead of being bridged).
    Input sorted by device time.
    """
    n = len(t_us)
    dist, speed, dur = np.full(n, np.nan), np.full(n, np.nan), np.full(n, np.nan)
    idx = np.flatnonzero(usable)
    if len(idx) < 2:
        return dist, speed, dur
    bad_before = np.cumsum(~usable)
    a, b = idx[:-1], idx[1:]
    dt = (t_us[b] - t_us[a]) / 1e6
    ok = (dt > 0) & (dt <= max_gap_s) & (bad_before[b] == bad_before[a])
    d = haversine_km(lat[a], lon[a], lat[b], lon[b])
    dist[b[ok]] = d[ok]
    speed[b[ok]] = d[ok] / (dt[ok] / 3600)
    dur[b[ok]] = dt[ok]
    return dist, speed, dur


def odometer_steps(t_us: np.ndarray, km: np.ndarray, segment: np.ndarray, max_speed_kmh: float,
                   max_gap_s: float = np.inf):
    """Increments between consecutive valid odometer readings (km > 0), in receipt order.

    Returns (increment, kind, previous index) per reading; kind is 'in_segment' or 'across_gap'
    for a qualified increment (0 <= dkm <= max_speed * dt + 1 km resolution) between segments
    at most max_gap_s apart, 'across_long_gap' for a qualified increment over a longer gap
    (distance not attributable in time), 'in_segment_delayed' for a forward increment inside a
    segment that exceeds the limit on the receipt clock (delayed delivery compresses receipt
    times; the distance is real but no speed may be derived from it), 'backward', 'jump'
    (forward beyond the limit across segments), '' for the first reading or invalid values.
    """
    n = len(t_us)
    inc = np.full(n, np.nan)
    kind = np.full(n, "", dtype=object)
    prev = np.full(n, -1, dtype=np.int64)
    idx = np.flatnonzero(np.isfinite(km) & (km > 0))
    if len(idx) < 2:
        return inc, kind, prev
    a, b = idx[:-1], idx[1:]
    d = km[b] - km[a]
    limit = max_speed_kmh * (t_us[b] - t_us[a]) / 3.6e9 + 1.0
    same = segment[b] == segment[a]
    long_gap = (t_us[b] - t_us[a]) / 1e6 > max_gap_s
    k = np.where(d < 0, "backward", np.where(d > limit, np.where(same, "in_segment_delayed", "jump"), np.where(
        same, "in_segment", np.where(long_gap, "across_long_gap", "across_gap"))))
    inc[b], kind[b], prev[b] = d, k, a
    return inc, kind, prev


def counter_steps(value: np.ndarray, km: np.ndarray, period: float, min_inferred_km: float):
    """Restore a cumulative counter that wraps at `period` (e.g. hydrogen consumed, integer kg).

    Readings <= 0 or non-finite count as not reported. Between consecutive valid readings
    (receipt order) a drop of more than half a period is one wrap. Where the odometer advanced
    by at least `min_inferred_km` between the two readings, the number of wraps is taken from
    the expected consumption (device rate x distance), so wraps hidden in a data gap are counted.
    Returns (restored value, increment, kind) per reading; kind is 'increment', 'wrap',
    'wrap_inferred' (count set from the odometer), 'backward' (a smaller decrease, kept as is) or
    '' (first reading / not reported). Restored = value + period x cumulative wraps.
    """
    n = len(value)
    restored, inc = np.full(n, np.nan), np.full(n, np.nan)
    kind = np.full(n, "", dtype=object)
    idx = np.flatnonzero(np.isfinite(value) & (value > 0))
    if not len(idx):
        return restored, inc, kind
    a, b = idx[:-1], idx[1:]
    d = value[b] - value[a]
    dkm = km[b] - km[a] if km is not None else np.full(len(a), np.nan)
    wraps = (d < -period / 2).astype(np.int64)
    ok = (d >= 0) & (d <= period / 2) & np.isfinite(dkm) & (dkm > 0) & (dkm < min_inferred_km)   # rate from short steps
    rate = d[ok].sum() / dkm[ok].sum() if ok.any() and dkm[ok].sum() > 0 else np.nan   # counter units per km
    infer = np.isfinite(dkm) & (dkm >= min_inferred_km) & np.isfinite(rate)
    expected = np.where(infer, np.round((rate * np.where(infer, dkm, 0) - d) / period), 0).astype(np.int64)
    inferred = infer & (expected != wraps) & (expected >= 0)
    wraps = np.where(inferred, expected, wraps)
    k = np.where(inferred, "wrap_inferred", np.where(wraps > 0, "wrap", np.where(d < 0, "backward", "increment")))
    restored[idx] = value[idx] + period * np.r_[0, np.cumsum(wraps)]
    inc[b] = restored[b] - restored[a]
    kind[b] = k
    return restored, inc, kind


def align(can_t: np.ndarray, gps_t: np.ndarray, tolerance_s: float, mode: str = "nearest", tie: str = "earlier"):
    """For each CAN time, the GPS fix (index into sorted gps_t) within tolerance.

    mode 'nearest' takes the closest fix; with tie='ambiguous' a tie between two different fixes
    gives -2, with tie='earlier' the earlier fix is taken (the receipt clock trails the device clock);
    'backward' takes the latest fix not after the CAN time. Returns (index, offset_s = gps - can);
    index -1 when no fix lies within tolerance.
    """
    n = len(can_t)
    index = np.full(n, -1, dtype=np.int64)
    offset = np.full(n, np.nan)
    if not len(gps_t) or not n:
        return index, offset
    tol = tolerance_s * 1e6
    right = np.searchsorted(gps_t, can_t, side="left")
    left = np.searchsorted(gps_t, can_t, side="right") - 1
    if mode == "backward":
        ok = (left >= 0) & (can_t - gps_t[np.clip(left, 0, None)] <= tol)
        index[ok] = left[ok]
    else:
        r = np.clip(right, 0, len(gps_t) - 1)
        lft = np.clip(left, 0, len(gps_t) - 1)
        dr = np.where(right < len(gps_t), gps_t[r] - can_t, np.inf)
        dl = np.where(left >= 0, can_t - gps_t[lft], np.inf)
        best = np.minimum(dr, dl)
        ok = best <= tol
        pick = np.where(dl <= dr, lft, r)
        index[ok] = pick[ok]
        if tie == "ambiguous":
            index[ok & (dl == dr) & (lft != r)] = -2
    hit = index >= 0
    offset[hit] = (gps_t[index[hit]] - can_t[hit]) / 1e6
    return index, offset


def gap_check(t_us: np.ndarray, km_inc: np.ndarray, mask: np.ndarray, prev: np.ndarray, gps_t: np.ndarray,
              lat: np.ndarray, lon: np.ndarray, window_s: float) -> dict:
    """GPS evidence for odometer increments (mask) between readings prev -> i (receipt clock).

    For each increment, the longest interval between consecutive usable GPS fixes (device clock)
    in [t(prev) - window_s, t(i) + 60 s] and the straight-line displacement across it, as a share
    of the odometer increment. A delivery delay shows as a long GPS gap whose displacement matches
    the increment (the vehicle drove through a transmission outage). Returns summary statistics.
    """
    gaps, ratio = [], []
    for i in np.flatnonzero(mask):
        lo, hi = np.searchsorted(gps_t, [t_us[prev[i]] - window_s * 1e6, t_us[i] + 60e6])
        if hi - lo < 2:
            continue
        d = np.diff(gps_t[lo:hi])
        k = lo + int(np.argmax(d))
        gaps.append(d.max() / 1e6)
        ratio.append(haversine_km(lat[k], lon[k], lat[k + 1], lon[k + 1]) / km_inc[i])
    gaps, ratio = np.array(gaps), np.array(ratio)
    q = lambda a: dict(zip(["p25", "p50", "p75"], np.percentile(a, [25, 50, 75]).round(3).tolist())) if len(a) else {}
    return dict(n=int(mask.sum()), with_gps=int(len(gaps)), gap_s=q(gaps), gap_over_120s=int((gaps > 120).sum()),
                displacement_to_odometer=q(ratio))


def runs(mask: np.ndarray, segment: np.ndarray) -> list[tuple[int, int]]:
    """[start, end) index ranges of consecutive True values that stay inside one segment."""
    if not len(mask):
        return []
    brk = np.ones(len(mask), dtype=bool)
    brk[1:] = (mask[1:] != mask[:-1]) | (segment[1:] != segment[:-1])
    starts = np.flatnonzero(brk)
    ends = np.append(starts[1:], len(mask))
    return [(int(s), int(e)) for s, e in zip(starts, ends) if mask[s]]

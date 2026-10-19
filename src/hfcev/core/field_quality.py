"""Per-field quality flags that need no unconfirmed physical knowledge.

From the CAN protocol (DBC) each operation field maps to one or more signals (same alias
rule as stage 2). A signal of `length` bits with scaling (factor, offset) can only
produce values in its encodable range; a stored value outside it cannot come from a
correct decode. The all-ones raw value is the conventional "not available" code.
The DBC declares no physical ranges, so no physical-range check is made here.
Flags per field are kept as bits of one integer per record (bit j = field j).
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from .dbc import MESSAGE, NON_BUS_MESSAGE, SIGNAL
from .matching import canonical
from .messages import field_alias


@dataclass(frozen=True)
class Encoding:
    length: int
    signed: bool
    factor: float
    offset: float

    def bounds(self) -> tuple[float, float]:
        lo, hi = (-(1 << (self.length - 1)), (1 << (self.length - 1)) - 1) if self.signed else (0, (1 << self.length) - 1)
        a, b = lo * self.factor + self.offset, hi * self.factor + self.offset
        return min(a, b), max(a, b)

    def all_ones(self) -> float:
        raw = -1 if self.signed else (1 << self.length) - 1
        return raw * self.factor + self.offset


def encodings(dbc_path: Path, fields: list[str]) -> dict[str, list[Encoding]]:
    """Field -> encodings of every bus signal whose name matches it (case/underscore-insensitive)."""
    raw = Path(dbc_path).read_bytes()
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        text = raw.decode("gb18030")
    wanted = {field_alias(f): f for f in fields}
    found, current = {f: [] for f in fields}, None
    for line in text.splitlines():
        m = MESSAGE.match(line)
        if m:
            current = None if m[2] == NON_BUS_MESSAGE else m[2]
            continue
        s = SIGNAL.match(line) if current and re.match(r"^\s*SG_\s+", line) else None
        if s and field_alias(s[1]) in wanted:
            enc = Encoding(int(s[3]), s[5] == "-", float(s[6]), float(s[7]))
            if enc not in found[wanted[field_alias(s[1])]]:
                found[wanted[field_alias(s[1])]].append(enc)
    return found


def flags(values: np.ndarray, encs: list[Encoding]) -> dict[str, np.ndarray]:
    """missing / zero / outside_encodable / all_ones masks for one field (values: float, NaN = missing)."""
    missing = ~np.isfinite(values)
    out = dict(missing=missing, zero=~missing & (values == 0))
    if encs:
        inside = np.zeros(len(values), dtype=bool)
        ones = np.zeros(len(values), dtype=bool)
        for e in encs:
            lo, hi = e.bounds()
            tol = abs(e.factor) * 1e-6
            inside |= (values >= lo - tol) & (values <= hi + tol)
            ones |= np.isclose(values, e.all_ones(), rtol=0, atol=tol)
        out["outside_encodable"] = ~missing & ~inside
        out["all_ones"] = ~missing & ones
    else:
        out["outside_encodable"] = out["all_ones"] = np.zeros(len(values), dtype=bool)
    return out


def compare(stored: np.ndarray, logged: np.ndarray) -> dict[str, np.ndarray]:
    """Stored value vs log-decoded value of the same message, at 6-decimal resolution."""
    s = np.array([canonical(v) for v in stored.tolist()], dtype=object)
    lg = np.array([canonical(v) for v in logged.tolist()], dtype=object)
    s_ok, l_ok = s != None, lg != None  # noqa: E711 (element-wise)
    return dict(equal=s_ok & l_ok & (s == lg), differ=s_ok & l_ok & (s != lg),
                stored_only=s_ok & ~l_ok, log_only=~s_ok & l_ok, both_missing=~s_ok & ~l_ok)


def scale_hint(stored: np.ndarray, logged: np.ndarray) -> dict:
    """For differing pairs: share explained by stored = log x 10^k, or by an exact sign flip."""
    ok = np.isfinite(stored) & np.isfinite(logged) & (logged != 0) & (stored != 0)
    if not ok.any():
        return {}
    ratio = stored[ok] / logged[ok]
    hint = {f"x1e{k}": int(np.isclose(ratio, 10.0 ** k, rtol=1e-6).sum()) for k in range(-3, 4) if k}
    hint["sign_flip"] = int(np.isclose(ratio, -1.0, rtol=1e-9).sum())
    hint["median_ratio"] = float(np.median(ratio))
    return {k: v for k, v in hint.items() if v}


def stuck_runs(values: np.ndarray, times_us: np.ndarray, segment: np.ndarray, min_s: list[float]) -> dict:
    """Constant runs: consecutive equal values inside one segment (inputs in time order).

    Returns, per threshold, the number of runs lasting at least that long and the records
    they cover, separately for zero and non-zero values. Statistics only, no flags.
    """
    n = len(values)
    out = {f"{int(m)}s": dict(runs_nonzero=0, records_nonzero=0, runs_zero=0, records_zero=0) for m in min_s}
    if n < 2:
        return out
    finite = np.isfinite(values)
    brk = np.ones(n, dtype=bool)
    brk[1:] = (segment[1:] != segment[:-1]) | ~finite[1:] | ~finite[:-1] | (values[1:] != values[:-1])
    run = np.cumsum(brk) - 1
    starts = np.flatnonzero(brk)
    duration = (np.maximum.reduceat(times_us, starts) - np.minimum.reduceat(times_us, starts)) / 1e6
    records = np.bincount(run)
    first = values[starts]
    ok = np.isfinite(first)
    for m in min_s:
        long = ok & (duration >= m)
        zero = long & (first == 0)
        out[f"{int(m)}s"] = dict(runs_nonzero=int((long & ~zero).sum()), records_nonzero=int(records[long & ~zero].sum()),
                                 runs_zero=int(zero.sum()), records_zero=int(records[zero].sum()))
    return out


def jumps(values: np.ndarray, times_us: np.ndarray, segment: np.ndarray, max_dt_s: float, share: float) -> dict:
    """Steps between consecutive records of one segment (0 < dt <= max_dt_s) larger than `share`
    of the device's value range (p99.5 - p0.5). Statistics only, no flags."""
    finite = np.isfinite(values)
    if finite.sum() < 3:
        return dict(pairs=0, jumps=0, threshold=None)
    lo, hi = np.percentile(values[finite], [0.5, 99.5])
    threshold = share * (hi - lo)
    dt = np.diff(times_us) / 1e6
    pair = (segment[1:] == segment[:-1]) & (dt > 0) & (dt <= max_dt_s) & finite[1:] & finite[:-1]
    step = np.abs(np.diff(values))
    big = pair & (step > threshold) if threshold > 0 else np.zeros(len(pair), dtype=bool)
    return dict(pairs=int(pair.sum()), jumps=int(big.sum()), threshold=float(threshold))

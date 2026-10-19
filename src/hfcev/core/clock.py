"""Is a message's reported (device) clock usable as its acquisition time?

Two clocks exist for every log message: the server receipt clock (log time) and the
device clock reported in the payload. Stored records carry the receipt clock. The
device clock is offered as an additional, verified time only. Checks on the messages
of one device, in log (receipt) order:
  V1 parse     both clocks parse
  V2 lag       -max_lead <= log time - reported time <= max_lag
               (a small device-clock lead is normal skew between two clocks; reported
               times far older than receipt come from reset device clocks)
  V3 order     among V1+V2 messages, the reported time is strictly between those of the
               previous and next message in receipt order (both neighbours required)
  V4 reuse     no other message of the device carries the same reported time with
               different content
The log time is never used as acquisition time and no clock is interpolated.
"""
from __future__ import annotations

import numpy as np

NAT = np.iinfo(np.int64).min


def to_us(values) -> np.ndarray:
    """datetime-like array -> int64 microseconds, NAT for missing."""
    arr = np.asarray(values, dtype="datetime64[us]")
    out = arr.astype(np.int64)
    out[np.isnat(arr)] = NAT
    return out


def checks(reported_us: np.ndarray, log_us: np.ndarray, content: list, identity_ok: np.ndarray,
           max_lead_s: float, max_lag_s: float) -> dict[str, np.ndarray]:
    present = (reported_us != NAT) & (log_us != NAT)
    v1 = present
    lag_s = np.where(present, (log_us - reported_us) / 1e6, np.nan)
    v2 = v1 & (lag_s >= -max_lead_s) & (lag_s <= max_lag_s)

    v3 = np.zeros(len(reported_us), dtype=bool)
    idx = np.flatnonzero(v2)
    if len(idx) >= 3:
        t = reported_us[idx]
        inner = (t[1:-1] > t[:-2]) & (t[1:-1] < t[2:])
        v3[idx[1:-1][inner]] = True

    contents = {}
    for t, c in zip(reported_us.tolist(), content):
        if t != NAT:
            contents.setdefault(t, set()).add(c)
    reused = {t for t, cs in contents.items() if len(cs) > 1}
    v4 = np.array([t not in reused for t in reported_us.tolist()], dtype=bool)

    valid = v2 & v3 & v4 & identity_ok
    return dict(v1=v1, v2=v2, v3=v3, v4=v4, identity=identity_ok, valid=valid, lag_s=lag_s)


def first_failure(c: dict, i: int) -> str | None:
    for name, label in (("identity", "identity_unresolved"), ("v1", "clock_missing"),
                        ("v2", "clock_lag_out_of_range"), ("v3", "clock_out_of_order"), ("v4", "clock_reused")):
        if not c[name][i]:
            return label
    return None


def legacy_chain_ok(reported_us: np.ndarray, log_us: np.ndarray, reused: np.ndarray) -> np.ndarray:
    """Diagnostic only: approximate earlier rule (two neighbours each side in receipt order,
    strictly forward reported clock on a 5 s grid, strictly forward log clock, target not
    after its log time, reported and log dates equal across the chain, no clock reuse)."""
    n = len(reported_us)
    ok = np.zeros(n, dtype=bool)
    if n < 5:
        return ok
    present = (reported_us != NAT) & (log_us != NAT)
    day_r = np.where(present, reported_us // 86_400_000_000, -1)
    day_l = np.where(present, log_us // 86_400_000_000, -2)
    same_day = present & (day_r == day_l)
    dr = np.diff(reported_us)
    step_ok = (dr > 0) & (dr % 5_000_000 == 0) & present[1:] & present[:-1]
    log_ok = np.diff(log_us) > 0
    for i in range(2, n - 2):
        if (step_ok[i - 2:i + 2].all() and log_ok[i - 2:i + 2].all() and same_day[i - 2:i + 3].all()
                and reported_us[i] <= log_us[i] and not reused[i]):
            ok[i] = True
    return ok


def null_collisions(times: np.ndarray) -> np.ndarray:
    """Mask of entries whose (non-missing) time is shared with another entry of the same device.

    Distinct records cannot have been acquired at the same device instant; when they claim
    to, none of the claims is kept (no winner is chosen)."""
    present = times != NAT
    values, counts = np.unique(times[present], return_counts=True)
    shared = set(values[counts > 1].tolist())
    return present & np.array([t in shared for t in times.tolist()], dtype=bool)

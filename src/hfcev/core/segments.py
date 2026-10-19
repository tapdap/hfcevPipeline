"""Reporting segments: maximal runs of records whose consecutive times differ by at most a gap."""
from __future__ import annotations

import numpy as np


def split(sorted_us: np.ndarray, max_gap_s: float) -> np.ndarray:
    """Segment number (0, 1, ...) per record of a time-sorted array; a new segment starts after a gap > max_gap_s."""
    if not len(sorted_us):
        return np.array([], dtype=np.int64)
    breaks = np.diff(sorted_us) > max_gap_s * 1e6
    return np.concatenate([[0], np.cumsum(breaks)]).astype(np.int64)


def count(sorted_us: np.ndarray, max_gap_s: float) -> int:
    return int(split(sorted_us, max_gap_s)[-1] + 1) if len(sorted_us) else 0


def hours_covered(start_us: int, end_us: int, hours: set) -> float:
    """Share of whole hours in [start, end) for which a log file exists (hour index = us // 3.6e9)."""
    first, last = start_us // 3_600_000_000, (end_us - 1) // 3_600_000_000
    span = range(int(first), int(last) + 1)
    return sum(h in hours for h in span) / len(span) if len(span) else 0.0

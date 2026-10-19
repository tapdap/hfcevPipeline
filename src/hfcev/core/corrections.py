"""Reversible correction of a known decoding defect in the stored operation records.

Signals HighPPMNum (bits 32-35) and HyBottleP (bits 36-47, factor 0.1) share bytes D4 and
D5 of one CAN frame. The historical parser sliced an MSB-first bit string at the DBC start
bit, so it stored
    HighPPMNum_stored = D4 >> 4                                   (should be D4 & 0x0F)
    HyBottleP_stored  = (((D4 & 0x0F) << 4) + (D5 >> 4) + ((D5 & 0x0F) << 8)) * 0.1
                                                                  (should be ((D4 >> 4) + (D5 << 4)) * 0.1)
Every bit survives, only displaced, so both DBC values are recovered exactly from the two
stored values. Stored values that cannot arise from that parser (missing, non-integer raw,
raw out of range) are left unreconstructed.
"""
from __future__ import annotations

import numpy as np

FACTOR = 0.1


def nibble_swap(hybottlep, highppmnum):
    """Return (HyBottleP_dbc, HighPPMNum_dbc, reconstructable) from the stored pair."""
    bp = np.asarray(hybottlep, dtype=float)
    hp = np.asarray(highppmnum, dtype=float)
    raw = bp / FACTOR
    ok = (np.isfinite(raw) & np.isfinite(hp) & (np.abs(raw - np.rint(raw)) < 1e-6) & (np.rint(raw) >= 0)
          & (np.rint(raw) <= 0xFFF) & (hp == np.rint(hp)) & (hp >= 0) & (hp <= 0xF))
    r = np.where(ok, np.rint(raw), 0).astype(np.int64)
    h = np.where(ok, hp, 0).astype(np.int64)
    d5 = ((r & 0xF) << 4) | (r >> 8)
    fixed_bp = np.where(ok, (h + (d5 << 4)) * FACTOR, np.nan)
    fixed_hp = np.where(ok, (r >> 4) & 0xF, np.nan).astype(float)
    return fixed_bp, fixed_hp, ok


def buggy_encode(d4: int, d5: int) -> tuple[float, int]:
    """Stored (HyBottleP, HighPPMNum) the historical parser produced from bytes D4, D5 (for tests)."""
    return ((((d4 & 0xF) << 4) + (d5 >> 4) + ((d5 & 0xF) << 8)) * FACTOR, d4 >> 4)


def dbc_decode(d4: int, d5: int) -> tuple[float, int]:
    """DBC values of (HyBottleP, HighPPMNum) from bytes D4, D5 (for tests)."""
    return (((d4 >> 4) + (d5 << 4)) * FACTOR, d4 & 0xF)

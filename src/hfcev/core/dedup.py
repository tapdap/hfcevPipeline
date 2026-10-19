"""Record dispositions for overlapping sources.

All comparisons are exact on values (no hashing, no tolerance). Provenance
columns (id, sn, dump metadata) are ignored. Distinct records sharing a
timestamp within one source are all kept; nothing is chosen arbitrarily.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

CAN_DUPLICATE = "can_exact_duplicate"
RUN_DUPLICATE = "run_exact_duplicate"
RUN_MATCHES_CAN = "run_matches_can_shared_fields"
RUN_CONFLICT = "run_conflict_prefer_can"
ORDER = ["time", "source_stream", "source_file", "source_row"]


def operation_dispositions(frame: pd.DataFrame, business: list[str], shared: list[str]):
    """Decide which operation records to keep when CAN tables and the legacy run table overlap.

    1. Within each source, an exact repeat of time + all business fields is a duplicate
       (the first occurrence in file/row order is kept).
    2. A legacy run record at a timestamp that CAN also covers is retired: as a match if
       every field the two sources share is equal, otherwise as a conflict resolved
       in favour of CAN. The legacy table lacks CAN-only fields, hence `shared`.

    `frame` needs time, business fields, source_stream, source_file and source_row.
    Returns (positions to keep, ordered by time/source; reason per row, '' if kept).
    """
    order = frame.sort_values(["source_file", "source_row"], kind="stable").index
    reason = np.full(len(frame), "", dtype=object)
    for stream, label in (("can", CAN_DUPLICATE), ("run", RUN_DUPLICATE)):
        ix = order[frame.loc[order, "source_stream"].to_numpy() == stream]
        repeated = frame.loc[ix].duplicated(["time"] + business, keep="first").to_numpy()
        reason[ix[repeated]] = label

    is_can = frame["source_stream"].to_numpy() == "can"
    can = np.flatnonzero(is_can & (reason == ""))
    run = np.flatnonzero(~is_can & (reason == ""))
    overlap = run[frame.loc[run, "time"].isin(frame.loc[can, "time"]).to_numpy()]
    if len(overlap):
        keys = ["time"] + shared
        right = frame.loc[can, keys].drop_duplicates()
        left = frame.loc[overlap, keys].copy()
        left["_row"] = overlap
        matched = left.merge(right, on=keys, how="left", indicator=True, validate="many_to_one")
        rows = matched["_row"].to_numpy(dtype=np.int64)
        same = matched["_merge"].eq("both").to_numpy()
        reason[rows[same]] = RUN_MATCHES_CAN
        reason[rows[~same]] = RUN_CONFLICT

    keep = np.flatnonzero(reason == "")
    keep = frame.loc[keep].sort_values(ORDER, kind="stable").index.to_numpy(dtype=np.int64)
    return keep, reason


def gps_dispositions(frame: pd.DataFrame, business: list[str]):
    """Exact repeats of time + all GPS business fields are duplicates; first occurrence kept.

    Returns (positions to keep ordered by time/source, positions retired).
    """
    order = frame.sort_values(["source_file", "source_row"], kind="stable").index
    repeated = frame.loc[order].duplicated(["time"] + business, keep="first").to_numpy()
    retired = order[repeated].to_numpy(dtype=np.int64)
    keep = frame.loc[order[~repeated]].sort_values(["time", "source_file", "source_row"], kind="stable")
    return keep.index.to_numpy(dtype=np.int64), retired


def same_time_variants(times: pd.Series) -> np.ndarray:
    """Number of kept records sharing each record's timestamp (1 = unique)."""
    frame = times.to_frame("time")
    return frame.groupby("time", dropna=False)["time"].transform("size").to_numpy(dtype=np.int32)

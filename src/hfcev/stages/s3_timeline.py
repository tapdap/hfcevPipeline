"""Stage 3 — attach log clocks to stored records and add records found only in the logs.

Methods: "Timestamp reconciliation and log supplementation".

Two clocks exist for every log message: the server receipt clock (log time) and the
device clock reported in the payload. The stored operation `time` is the receipt clock
(to the second). No stored record is moved. Per device, using the stage-1 operation
stream (stored records) and stage-2 log messages:
1. Correspondence (`core.matching`): bidirectionally unique content match on 16 fields.
   A matched record gains `time_received_ms` (the log time of its message).
2. Device clock (`core.clock`, V1-V4 + identity) on every log message, in receipt order.
   A matched record gains `time_device` (its message's reported time) only if it passes;
   `time_device_status` is "verified" or the first failed check.
3. Additions: log messages whose core content occurs in no stored record, received inside
   the study window, of sound native quality, first of their (reported time, content).
   All are added at `time` = receipt time truncated to the second; `time_device` as in 2.
4. Device-time axis: a device time shared by two records (stored or added) is nulled for
   all of them ("device_time_collision"); no winner is chosen.
5. `same_time_variants` is recounted over stored records and additions together.

GPS (stored GPS `time` is the device clock; see `_gps`):
6. A log GPS fix corresponds to a stored fix with the same device time and coordinates
   (rounded to `gps_coordinate_decimals`); the stored fix gains `time_received_ms`.
7. A log fix received inside the window, valid (degree-minute coordinates, N/S and E/W),
   at a device time with no stored fix, first of its (device time, coordinates), is added
   at its device time if its device clock passes V1-V4 (checked within the GPS stream).
   Fixes with a failed clock, or at a device time already holding a different stored fix,
   stay pending and are not published. Stored GPS rows are never changed.

Diagnostics in the manifest record: ordering of stored same-time groups by the two new
clocks, odometer tests (`core.odometer`) against the stored timeline, among verified records
on the device clock, and in receipt order,
threshold sensitivity of the clock checks, the earlier rule's approximate verdict, and the
earlier candidate-pool definition (reported time in window) for exact regression.

Outputs in L3_timeline/: operation/<device>.parquet (stored records + clock columns),
additions/<device>.parquet, log_events/<device>.parquet (checks and disposition per message),
gps/<device>.parquet (stored fixes + receipt clock), gps_additions/<device>.parquet,
gps_log_events/<device>.parquet. GPS plausibility uses implied speed (`core.motion`).
"""
from __future__ import annotations

from collections import Counter
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

from .. import io
from ..core import clock, gps_scale, matching, motion, odometer
from ..core.clock import NAT
from ..io import atomic_path, sha256_file
from . import s1_harmonize

NAME = "s3"
SECTION = "s3_timeline"
PARAM_SECTIONS = ("s1_harmonize", SECTION)
CODE = (matching, clock, odometer, motion, gps_scale, io)
LAYER = "L3_timeline"
TIGHT_GAP_S = 600
VERIFIED = "verified"
COLLISION = "device_time_collision"
PROVENANCE = ["event_id", "source_index", "byte_offset", "end_offset", "log_time", "identity_status"]
GPS_LOG_COLUMNS = ["event_id", "log_time", "source_index", "byte_offset", "end_offset", "device_time",
                   "latitude_raw", "latitude_hemisphere", "longitude_raw", "longitude_hemisphere", "result_raw"]


def prepare(cfg, devices, force=False, workers=1, options=None) -> dict:
    window, params = cfg.release["dataset"], cfg.release[SECTION]
    us = lambda s: int(np.datetime64(datetime.fromisoformat(s), "us").astype(np.int64))
    return dict(l1=str(cfg.layer(s1_harmonize.LAYER)), l2=str(cfg.layer("L2_logs")), out=str(cfg.layer(LAYER)),
                start_us=us(window["window_start"]), end_us=us(window["window_end"]),
                fields=s1_harmonize.operation_fields(cfg), max_lead_s=params["max_reported_lead_s"],
                max_lag_s=params["max_reported_lag_s"], grid_lead=params["sensitivity_lead_s"],
                grid_lag=params["sensitivity_lag_s"],
                additions_require_verified=params.get("additions_require_verified_device_time", False),
                gps_decimals=params["gps_coordinate_decimals"], gps_max_speed=params["gps_max_speed_kmh"],
                gps_max_gap=params["gps_speed_max_gap_s"])


def _log_us(log_time: list) -> np.ndarray:
    s = pd.Series(log_time, dtype="string").str.replace(",", ".", regex=False)
    return clock.to_us(pd.to_datetime(s, format="ISO8601").to_numpy())


def _quality_ok(ops: dict) -> np.ndarray:
    """Native quality of a decoded message (as for the earlier candidate pool)."""
    return np.array([ident in matching.GOOD_IDENTITY and nf > 0 and not uf and not qc and not lc
                     for ident, nf, uf, qc, lc in zip(ops["identity_status"], ops["normal_frames"], ops["unknown_frames"],
                                                      ops["qualified_conflicts"], ops["legacy_conflicts"])], dtype=bool)


def _empty_ops(fields) -> dict:
    cols = ["event_id", "message_time", "log_time", "identity_status", "normal_frames", "unknown_frames",
            "qualified_signals", "qualified_conflicts", "legacy_conflicts", "source_index", "byte_offset", "end_offset"]
    return {c: [] for c in cols + list(fields)}


def _pool(candidate: np.ndarray, reported: np.ndarray, content: list) -> tuple[np.ndarray, int]:
    """First occurrence of each (reported time, content) among candidate messages."""
    seen, pool, repeated = set(), np.zeros(len(candidate), dtype=bool), 0
    for i in np.flatnonzero(candidate):
        key = (int(reported[i]), content[i])
        if key in seen:
            repeated += 1
            continue
        seen.add(key)
        pool[i] = True
    return pool, repeated


def _clock_status(chk: dict) -> np.ndarray:
    return np.array([VERIFIED if chk["valid"][i] else clock.first_failure(chk, i)
                     for i in range(len(chk["valid"]))], dtype=object)


def _ts(values, unit="us") -> pa.Array:
    arr = np.asarray(values, dtype=np.int64)
    return pa.array(np.where(arr == NAT, 0, arr), pa.timestamp(unit), mask=arr == NAT)


def _ms(us: np.ndarray) -> np.ndarray:
    return np.where(us == NAT, NAT, us // 1000)


def _odo(ref_t, ref_km, t, km) -> dict:
    return dict(any_gap=odometer.summary(odometer.check(ref_t, ref_km, t, km)),
                within_600s=odometer.summary(odometer.check(ref_t, ref_km, t, km, TIGHT_GAP_S)))


def _ordered(groups: np.ndarray, values: np.ndarray) -> int:
    """Number of groups whose members all carry a value and no two share it."""
    if not len(groups):
        return 0
    df = pd.DataFrame({"g": groups, "v": np.where(values == NAT, np.nan, values.astype(float))})
    g = df.groupby("g")["v"]
    return int(((g.count() == g.size()) & (g.nunique() == g.size())).sum())


def process(device: str, ctx: dict) -> dict:
    out, fields = Path(ctx["out"]), ctx["fields"]
    stored = pq.read_table(Path(ctx["l1"]) / "can_run" / f"{device}.parquet")
    op_path = Path(ctx["l2"]) / "operation" / f"{device}.parquet"
    ops = pq.read_table(op_path).to_pydict() if op_path.exists() else _empty_ops(fields)
    n, m = stored.num_rows, len(ops["event_id"])

    # 1. correspondence (a match field absent from the export is unobserved for every record)
    raw = {f: matching.canonical_column(ops.get(f, [None] * m)) for f in matching.MATCH_FIELDS}
    raw_core, raw_aux = matching.keys(raw, m)
    keyed = stored
    for f in matching.MATCH_FIELDS:
        if f not in keyed.column_names:
            keyed = keyed.append_column(f, pa.nulls(n, pa.float64()))
    old_core, old_aux = matching.stored_keys(keyed, set(raw_core))
    per_record, per_message, states = matching.match(
        old_core, old_aux, raw_core, raw_aux, list(zip(ops["event_id"], ops["message_time"], ops["identity_status"])))
    event_index = {e: i for i, e in enumerate(ops["event_id"])}
    matched = np.array([event_index[d["event_id"]] if d.get("event_id") else -1 for d in per_record], dtype=np.int64)
    linked = matched >= 0

    # 2. clock checks on every message (receipt order = log order of stage 2)
    reported = clock.to_us(np.array(ops["message_time"], dtype="datetime64[us]")) if m else np.array([], np.int64)
    logged = _log_us(ops["log_time"]) if m else np.array([], np.int64)
    content = [(tuple(s), tuple(c)) for s, c in zip(ops["qualified_signals"], ops["qualified_conflicts"])]
    identity_ok = np.array([i in matching.GOOD_IDENTITY for i in ops["identity_status"]], dtype=bool)
    chk = clock.checks(reported, logged, content, identity_ok, ctx["max_lead_s"], ctx["max_lag_s"])
    msg_status = _clock_status(chk)
    legacy_ok = clock.legacy_chain_ok(reported, logged, ~chk["v4"])

    # 3. candidate pools: earlier definition (reported time in window; regression only) and v3 (receipt time)
    no_core = np.array([s == "no_old_core_candidate" for s in per_message], dtype=bool)
    quality = _quality_ok(ops) if m else np.array([], bool)
    in_window = lambda t: (t != NAT) & (t >= ctx["start_us"]) & (t < ctx["end_us"])
    legacy_absent = no_core & in_window(reported)
    legacy_pool, legacy_repeated = _pool(legacy_absent & quality, reported, content)
    core_absent = no_core & in_window(logged)
    pool, repeated = _pool(core_absent & quality, reported, content)
    added = np.flatnonzero(pool)

    # 4. device time of stored records and additions; collisions on the device-time axis
    stored_status = np.full(n, "no_unique_match", dtype=object)
    stored_status[linked] = msg_status[matched[linked]]
    stored_device = np.full(n, NAT, dtype=np.int64)
    ok = stored_status == VERIFIED
    stored_device[ok] = reported[matched[ok]]
    added_status = msg_status[added].copy()
    added_device = np.where(added_status == VERIFIED, reported[added], NAT).astype(np.int64)
    collide = clock.null_collisions(np.concatenate([stored_device, added_device]))
    stored_status[collide[:n]], added_status[collide[n:]] = COLLISION, COLLISION
    stored_device[collide[:n]], added_device[collide[n:]] = NAT, NAT
    stored_received = np.full(n, NAT, dtype=np.int64)
    stored_received[linked] = logged[matched[linked]]
    # additions published: the whole pool, or only those with a verified device time
    publish = (added_status == VERIFIED) if ctx.get("additions_require_verified", False) \
        else np.ones(len(added), dtype=bool)

    # 5. receipt-second timeline and same-time variants over stored records and published additions
    stored_us = clock.to_us(stored["time"].to_numpy(zero_copy_only=False))
    added_time = (logged[added] // 1_000_000) * 1_000_000
    counts = Counter(stored_us.tolist()) + Counter(added_time[publish].tolist())
    stored_variants = np.array([counts[t] for t in stored_us.tolist()], dtype=np.int32)
    added_variants = np.array([counts[t] for t in added_time.tolist()], dtype=np.int32)
    old_variants = stored["same_time_variants"].to_numpy()
    stored_seconds = set((stored_us // 1_000_000).tolist())

    # 6. stored same-time groups: which become ordered by the new clocks
    group_rows = np.flatnonzero(old_variants > 1)
    groups = dict(same_time_groups=int(len(np.unique(stored_us[group_rows]))), same_time_rows=int(len(group_rows)),
                  rows_with_received_ms=int((stored_received[group_rows] != NAT).sum()),
                  rows_with_device_time=int((stored_device[group_rows] != NAT).sum()),
                  ordered_by_received_ms=_ordered(stored_us[group_rows], stored_received[group_rows]),
                  ordered_by_device_time=_ordered(stored_us[group_rows], stored_device[group_rows]))

    # 7. disposition of every message
    disposition = np.full(m, None, dtype=object)
    for i in range(m):
        if per_message[i] == "unique_content_candidate":
            disposition[i] = "linked_to_stored_record"
        elif pool[i]:
            disposition[i] = "added"
        elif core_absent[i]:
            disposition[i] = "not_candidate:quality" if not quality[i] else "not_candidate:repeated_content"
        elif no_core[i]:
            disposition[i] = "not_candidate:outside_window"
        else:
            disposition[i] = "not_candidate:" + per_message[i]

    # 8. odometer: against the stored receipt timeline, and in receipt order within the log stream
    km_stored = stored["TotKM"].to_numpy(zero_copy_only=False).astype(float)
    ref_t, ref_km = odometer.reference(stored_us, km_stored)
    km_log = np.array([np.nan if v is None else v for v in ops["TotKM"]], dtype=float)
    km_added = km_log[added]
    vs = stored_device != NAT
    va = added_device != NAT
    timeline = {"stored_baseline": _odo(ref_t, ref_km, stored_us, km_stored),
                "stored_at_device_time": _odo(ref_t, ref_km, stored_device[vs], km_stored[vs]),
                "added_at_receipt_time": _odo(ref_t, ref_km, logged[added], km_added),
                "added_at_device_time": _odo(ref_t, ref_km, added_device[va], km_added[va])}
    for reason in sorted(set(added_status.tolist()) - {VERIFIED}):
        sel = (added_status == reason) & (reported[added] != NAT)
        timeline[f"added_{reason}_at_reported_time"] = _odo(ref_t, ref_km, reported[added][sel], km_added[sel])
    # verified records against each other on the device clock alone (no mixing of the two clocks)
    dev_t, dev_km = odometer.reference(np.concatenate([stored_device[vs], added_device[va]]),
                                       np.concatenate([km_stored[vs], km_added[va]]))
    device_axis = {"stored": _odo(dev_t, dev_km, stored_device[vs], km_stored[vs]),
                   "added": _odo(dev_t, dev_km, added_device[va], km_added[va])}
    sequence = odometer.sequence_check(km_log)
    receipt = {"all_messages": odometer.summary(sequence),
               "linked": odometer.summary(sequence[np.flatnonzero(disposition == "linked_to_stored_record")])}
    for status in sorted(set(added_status.tolist())):
        receipt[f"added:{status}"] = odometer.summary(sequence[added[added_status == status]])

    # 9. sensitivity of the clock checks (before the collision step) and the earlier rule
    sensitivity = {}
    for lead in ctx["grid_lead"]:
        for lag in ctx["grid_lag"]:
            c = clock.checks(reported, logged, content, identity_ok, lead, lag)
            sel = added[c["valid"][added]]
            sensitivity[f"lead={lead}s,lag={lag}s"] = dict(
                linked_verified=int(c["valid"][matched[linked]].sum()), added_verified=int(len(sel)),
                odometer_at_device_time=odometer.summary(odometer.check(ref_t, ref_km, reported[sel], km_log[sel])),
                odometer_receipt_order=odometer.summary(sequence[sel]))
    earlier = {}
    for label, members in (("v3_pool", pool), ("earlier_pool", legacy_pool)):
        sel = np.flatnonzero(members & legacy_ok)
        earlier[label] = dict(pool=int(members.sum()), accepted=int(len(sel)),
                              odometer_at_reported_time=odometer.summary(
                                  odometer.check(ref_t, ref_km, reported[sel], km_log[sel])),
                              odometer_receipt_order=odometer.summary(sequence[sel]))

    # 10. outputs
    operation = stored.set_column(stored.schema.get_field_index("same_time_variants"), "same_time_variants",
                                  pa.array(stored_variants)) \
        .append_column("match_status", pa.array([d["status"] for d in per_record], pa.string())) \
        .append_column("match_event_id", pa.array([d.get("event_id") for d in per_record], pa.string())) \
        .append_column("time_received_ms", _ts(_ms(stored_received), "ms")) \
        .append_column("time_device", _ts(stored_device)) \
        .append_column("time_device_status", pa.array(stored_status.tolist(), pa.string()))
    _write(out / "operation" / f"{device}.parquet", operation)

    released_added = added[publish]
    released_time, released_device, released_status = added_time[publish], added_device[publish], added_status[publish]
    released_variants, released_lag, released_logged = added_variants[publish], chk["lag_s"][added][publish], logged[added][publish]

    take = pa.array(released_added, pa.int64())
    source = pa.table({k: ops[k] for k in PROVENANCE + list(fields)}) if m else \
        pa.table({k: pa.array([], pa.string()) for k in PROVENANCE})
    additions = pa.table({"time": _ts(released_time), "time_received_ms": _ts(_ms(released_logged), "ms"),
                          "time_device": _ts(released_device),
                          "time_device_status": pa.array(released_status.tolist(), pa.string()),
                          "lag_s": pa.array(released_lag, pa.float64()),
                          "same_time_variants": pa.array(released_variants, pa.int32())})
    for name in source.column_names:
        additions = additions.append_column(name, source[name].take(take))
    _write(out / "additions" / f"{device}.parquet", additions)

    log_events = pa.table({"event_id": pa.array(ops["event_id"], pa.string()), "time_reported": _ts(reported),
                           "time_log": _ts(logged), "lag_s": pa.array(chk["lag_s"], pa.float64()),
                           "match_state": pa.array(per_message, pa.string()),
                           **{k: pa.array(chk[k], pa.bool_()) for k in ("v1", "v2", "v3", "v4", "identity", "valid")},
                           "clock_status": pa.array(msg_status.tolist(), pa.string()),
                           "quality_ok": pa.array(quality, pa.bool_()), "in_pool": pa.array(pool, pa.bool_()),
                           "in_earlier_pool": pa.array(legacy_pool, pa.bool_()),
                           "earlier_rule_approx": pa.array(legacy_ok, pa.bool_()),
                           "receipt_order_odometer": pa.array(sequence, pa.float64()),
                           "disposition": pa.array(disposition.tolist(), pa.string())})
    _write(out / "log_events" / f"{device}.parquet", log_events)

    gps = _gps(device, ctx, out)

    # 11. record (matching and candidates mirror the earlier receipts for exact regression)
    status_counts = Counter(d["status"] for d in per_record)
    added_seconds = (added_time // 1_000_000).tolist()
    return dict(
        matching=dict(**status_counts, old_rows=n, unique_hash_candidates=int(linked.sum()),
                      raw_core_present_rows=m - states.get("missing_raw_core", 0),
                      raw_missing_core_rows=states.get("missing_raw_core", 0), raw_source_rows=m,
                      **{"raw:" + k: v for k, v in states.items()}),
        candidates=dict(core_absent_envelopes=int(legacy_absent.sum()), addition_candidates=int(legacy_pool.sum()),
                        quality_flagged=int((legacy_absent & ~quality).sum()), repeated_point_envelopes=legacy_repeated,
                        unique_provenance=states.get("unique_content_candidate", 0)),
        stored=dict(rows=n, linked=int(linked.sum()), time_received_ms=int((stored_received != NAT).sum()),
                    time_device=int(vs.sum()), time_device_status=dict(Counter(stored_status.tolist())),
                    device_time_differs_from_time=int((vs & (stored_device != stored_us)).sum()),
                    same_time_variants_changed=int((stored_variants != old_variants).sum())),
        groups=groups,
        additions=dict(core_absent=int(core_absent.sum()), quality_flagged=int((core_absent & ~quality).sum()),
                       repeated_content=repeated, rows=int(len(added)), time_device=int(va.sum()),
                       time_device_status=dict(Counter(added_status.tolist())),
                       in_earlier_pool=int((pool & legacy_pool).sum()),
                       earlier_pool_only=int((legacy_pool & ~pool).sum()),
                       same_time_variant_rows=int((added_variants > 1).sum()),
                       sharing_stored_second=int(sum(s in stored_seconds for s in added_seconds)),
                       rows_released=int(len(released_added))),
        disposition=dict(Counter(disposition.tolist())),
        clock=dict(messages=m, status=dict(Counter(msg_status.tolist())), collisions=int(collide.sum())),
        odometer=dict(stored_timeline=timeline, device_axis=device_axis, receipt_order=receipt),
        sensitivity=sensitivity, earlier_rule=earlier,
        rows=dict(operation=n, additions=int(len(added)), additions_released=int(len(released_added)), log_events=m,
                  gps=gps["stored_rows"], gps_additions=gps["published"], gps_log_events=gps["log_fixes"]),
        gps=gps,
        sha256={k: sha256_file(out / k / f"{device}.parquet")
                for k in ("operation", "additions", "log_events", "gps", "gps_additions", "gps_log_events")})


def _upper(values) -> list:
    return [(v or "").strip().upper() for v in values]


def _gps(device: str, ctx: dict, out: Path) -> dict:
    """Correspond log GPS fixes with stored GPS and add verified fixes missing from it."""
    l2, dec = Path(ctx["l2"]), ctx["gps_decimals"]
    stored = pq.read_table(Path(ctx["l1"]) / "gps" / f"{device}.parquet")
    n = stored.num_rows
    st_t = clock.to_us(stored["time"].to_numpy(zero_copy_only=False))
    st_lat_raw = stored["LAT"].to_numpy(zero_copy_only=False).astype(float)
    st_lon_raw = stored["LON"].to_numpy(zero_copy_only=False).astype(float)
    st_ns, st_ew = _upper(stored["LATD"].to_pylist()), _upper(stored["LOND"].to_pylist())
    stored_index = {}
    for r, key in enumerate(zip(st_t.tolist(), np.round(st_lat_raw, dec).tolist(), st_ns,
                                np.round(st_lon_raw, dec).tolist(), st_ew)):
        stored_index.setdefault(key, r)

    path = l2 / "gps" / f"{device}.parquet"
    log = pq.read_table(path, columns=GPS_LOG_COLUMNS).to_pydict() if path.exists() else {c: [] for c in GPS_LOG_COLUMNS}
    m = len(log["event_id"])
    reported = clock.to_us(np.array(log["device_time"], dtype="datetime64[us]")) if m else np.array([], np.int64)
    logged = _log_us(log["log_time"]) if m else np.array([], np.int64)
    lat_raw = np.array([np.nan if v is None else v for v in log["latitude_raw"]], dtype=float)
    lon_raw = np.array([np.nan if v is None else v for v in log["longitude_raw"]], dtype=float)
    ns, ew = _upper(log["latitude_hemisphere"]), _upper(log["longitude_hemisphere"])
    content = list(zip(np.round(lat_raw, dec).tolist(), ns, np.round(lon_raw, dec).tolist(), ew))
    messages = l2 / "messages" / f"{device}.parquet"
    identity = dict(zip(*pq.read_table(messages, columns=["event_id", "identity_status"]).to_pydict().values())) \
        if m and messages.exists() else {}
    identity_ok = np.array([identity.get(e) in matching.GOOD_IDENTITY for e in log["event_id"]], dtype=bool)

    # correspondence: same device time and coordinates; a stored fix keeps its earliest receipt
    linked = np.zeros(m, dtype=bool)
    received = np.full(n, NAT, dtype=np.int64)
    match_event = np.full(n, None, dtype=object)
    redelivered = 0
    for i, (t, c) in enumerate(zip(reported.tolist(), content)):
        r = stored_index.get((t, *c))
        if r is None:
            continue
        linked[i] = True
        if received[r] == NAT:
            received[r], match_event[r] = logged[i], log["event_id"][i]
        else:
            redelivered += 1

    # candidates, device-clock checks within the GPS stream, publication
    chk = clock.checks(reported, logged, content, identity_ok, ctx["max_lead_s"], ctx["max_lag_s"])
    status = _clock_status(chk)
    fix_ok = ((gps_scale.classify(lat_raw, lon_raw) == gps_scale.DMM_VALID)
              & np.isin(np.array(ns, dtype=object), ["N", "S"]) & np.isin(np.array(ew, dtype=object), ["E", "W"])
              & (reported != NAT)) if m else np.array([], bool)
    in_window = (logged != NAT) & (logged >= ctx["start_us"]) & (logged < ctx["end_us"])
    stored_times = set(st_t.tolist())
    occupied = np.array([t in stored_times for t in reported.tolist()], dtype=bool)
    candidate = ~linked & in_window & fix_ok
    same_time = candidate & occupied
    pool, repeated = _pool(candidate & ~same_time, reported, content)
    publish = pool & chk["valid"]
    disposition = np.full(m, None, dtype=object)
    for i in range(m):
        disposition[i] = ("linked_to_stored_fix" if linked[i] else "not_candidate:outside_window" if not in_window[i]
                          else "not_candidate:invalid_fix" if not fix_ok[i]
                          else "pending_same_time_different_coords" if same_time[i]
                          else "added" if publish[i] else "pending_" + status[i] if pool[i]
                          else "not_candidate:repeated_fix")

    # implied-speed plausibility (km/h) against the stored track and within the log stream
    gap, vmax = ctx["gps_max_gap"], ctx["gps_max_speed"]
    st_lat = motion.degrees(st_lat_raw, st_ns, "S", 90)
    st_lon = motion.degrees(st_lon_raw, st_ew, "W", 180)
    lat, lon = motion.degrees(lat_raw, ns, "S", 90), motion.degrees(lon_raw, ew, "W", 180)
    # reference track: stored fixes with a unique timestamp (same-time variants would make the
    # neighbour choice arbitrary)
    single = stored["same_time_variants"].to_numpy() == 1
    ref = motion.track(st_t[single], st_lat[single], st_lon[single])
    usable = chk["valid"] & fix_ok
    stream = motion.track(reported[usable], lat[usable], lon[usable])
    in_stream = np.full(m, np.nan)
    in_stream[usable] = motion.check(*stream, reported[usable], lat[usable], lon[usable], gap, vmax)
    speed = dict(stored_baseline=odometer.summary(motion.check(*ref, st_t[single], st_lat[single], st_lon[single],
                                                               gap, vmax)),
                 log_stream_linked=odometer.summary(in_stream[linked & usable]),
                 log_stream_added=odometer.summary(in_stream[publish]),
                 added_vs_stored_track=odometer.summary(
                     motion.check(*ref, reported[publish], lat[publish], lon[publish], gap, vmax)))
    for label in sorted({d for d in disposition.tolist() if d and d.startswith("pending_")}):
        sel = disposition == label
        speed[f"{label}_at_reported_time_vs_stored_track"] = odometer.summary(
            motion.check(*ref, reported[sel], lat[sel], lon[sel], gap, vmax))

    # outputs
    gps_table = stored.append_column("match_status", pa.array(np.where(received != NAT, "linked", "no_log_fix").tolist(),
                                                              pa.string())) \
        .append_column("match_event_id", pa.array(match_event.tolist(), pa.string())) \
        .append_column("time_received_ms", _ts(_ms(received), "ms"))
    _write(out / "gps" / f"{device}.parquet", gps_table)
    idx = np.flatnonzero(publish)
    counts = Counter(st_t.tolist()) + Counter(reported[idx].tolist())
    take = pa.array(idx, pa.int64())
    source = pa.table({k: log[k] for k in GPS_LOG_COLUMNS}) if m else None
    additions = pa.table({
        "time": _ts(reported[idx]), "time_received_ms": _ts(_ms(logged[idx]), "ms"),
        "LAT": pa.array(lat_raw[idx]), "LATD": pa.array([log["latitude_hemisphere"][i] for i in idx], pa.string()),
        "LON": pa.array(lon_raw[idx]), "LOND": pa.array([log["longitude_hemisphere"][i] for i in idx], pa.string()),
        "time_device_status": pa.array([VERIFIED] * len(idx), pa.string()),
        "lag_s": pa.array(chk["lag_s"][idx], pa.float64()),
        "same_time_variants": pa.array([counts[t] for t in reported[idx].tolist()], pa.int32()),
        **({k: source[k].take(take) for k in ("event_id", "source_index", "byte_offset", "end_offset", "log_time",
                                              "result_raw")} if m else {}),
        "identity_status": pa.array([identity.get(log["event_id"][i]) for i in idx], pa.string())})
    _write(out / "gps_additions" / f"{device}.parquet", additions)
    _write(out / "gps_log_events" / f"{device}.parquet", pa.table({
        "event_id": pa.array(log["event_id"], pa.string()), "time_reported": _ts(reported), "time_log": _ts(logged),
        "lag_s": pa.array(chk["lag_s"], pa.float64()),
        **{k: pa.array(chk[k], pa.bool_()) for k in ("v1", "v2", "v3", "v4", "identity", "valid")},
        "clock_status": pa.array(status.tolist(), pa.string()), "fix_ok": pa.array(fix_ok, pa.bool_()),
        "linked": pa.array(linked, pa.bool_()), "in_pool": pa.array(pool, pa.bool_()),
        "speed_in_log_stream": pa.array(in_stream, pa.float64()),
        "disposition": pa.array(disposition.tolist(), pa.string())}))
    return dict(stored_rows=n, stored_linked=int((received != NAT).sum()), log_fixes=m,
                log_in_window=int(in_window.sum()), log_linked=int(linked.sum()), redelivered=redelivered,
                invalid_fix=int((~linked & in_window & ~fix_ok).sum()), candidates=int(candidate.sum()),
                pending_same_time_different_coords=int(same_time.sum()), repeated_fix=repeated,
                pool=int(pool.sum()), published=int(len(idx)),
                pool_clock_status=dict(Counter(status[pool].tolist())), disposition=dict(Counter(disposition.tolist())),
                speed=speed)


def _write(path: Path, table: pa.Table) -> None:
    with atomic_path(path) as tmp:
        pq.write_table(table, tmp, compression="zstd")

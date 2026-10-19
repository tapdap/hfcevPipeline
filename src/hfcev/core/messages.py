"""Turn one parsed log message into table rows: envelope, GPS fix or operation record.

`real_batch` payloads carry CAN frames in flat integer arrays:
    data  : 10 values per frame (prefix, CAN ID, 8 bytes), sharing the payload `time`
    fault : 11 values per frame (frame time, prefix, CAN ID, 8 bytes)
Frames are decoded with the DBC. The operation record of a message collects the
signals of its `data` frames (a) under unambiguous qualified names and (b) under the
operation-stream field names they correspond to; a signal seen twice with different
values becomes null and is listed as a conflict. Nothing is filled across messages.
"""
from __future__ import annotations

import json
import math
from collections import Counter

from .dbc import qualified_name
from .logs import parse_time

FRAME_WIDTH = {"data": 10, "fault": 11}


def field_alias(name: str) -> str:
    """Match DBC signal names to operation fields ignoring case and underscores."""
    return name.replace("_", "").lower()


def payload_time_text(payload: dict) -> str:
    value = payload.get("time")
    return value if isinstance(value, str) else json.dumps(value)


def _number(value) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def _set(values: dict, conflicts: set, key: str, value: float) -> None:
    if key in conflicts:
        return
    if key not in values:
        values[key] = value
    elif values[key] != value:
        values[key] = None
        conflicts.add(key)


class MessageDecoder:
    """Decode messages with one DBC into rows for the operation, GPS and reject tables."""

    def __init__(self, dbc: dict, fields: list[str]):
        self.dbc = dbc
        self.fields = fields
        self.lookup = {field_alias(f): f for f in fields}
        self.counts = Counter()

    def _clock(self, kind: str, device_time, log_time) -> None:
        self.counts["rows:" + kind] += 1
        if device_time is None or log_time is None:
            return
        delta = (log_time - device_time).total_seconds()
        self.counts["clock_delta_count:" + kind] += 1
        for label, hit in (("negative", delta < 0), ("abs_gt_60s", abs(delta) > 60),
                           ("abs_gt_1h", abs(delta) > 3600), ("abs_gt_1d", abs(delta) > 86400)):
            if hit:
                self.counts[f"clock_delta_{label}:{kind}"] += 1

    def decode(self, payload: dict, base: dict, identity_status: str):
        """Return (parse_status, gps_row | None, operation_row | None, reject_rows)."""
        kind = payload.get("type")
        log_time = parse_time(base["log_time"])
        rejects = []

        def reject(reason, array=None, index=None):
            rejects.append(dict(base, array_name=array, frame_index=index, reason=reason))

        gps = operation = None
        if kind == "real_batch":
            operation = self._operation(payload, base, identity_status, log_time, reject)
        elif kind == "real_gps":
            values = payload.get("data")
            if (isinstance(values, list) and len(values) == 4 and _number(values[0]) and isinstance(values[1], str)
                    and _number(values[2]) and isinstance(values[3], str)):
                raw_time = payload.get("time")
                device_time = parse_time(raw_time)
                gps = dict(base, device_time_raw=raw_time if isinstance(raw_time, str) else (json.dumps(raw_time) if raw_time is not None else None),
                           device_time=device_time,
                           server_minus_device_s=(log_time - device_time).total_seconds() if device_time and log_time else None,
                           latitude_raw=float(values[0]), latitude_hemisphere=values[1],
                           longitude_raw=float(values[2]), longitude_hemisphere=values[3],
                           result_raw=json.dumps(payload.get("result")))
                self._clock("gps", device_time, log_time)
            else:
                reject("unsupported_gps_shape", "data")
        status = "partial" if rejects else ("parsed" if kind in ("real_batch", "real_gps") else "auxiliary_preserved")
        return status, gps, operation, rejects

    def _operation(self, payload, base, identity_status, log_time, reject) -> dict:
        native, native_conflicts, legacy, legacy_conflicts = {}, set(), {}, set()
        frames = Counter()
        for array, width in FRAME_WIDTH.items():
            if array not in payload:
                if array == "data":
                    reject("missing_data", array)
                continue
            values = payload[array]
            if not isinstance(values, list) or len(values) % width:
                reject("unsupported_frame_array", array)
                continue
            for start in range(0, len(values), width):
                index = start // width
                frame = ([payload.get("time")] + values[start:start + width]) if array == "data" else values[start:start + width]
                if (type(frame[1]) is not int or type(frame[2]) is not int or not 0 <= frame[2] <= 0x1FFFFFFF
                        or not all(type(x) is int and 0 <= x <= 255 for x in frame[3:])):
                    reject("invalid_frame_fields", array, index)
                    continue
                frames[array] += 1
                self._clock("can_frames", parse_time(frame[0]), log_time)
                message = self.dbc.get(frame[2])
                if message is None:
                    status = "unknown_can_id"
                elif message.length != len(frame) - 3:
                    status = "dbc_length_mismatch"
                else:
                    status = "decoded"
                self.counts["frames:" + status] += 1
                if status != "decoded":
                    frames["unknown"] += 1
                    continue
                if array != "data":
                    continue
                for name, value in message.decode(bytes(frame[3:])).items():
                    _set(native, native_conflicts, qualified_name(frame[2], message.name, name), value)
                    target = self.lookup.get(field_alias(name))
                    if target:
                        _set(legacy, legacy_conflicts, target, value)
        return dict(base, message_time_raw=payload_time_text(payload), message_time=parse_time(payload.get("time")),
                    identity_status=identity_status, normal_frames=frames["data"], fault_frames=frames["fault"],
                    unknown_frames=frames["unknown"], qualified_signals=sorted(native.items()),
                    qualified_conflicts=sorted(native_conflicts), legacy_conflicts=sorted(legacy_conflicts),
                    **{f: legacy.get(f) for f in self.fields})

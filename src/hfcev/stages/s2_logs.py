"""Stage 2 — decode the raw receive logs into per-device message, operation and GPS tables.

Methods: "Receive-log decoding".

Pass 1 (prepare, parallel over chunks of log files): every log file is SHA-checked, split into
blocks (`core.logs`) and each message decoded (`core.messages`) into
    messages   one row per message: envelope, identity, both clocks, full payload JSON
    operation  one row per `real_batch` message: decoded signals mapped to operation fields
    gps        one row per `real_gps` message: raw coordinates and hemispheres
    rejects    one row per block, frame or identity problem that could not be decoded
Pass 2 (process, per device): the device's rows are gathered from all days and
written in log order (source file, byte offset).

No record is removed, deduplicated, re-timed or restricted to the study window here.
Server-log time and payload time are both kept; their difference is a wall-clock gap,
not a measured latency. `event_id` (source SHA-256, path, byte offset) identifies each
message for later stages.

Outputs in L2_logs/: {messages,operation,gps}/<device>.parquet, rejects.parquet,
_pass1_summary.json; pass-1 fragments in _pass1/<day>_<chunk>/.
"""
from __future__ import annotations

import hashlib
import json
import multiprocessing
import shutil
import sys
import time
from collections import Counter
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path

import pyarrow as pa
import pyarrow.compute as pc
import pyarrow.dataset as ds
import pyarrow.parquet as pq

from .. import io
from ..core import dbc as dbc_module
from ..core import logs, messages
from ..core.dbc import read_dbc
from ..core.logs import UNASSIGNED, event_id, identity, log_blocks, parse_time
from ..core.messages import MessageDecoder, payload_time_text
from ..io import atomic_path, read_json, sha256_file, write_json
from ..runner import code_hash, init_worker, peak_rss_mb
from . import s1_harmonize

NAME = "s2"
SECTION = "s2_logs"
PARAM_SECTIONS = ("s1_harmonize", SECTION)  # operation field names come from stage 1
CODE = (logs, messages, dbc_module, io)
LAYER = "L2_logs"
KINDS = ("messages", "operation", "gps")

_POSITION = [("event_id", pa.string()), ("tbox", pa.string()), ("log_time", pa.string()), ("log_month", pa.string()),
             ("source_index", pa.int32()), ("byte_offset", pa.int64()), ("end_offset", pa.int64())]
MESSAGES = pa.schema(_POSITION + [
    ("message_type", pa.string()), ("identity_status", pa.string()), ("topic_sn", pa.string()), ("topic", pa.string()),
    ("payload_time", pa.string()), ("device_time", pa.timestamp("us")), ("payload_json", pa.string()),
    ("parse_status", pa.string())])
GPS = pa.schema(_POSITION + [
    ("device_time_raw", pa.string()), ("device_time", pa.timestamp("us")), ("server_minus_device_s", pa.float64()),
    ("latitude_raw", pa.float64()), ("latitude_hemisphere", pa.string()), ("longitude_raw", pa.float64()),
    ("longitude_hemisphere", pa.string()), ("result_raw", pa.string())])
REJECTS = pa.schema(_POSITION + [("array_name", pa.string()), ("frame_index", pa.int32()), ("reason", pa.string())])
LOG_ORDER = [("source_index", "ascending"), ("byte_offset", "ascending")]
# Pass-1 task size: consecutive files of one day. Bounds worker memory (~0.7 GB); does not affect outputs.
FILES_PER_TASK = 12


def operation_schema(fields: list[str]) -> pa.Schema:
    return pa.schema(_POSITION + [
        ("message_time_raw", pa.string()), ("message_time", pa.timestamp("us")), ("identity_status", pa.string()),
        ("normal_frames", pa.int32()), ("fault_frames", pa.int32()), ("unknown_frames", pa.int32()),
        ("qualified_signals", pa.map_(pa.string(), pa.float64())), ("qualified_conflicts", pa.list_(pa.string())),
        ("legacy_conflicts", pa.list_(pa.string()))] + [(f, pa.float64()) for f in fields])


# ---------------------------------------------------------------- pass 1: decode log files

def _read_verified(root: Path, item: dict, max_bytes: int) -> bytes:
    path = root / item["relative_path"]
    if path.stat().st_size > max_bytes:
        raise ValueError(f"{item['relative_path']} exceeds {max_bytes} bytes")
    raw = path.read_bytes()
    if len(raw) != item["bytes"] or hashlib.sha256(raw).hexdigest() != item["sha256"]:
        raise ValueError(f"Log file changed: {item['relative_path']}")
    return raw


def decode_file(raw: bytes, item: dict, decoder: MessageDecoder) -> tuple[dict[str, list], Counter]:
    """Rows of every table for one log file, plus block/message accounting."""
    rows = {"messages": [], "operation": [], "gps": [], "rejects": []}
    counts = Counter()
    for block in log_blocks(raw):
        counts["log_blocks:" + block["kind"]] += 1
        if block["kind"] in ("topic", "other"):
            continue
        log_time = block["log_time"]
        base = dict(event_id=event_id(item["sha256"], item["relative_path"], block["offset"]), tbox=UNASSIGNED,
                    log_time=log_time, log_month=log_time[:7] if log_time else "unknown",
                    source_index=item["source_index"], byte_offset=block["offset"], end_offset=block["end_offset"])
        if block["kind"] != "message":
            rows["rejects"].append(dict(base, array_name=None, frame_index=None, reason=block["reason"]))
            continue
        payload = block["payload"]
        device, status, topic_sn = identity(payload, block["topic"])
        base["tbox"] = device
        counts["identity:" + status] += 1
        message_type = str(payload.get("type", "missing"))
        counts["type:" + message_type] += 1
        parse_status, gps, operation, rejects = decoder.decode(payload, base, status)
        rows["rejects"].extend(rejects)
        if gps:
            rows["gps"].append(gps)
        if operation:
            rows["operation"].append(operation)
        rows["messages"].append(dict(
            base, message_type=message_type, identity_status=status, topic_sn=topic_sn, topic=block["topic"],
            payload_time=payload_time_text(payload), device_time=parse_time(payload.get("time")),
            payload_json=json.dumps(payload, ensure_ascii=False, separators=(",", ":"), allow_nan=False),
            parse_status=parse_status))
        if status in ("invalid_or_missing_sn", "topic_sn_mismatch"):
            rows["rejects"].append(dict(base, array_name=None, frame_index=None, reason=status))
    return rows, counts


def _pass1_task(task: dict) -> dict:
    """Decode a chunk of one day's log files into fragments sorted by device, then log order."""
    out, start = Path(task["out"]), time.perf_counter()
    if sha256_file(task["dbc"]) != task["dbc_sha256"]:
        raise ValueError("DBC changed")
    decoder = MessageDecoder(read_dbc(task["dbc"]), task["fields"])
    schemas = dict(messages=MESSAGES, operation=operation_schema(task["fields"]), gps=GPS, rejects=REJECTS)
    tables = {k: [] for k in schemas}
    counts = Counter()
    root = Path(task["root"])
    for item in task["files"]:
        raw = _read_verified(root, item, task["max_bytes"])
        rows, file_counts = decode_file(raw, item, decoder)
        counts.update(file_counts)
        counts["input_files"] += 1
        counts["input_bytes"] += len(raw)
        for kind, schema in schemas.items():
            if rows[kind]:
                tables[kind].append(pa.Table.from_pylist(rows[kind], schema=schema))
    counts.update(decoder.counts)
    tmp = out.with_name(out.name + ".tmp")
    shutil.rmtree(tmp, ignore_errors=True)
    tmp.mkdir(parents=True)
    devices = Counter()
    for kind, schema in schemas.items():
        table = pa.concat_tables(tables[kind]) if tables[kind] else schema.empty_table()
        table = table.sort_by([("tbox", "ascending")] + LOG_ORDER)
        pq.write_table(table, tmp / f"{kind}.parquet", compression="zstd", row_group_size=16384)
        counts["rows:" + kind] = len(table)
        if kind == "messages":
            devices.update({r["values"]: r["counts"] for r in pc.value_counts(table["tbox"]).to_pylist()})
    receipt = dict(key=task["key"], fingerprint=task["fingerprint"], files=[f["relative_path"] for f in task["files"]],
                   counts=dict(counts), devices=dict(devices),
                   seconds=round(time.perf_counter() - start, 2), peak_rss_mb=peak_rss_mb())
    write_json(tmp / "receipt.json", receipt)
    shutil.rmtree(out, ignore_errors=True)
    tmp.rename(out)
    return receipt


def _select(manifest: list[dict], limit: int | None) -> list[dict]:
    """All files, or `limit` files evenly spaced over the natural order (calibration runs)."""
    items = [dict(item, source_index=i) for i, item in enumerate(manifest)]
    if not limit or limit >= len(items):
        return items
    return [items[i * (len(items) - 1) // max(1, limit - 1)] for i in range(limit)]


def prepare(cfg, devices, force=False, workers=1, options=None) -> dict:
    params = cfg.release[SECTION]
    options = options or {}
    log = options.get("log", lambda **event: None)
    root = cfg.layer("L0_logs")
    selected = _select(read_json(root / "_manifest.json"), options.get("files"))
    dbc_path = cfg.file("dbc")
    if sha256_file(dbc_path) != params["dbc_sha256"]:
        raise ValueError("DBC does not match dbc_sha256 in the release configuration")
    read_dbc(dbc_path)  # fail early on unsupported DBC content
    fields = s1_harmonize.operation_fields(cfg)
    out = cfg.layer(LAYER)
    pass1 = out / "_pass1"

    days, seen = {}, Counter()  # task key "<day>_<chunk>" sorts in log order
    for item in selected:  # natural file order
        day = item["relative_path"].split("/")[2]
        days.setdefault(f"{day}_{seen[day] // FILES_PER_TASK:02d}", []).append(item)
        seen[day] += 1
    base_fp = {"params": cfg.params_hash(*PARAM_SECTIONS), "code": code_hash(sys.modules[__name__])}
    tasks, receipts = [], []
    for day, files in sorted(days.items()):
        fp = dict(base_fp, files=hashlib.sha256(json.dumps([[f["relative_path"], f["sha256"]] for f in files]).encode()).hexdigest()[:16])
        receipt = read_json(pass1 / day / "receipt.json")
        if not force and receipt and receipt["fingerprint"] == fp:
            receipts.append(receipt)
            continue
        tasks.append(dict(key=day, files=files, root=str(root), out=str(pass1 / day), dbc=str(dbc_path),
                          dbc_sha256=params["dbc_sha256"], fields=fields, max_bytes=params["max_file_bytes"],
                          fingerprint=fp))
    # Largest days first so the tail of the run is short.
    tasks.sort(key=lambda t: -sum(f["bytes"] for f in t["files"]))
    if tasks:
        with ProcessPoolExecutor(max_workers=workers, initializer=init_worker, max_tasks_per_child=1,
                                 mp_context=multiprocessing.get_context("spawn")) as pool:
            futures = [pool.submit(_pass1_task, t) for t in tasks]
            for done, future in enumerate(as_completed(futures), 1):
                receipts.append(future.result())
                log(event="pass1", task=receipts[-1]["key"], done=done, todo=len(tasks),
                    seconds=receipts[-1]["seconds"], peak_rss_mb=receipts[-1]["peak_rss_mb"])

    wanted = {d for d in days}
    receipts = sorted((r for r in receipts if r["key"] in wanted), key=lambda r: r["key"])
    totals, device_set = Counter(), set()
    for r in receipts:
        totals.update(r["counts"])
        device_set.update(r["devices"])
    if totals["input_files"] != len(selected) or totals["input_bytes"] != sum(i["bytes"] for i in selected):
        raise AssertionError("Not every selected log file was decoded exactly once")
    if totals["rows:messages"] != totals["log_blocks:message"]:
        raise AssertionError("Message rows differ from message blocks")
    fragments = {kind: [str(pass1 / r["key"] / f"{kind}.parquet") for r in receipts] for kind in (*KINDS, "rejects")}
    with atomic_path(out / "rejects.parquet") as tmp:
        pq.write_table(ds.dataset(fragments["rejects"], format="parquet").to_table().sort_by(LOG_ORDER), tmp,
                       compression="zstd")
    inputs = hashlib.sha256(json.dumps([[i["relative_path"], i["sha256"]] for i in selected]).encode()).hexdigest()[:16]
    write_json(out / "_pass1_summary.json", dict(files=len(selected), tasks=[r["key"] for r in receipts], inputs=inputs,
                                                 devices=len(device_set), counts=dict(sorted(totals.items()))))
    return dict(devices=sorted(device_set), inputs=inputs, out=str(out), fragments=fragments, fields=fields,
                rejects=str(out / "rejects.parquet"))


# ---------------------------------------------------------------- pass 2: gather per device

def _gather(fragments: list[str], device: str, path: Path) -> int:
    """Append the device's rows from each day fragment in day order.

    Fragments are sorted by device then log order, and days follow the natural file
    order, so the concatenation is already in log order; this is asserted, not assumed.
    """
    rows, last = 0, (-1, -1)
    with atomic_path(path) as tmp:
        writer = io.ChunkWriter(tmp)
        for fragment in fragments:
            part = pq.read_table(fragment, filters=[("tbox", "=", device)])
            if not len(part):
                continue
            first = (part["source_index"][0].as_py(), part["byte_offset"][0].as_py())
            if first <= last:
                raise AssertionError(f"{path.name}: fragments are not in log order")
            last = (part["source_index"][-1].as_py(), part["byte_offset"][-1].as_py())
            writer.write(part)
            rows += len(part)
        if not writer.close():
            pq.write_table(pq.read_schema(fragments[0]).empty_table(), tmp, compression="zstd")
    if not rows:
        path.unlink()
    return rows


def process(device: str, ctx: dict) -> dict:
    out = Path(ctx["out"])
    record = {}
    for kind in KINDS:
        path = out / kind / f"{device}.parquet"
        rows = _gather(ctx["fragments"][kind], device, path)
        record[kind] = dict(rows=rows, sha256=sha256_file(path)) if rows else dict(rows=0)
    record["rejects"] = dict(rows=pq.read_table(ctx["rejects"], columns=["tbox"], filters=[("tbox", "=", device)]).num_rows)
    msgs = pq.read_table(out / "messages" / f"{device}.parquet", columns=["message_type", "identity_status"])
    record["messages"]["types"] = {r["values"]: r["counts"] for r in pc.value_counts(msgs["message_type"]).to_pylist()}
    record["messages"]["identity"] = {r["values"]: r["counts"] for r in pc.value_counts(msgs["identity_status"]).to_pylist()}
    if record["operation"]["rows"]:
        op = pq.read_table(out / "operation" / f"{device}.parquet",
                           columns=["normal_frames", "fault_frames", "unknown_frames", "legacy_conflicts", "qualified_conflicts"])
        record["operation"].update(
            normal_frames=pc.sum(op["normal_frames"]).as_py(), fault_frames=pc.sum(op["fault_frames"]).as_py(),
            unknown_frames=pc.sum(op["unknown_frames"]).as_py(),
            events_with_legacy_conflicts=pc.sum(pc.greater(pc.list_value_length(op["legacy_conflicts"]), 0)).as_py(),
            events_with_qualified_conflicts=pc.sum(pc.greater(pc.list_value_length(op["qualified_conflicts"]), 0)).as_py())
    return record

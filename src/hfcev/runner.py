"""Run a stage device by device: parallel, resumable, and with a log per run.

A stage module provides
    NAME, LAYER
    PARAM_SECTIONS   release-config blocks the stage depends on
    CODE             modules whose source defines the stage output (besides the stage itself)
    prepare(cfg, devices, force, workers, options) -> ctx
                     once per run in the parent; may run its own parallel pre-pass.
                     ctx["devices"] (optional) replaces the cohort as default device list;
                     ctx["inputs"] (optional) fingerprints the inputs selected for this run.
    process(device, ctx) -> record           per device, in a worker process
    finalize(cfg, ctx, records, log)         optional; once per run after all devices succeeded,
                                             with the manifest records of every device
A device is skipped when its manifest entry was produced with the same parameters,
code and inputs; `force=True` recomputes it.
"""
from __future__ import annotations

import hashlib
import json
import multiprocessing
import os
import resource
import sys
import time
import traceback
from concurrent.futures import ProcessPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path

from .io import Manifest, write_json

PACKAGE = Path(__file__).resolve().parent


def code_hash(stage) -> str:
    """Fingerprint of the source files that define a stage's output."""
    files = {Path(m.__file__).resolve() for m in (stage, *stage.CODE)}
    h = hashlib.sha256()
    for path in sorted(files):
        h.update(str(path.relative_to(PACKAGE)).encode() + b"\0" + path.read_bytes())
    return h.hexdigest()[:16]


def fingerprint(stage, cfg, ctx=None) -> dict:
    fp = {"params": cfg.params_hash(*stage.PARAM_SECTIONS), "code": code_hash(stage)}
    if ctx and "inputs" in ctx:
        fp["inputs"] = ctx["inputs"]
    return fp


def init_worker() -> None:
    # One device per process; keep numerical libraries single-threaded.
    for var in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS"):
        os.environ[var] = "1"
    import pyarrow as pa
    pa.set_cpu_count(1)
    pa.set_io_thread_count(1)


def peak_rss_mb() -> float:
    peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return round(peak / (1 << 20) if sys.platform == "darwin" else peak / 1024, 1)


def _new_run_dir(parent: Path, name: str) -> Path:
    """Create a fresh run directory; a numeric suffix separates runs started in the same second."""
    parent.mkdir(parents=True, exist_ok=True)
    for n in range(1000):
        path = parent / (name if n == 0 else f"{name}_{n}")
        try:
            path.mkdir()
            return path
        except FileExistsError:
            continue
    raise RuntimeError(f"Too many runs named {name}")


def _run_one(process, device, ctx):
    start = time.perf_counter()
    record = process(device, ctx)
    record["seconds"] = round(time.perf_counter() - start, 2)
    record["peak_rss_mb"] = peak_rss_mb()
    return record


def run_stage(stage, cfg, devices=None, workers=None, force=False, options=None, log=print) -> dict:
    requested = sorted(devices) if devices else None
    workers = workers or cfg.workers
    out = cfg.layer(stage.LAYER)
    out.mkdir(parents=True, exist_ok=True)
    manifest = Manifest(out / "_manifest.json", stage.NAME)

    started = datetime.now()
    run_dir = _new_run_dir(cfg.work / "runs", f"{started:%Y%m%d-%H%M%S}_{stage.NAME}")
    events = (run_dir / "log.jsonl").open("w")

    def emit(**event):
        event["t"] = round(time.perf_counter() - clock, 2)
        events.write(json.dumps(event) + "\n")
        events.flush()
        log(json.dumps(event))

    clock = time.perf_counter()
    options = dict(options or {}, log=lambda **event: emit(**event))
    ctx = stage.prepare(cfg, requested or list(cfg.cohort), force=force, workers=workers, options=options)
    devices = requested or sorted(ctx.get("devices", cfg.cohort))
    fp = fingerprint(stage, cfg, ctx)
    todo = [d for d in devices if force or not manifest.is_current(d, fp)]
    emit(event="start", stage=stage.NAME, devices=len(devices), todo=len(todo), workers=workers, **fp)

    failed = {}
    # `spawn` gives every device a fresh process, so peak memory is measured per device.
    pool = ProcessPoolExecutor(max_workers=workers, initializer=init_worker, max_tasks_per_child=1,
                               mp_context=multiprocessing.get_context("spawn"))
    with pool:
        futures = {pool.submit(_run_one, stage.process, d, ctx): d for d in todo}
        for future in as_completed(futures):
            device = futures[future]
            try:
                record = future.result()
            except Exception:
                failed[device] = traceback.format_exc()
                emit(event="failed", device=device, error=failed[device].strip().splitlines()[-1])
                continue
            record["fingerprint"] = fp
            manifest.devices[device] = record
            manifest.save()
            emit(event="done", device=device, seconds=record["seconds"], peak_rss_mb=record["peak_rss_mb"])

    if not failed and hasattr(stage, "finalize"):
        emit(event="finalize")
        stage.finalize(cfg, ctx, {d: manifest.devices[d] for d in devices}, log=lambda msg: emit(event="note", message=msg))

    summary = dict(stage=stage.NAME, started=started.isoformat(timespec="seconds"),
                   elapsed_seconds=round(time.perf_counter() - clock, 1), workers=workers,
                   devices=len(devices), processed=len(todo) - len(failed), skipped=len(devices) - len(todo),
                   failed=sorted(failed), fingerprint=fp, run_dir=str(run_dir))
    if failed:
        write_json(run_dir / "failed.json", failed)
    write_json(run_dir / "summary.json", summary)
    emit(event="end", processed=summary["processed"], skipped=summary["skipped"], failed=len(failed))
    events.close()
    return summary

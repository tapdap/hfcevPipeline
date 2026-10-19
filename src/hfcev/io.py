"""File helpers: hashing, atomic writes and per-stage manifests."""
from __future__ import annotations

import hashlib
import json
import os
from contextlib import contextmanager
from pathlib import Path

import pyarrow.parquet as pq

PARQUET_OPTIONS = dict(compression="zstd", use_dictionary=True)
ROW_GROUP = 65536


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with Path(path).open("rb") as f:
        for block in iter(lambda: f.read(4 << 20), b""):
            h.update(block)
    return h.hexdigest()


@contextmanager
def atomic_path(path: Path):
    """Yield a temporary sibling path; move it onto `path` only if the block succeeds."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.unlink(missing_ok=True)
    try:
        yield tmp
        os.replace(tmp, path)
    finally:
        tmp.unlink(missing_ok=True)


class ChunkWriter:
    """Parquet writer opened lazily on the first non-empty table; one row group per 65,536 rows."""

    def __init__(self, path: Path):
        self.path, self.writer = Path(path), None

    def write(self, table) -> None:
        if not len(table):
            return
        if self.writer is None:
            self.writer = pq.ParquetWriter(self.path, table.schema, **PARQUET_OPTIONS)
        self.writer.write_table(table, row_group_size=ROW_GROUP)

    def close(self) -> bool:
        """Close and report whether anything was written."""
        if self.writer is None:
            return False
        self.writer.close()
        return True


def read_json(path: Path, default=None):
    path = Path(path)
    return json.loads(path.read_text()) if path.exists() else default


def write_json(path: Path, value) -> None:
    with atomic_path(path) as tmp:
        tmp.write_text(json.dumps(value, indent=1, ensure_ascii=False, allow_nan=False) + "\n")


class Manifest:
    """Per-stage record of finished devices, stored as `<layer>/_manifest.json`."""

    def __init__(self, path: Path, stage: str):
        self.path = Path(path)
        data = read_json(self.path, {"stage": stage, "devices": {}})
        if data["stage"] != stage:
            raise ValueError(f"{self.path} belongs to stage {data['stage']!r}, not {stage!r}")
        self.data = data

    @property
    def devices(self) -> dict:
        return self.data["devices"]

    def is_current(self, device: str, fingerprint: dict) -> bool:
        return self.devices.get(device, {}).get("fingerprint") == fingerprint

    def save(self) -> None:
        self.data["devices"] = dict(sorted(self.devices.items()))
        write_json(self.path, self.data)

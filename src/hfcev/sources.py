"""Inventory of the L0 raw export: the dump manifest plus per-file fingerprints.

L0 layout (one Parquet file per source table):
    _manifest.json          list of {path, table, rows, bytes, status, ...}
    _fingerprint.json       {path: {bytes, rows, sha256}}
    can/z_<device>[_partN].parquet       per-device operation (CAN) tables
    can_legacy/<table>.parquet           legacy operation table covering all devices
    gps/z_gps_<DEVICE>[_partN].parquet   per-device GPS tables
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import pyarrow.parquet as pq

from .io import sha256_file


def device_of(relative: str) -> str:
    """Device ID implied by a per-device table name, e.g. can/z_qlkj..._part2.parquet."""
    stem = Path(relative).stem.lower()
    return stem.removeprefix("z_gps_").removeprefix("z_").split("_part")[0].upper()


class L0Inventory:
    def __init__(self, root: Path):
        self.root = Path(root)
        self.manifest = json.loads((self.root / "_manifest.json").read_text())
        self.fingerprints = json.loads((self.root / "_fingerprint.json").read_text())

    def records(self, stream: str) -> list[dict]:
        """Successfully dumped tables of a stream, in dump-manifest order."""
        return [r for r in self.manifest if r["path"].startswith(stream + "/") and r["status"] == "ok"]

    def record(self, relative: str) -> dict:
        return next(r for r in self.manifest if r["path"] == relative)

    def device_records(self, stream: str, device: str) -> list[dict]:
        return sorted((r for r in self.records(stream) if device_of(r["path"]) == device), key=lambda r: r["path"])

    def first_schema(self, stream: str):
        """Reference schema of a stream: the first dumped table, as in the original export order."""
        return pq.ParquetFile(self.root / self.records(stream)[0]["path"]).schema_arrow

    def verified_file(self, record: dict) -> pq.ParquetFile:
        """Open a source table after checking size, SHA-256 and row count against the fingerprints."""
        path = self.root / record["path"]
        expected = self.fingerprints[record["path"]]
        if path.stat().st_size != expected["bytes"] or sha256_file(path) != expected["sha256"]:
            raise ValueError(f"L0 fingerprint mismatch: {record['path']}")
        pf = pq.ParquetFile(path)
        if not pf.metadata.num_rows == expected["rows"] == record["rows"]:
            raise ValueError(f"L0 row count mismatch: {record['path']}")
        return pf


def natural_key(value: str):
    """Sort key treating digit runs as numbers (info.2025-03-17-18.10.log after ...18.9.log)."""
    return [int(x) if x.isdigit() else x for x in re.split(r"(\d+)", value)]


def _hash_entry(args):
    root, relative = args
    path = Path(root) / relative
    return dict(relative_path=relative, bytes=path.stat().st_size, sha256=sha256_file(path))


def build_file_inventory(root: Path, workers: int = 1, expected: list[dict] | None = None) -> list[dict]:
    """Hash every regular file under `root` (natural path order).

    With `expected` (same record layout), any missing, extra or differing file raises.
    Writes nothing; the caller decides where the inventory goes.
    """
    from concurrent.futures import ProcessPoolExecutor

    root = Path(root)
    files = sorted((p.relative_to(root).as_posix() for p in root.rglob("*")
                    if p.is_file() and not p.is_symlink() and not p.name.startswith(("_", "."))), key=natural_key)
    with ProcessPoolExecutor(max_workers=workers) as pool:
        records = list(pool.map(_hash_entry, [(str(root), f) for f in files], chunksize=8))
    if expected is not None:
        want = {e["relative_path"]: (e["bytes"], e["sha256"]) for e in expected}
        got = {r["relative_path"]: (r["bytes"], r["sha256"]) for r in records}
        if want != got:
            missing, extra = sorted(set(want) - set(got)), sorted(set(got) - set(want))
            differ = sorted(k for k in set(want) & set(got) if want[k] != got[k])
            raise ValueError(f"Inventory differs: missing={missing[:3]} extra={extra[:3]} differ={differ[:3]} "
                             f"({len(missing)}/{len(extra)}/{len(differ)})")
    return records

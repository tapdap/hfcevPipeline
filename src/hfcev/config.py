"""Load the release parameters (versioned) and the local machine settings (private)."""
from __future__ import annotations

import csv
import hashlib
import json
import tomllib
from dataclasses import dataclass
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
DEFAULT_RELEASE = REPO / "configs" / "release_v1.toml"
DEFAULT_LOCAL = REPO / "configs" / "local.toml"


@dataclass(frozen=True)
class Config:
    release: dict
    work: Path
    cohort: tuple[str, ...]
    workers: int
    files: dict  # extra machine-specific inputs (e.g. a private DBC), paths relative to `work`

    def layer(self, name: str) -> Path:
        return self.work / name

    def file(self, key: str) -> Path:
        if key not in self.files:
            raise KeyError(f"local.toml has no [files] entry {key!r}")
        return self.work / self.files[key]

    def stage_params(self, *sections: str) -> dict:
        """Parameters a stage may depend on: the shared dataset block plus the named blocks."""
        return {"dataset": self.release["dataset"], **{s: self.release[s] for s in sections}}

    def params_hash(self, *sections: str) -> str:
        blob = json.dumps(self.stage_params(*sections), sort_keys=True).encode()
        return hashlib.sha256(blob).hexdigest()[:16]


def read_cohort(path: Path) -> tuple[str, ...]:
    with path.open(encoding="utf-8-sig", newline="") as f:
        devices = [row["device_id"].strip() for row in csv.DictReader(f)]
    if not devices or len(devices) != len(set(devices)):
        raise ValueError(f"Cohort file is empty or has duplicate devices: {path}")
    return tuple(sorted(devices))


def load(release: Path | None = None, local: Path | None = None) -> Config:
    release = Path(release or DEFAULT_RELEASE)
    local = Path(local or DEFAULT_LOCAL)
    if not local.exists():
        raise FileNotFoundError(f"{local} not found; copy configs/local.example.toml and edit it")
    params = tomllib.loads(release.read_text())
    machine = tomllib.loads(local.read_text())
    work = (local.parent / machine["work"]).resolve()
    workers = int(machine.get("workers", 1))
    if workers < 1:
        raise ValueError("workers must be >= 1")
    return Config(release=params, work=work, cohort=read_cohort(work / machine["cohort"]), workers=workers,
                  files=dict(machine.get("files", {})))

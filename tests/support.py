"""Shared test fixtures: a temporary work directory wired to the synthetic sample."""
from __future__ import annotations

import os
import re
import shutil
from pathlib import Path

from hfcev import config

REPO = Path(__file__).resolve().parents[1]
SAMPLE = REPO / "sample_data"


def sample_release(path: Path) -> Path:
    """Release parameters with the DBC fingerprint of the synthetic protocol."""
    sha = (SAMPLE / "protocol" / "sample.dbc.sha256").read_text().strip()
    text = re.sub(r'(?m)^dbc_sha256 = ".*"$', f'dbc_sha256 = "{sha}"', config.DEFAULT_RELEASE.read_text())
    path.write_text(text)
    return path


def sample_config(tmp: Path, workers: int = 2) -> config.Config:
    tmp = Path(tmp)
    work = tmp / "work"
    work.mkdir(parents=True)
    for name in ("L0_raw", "L0_logs", "protocol", "vehicle_map.csv"):
        (work / name).symlink_to(SAMPLE / name)
    shutil.copy(SAMPLE / "cohort.csv", work / "cohort.csv")
    local = tmp / "local.toml"
    local.write_text(f'work = "{work}"\ncohort = "cohort.csv"\nworkers = {workers}\n\n'
                     '[files]\ndbc = "protocol/sample.dbc"\nvehicle_map = "vehicle_map.csv"\n')
    # Worker processes are spawned and must be able to import the package.
    src = str(REPO / "src")
    if src not in os.environ.get("PYTHONPATH", "").split(os.pathsep):
        os.environ["PYTHONPATH"] = os.pathsep.join(p for p in (src, os.environ.get("PYTHONPATH")) if p)
    return config.load(release=sample_release(tmp / "release.toml"), local=local)

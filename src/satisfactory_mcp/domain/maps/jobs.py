"""Generation jobs on disk, and how far one has got, read from the lines its generator prints.

One JSON file per job under ``data/local/maps/jobs/``, rewritten atomically on every state
change, beside the job's log. The progress regexes read lines written for humans; a test
pins them against a recorded log, so rewording one of those lines fails there first.
docs/maps_contract.md §5.
"""

from __future__ import annotations

import json
import math
import re
import time
from pathlib import Path

from ...core import atomic
from . import registry

__all__ = [
    "ACTIVE",
    "FINAL",
    "Progress",
    "jobs_dir",
    "load_all",
    "log_path",
    "log_tail",
    "new_id",
    "save",
]

ACTIVE = ("queued", "running")
FINAL = ("done", "failed", "cancelled", "interrupted")
LOG_TAIL_LINES = 200
RECENT_KEEP = 10

#: An ETA is shown only past this fraction, so the first minute does not read "4 h left".
ETA_FROM = 0.03


def jobs_dir() -> Path:
    return registry.maps_dir() / registry.JOBS_DIR_NAME


def log_path(job_id: str) -> Path:
    return jobs_dir() / f"{job_id}.log"


def new_id() -> str:
    stem = "j" + time.strftime("%Y%m%d-%H%M%S")
    ident, n = stem, 2
    while (jobs_dir() / f"{ident}.json").exists():
        ident, n = f"{stem}-{n}", n + 1
    return ident


def save(job: dict) -> None:
    jobs_dir().mkdir(parents=True, exist_ok=True)
    atomic.write_text(jobs_dir() / f"{job['id']}.json", json.dumps(job, ensure_ascii=False))


def load_all() -> list[dict]:
    """Every job on disk, oldest first."""
    found = []
    try:
        paths = sorted(jobs_dir().glob("*.json"))
    except OSError:
        return []
    for path in paths:
        try:
            job = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if isinstance(job, dict) and isinstance(job.get("id"), str):
            found.append(job)
    found.sort(key=lambda job: (job.get("created") or 0, job["id"]))
    return found


def log_tail(job_id: str, lines: int = LOG_TAIL_LINES) -> list[str]:
    try:
        text = log_path(job_id).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []
    return text.splitlines()[-lines:]


SWEEP = re.compile(r"(\d+)/(\d+) packages")
MESHES = re.compile(r"^\s*\d+ rock meshes, ")
DIRECT = re.compile(r"direct\.cache: ([\d.]+)% of")
DIRECT_REUSED = re.compile(r"reusing the direct raster")
TOP_START = re.compile(r"rasterising the arches")
TOP = re.compile(r"top\.cache: ([\d.]+)%")
DRAW_START = re.compile(r"^drawing (terrain|satellite) at")
DRAW = re.compile(r"^\s*(terrain|satellite): ([\d.]+)% of")
CUT = re.compile(r"pyramid z(\d+):")
WROTE = re.compile(r"^wrote .*[\\/](terrain|satellite)\s")
DONE = re.compile(r"^done in")


def cut_lines(size: int) -> int:
    """How many ``pyramid zN`` lines one layer prints: the 1x tree, then the @2x tree."""
    one = int(math.log2(max(size, 256) // 256)) + 1
    two = int(math.log2(max(min(size, 16384), 512) // 512)) + 1
    return one + two


class Progress:
    """Stage and fraction of one job, fed its log a line at a time."""

    def __init__(self, stages: dict[str, float], size: int | None = None) -> None:
        self.order = list(stages)
        self.weights = stages
        self.size = size or 0
        self.stage = self.order[0] if self.order else ""
        self.fraction = 0.0
        self.layer = ""
        self.cuts = 0
        self.finished = False

    def _enter(self, stage: str, fraction: float = 0.0) -> None:
        if stage not in self.weights:
            return
        if self.order.index(stage) < self.order.index(self.stage):
            return
        self.stage = stage
        self.fraction = min(max(fraction, 0.0), 1.0)

    def feed(self, line: str) -> None:
        if (m := SWEEP.search(line)) and int(m.group(2)):
            self._enter("sweep", int(m.group(1)) / int(m.group(2)))
        elif MESHES.search(line):
            self._enter("sweep", 1.0)
        elif m := DIRECT.search(line):
            self._enter("direct", float(m.group(1)) / 100)
        elif DIRECT_REUSED.search(line):
            self._enter("direct", 1.0)
        elif TOP_START.search(line):
            self._enter("top")
        elif m := TOP.search(line):
            self._enter("top", float(m.group(1)) / 100)
        elif m := DRAW_START.search(line):
            self.layer, self.cuts = m.group(1), 0
            self._enter(f"draw:{self.layer}")
        elif m := DRAW.search(line):
            self._enter(f"draw:{m.group(1)}", float(m.group(2)) / 100)
        elif CUT.search(line) and self.layer:
            self.cuts += 1
            self._enter(f"cut:{self.layer}", self.cuts / max(1, cut_lines(self.size)))
        elif m := WROTE.search(line):
            self._enter(f"cut:{m.group(1)}", 1.0)
        elif DONE.search(line):
            self.finished = True
            if self.order:
                self._enter(self.order[-1], 1.0)

    def pct(self) -> float | None:
        """Overall fraction 0..1, or ``None`` for a job whose lines this cannot read."""
        if self.order == ["run"]:
            return 1.0 if self.finished else None
        total = sum(self.weights.values()) or 1.0
        before = sum(self.weights[s] for s in self.order[: self.order.index(self.stage)])
        return min(1.0, (before + self.weights[self.stage] * self.fraction) / total)

    def eta(self, elapsed: float) -> float | None:
        pct = self.pct()
        if pct is None or pct < ETA_FROM or pct >= 1.0:
            return None
        return elapsed / pct * (1 - pct)

    def stage_words(self) -> str:
        names = {"prep": "reading the field", "sweep": "reading the rock meshes",
                 "direct": "direct raster", "top": "arches and boulders", "run": "running"}  # fmt: skip
        kind, _, layer = self.stage.partition(":")
        if kind == "draw":
            return f"drawing {layer}"
        if kind == "cut":
            return f"cutting {layer} tiles"
        return names.get(self.stage, self.stage)

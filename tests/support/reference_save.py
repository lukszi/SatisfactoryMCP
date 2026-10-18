"""The one save the tests read through the server's own save discovery, never the newest.

docs/DEVELOPING.md ("The reference save") says where it is looked for, how to pin another and
how to refresh the frozen copy.
"""

from __future__ import annotations

import hashlib
import os
import shutil
import tempfile
from collections.abc import Callable
from pathlib import Path

import pytest

from satisfactory_mcp import config
from satisfactory_mcp.core.saveio.schema import SaveScan
from tests.support.paths import REPO_ROOT
from tests.support.reference_world import FIXTURE_SAVE
from tests.support.saves import machine_saves_root

#: A ``.sav`` path that replaces the reference save for one run.
REFERENCE_SAVE_ENV = "SATISFACTORY_TEST_SAVE"

#: The frozen copy, looked for before the machine's save folder; gitignored with ``data/local``.
FROZEN_DIR = REPO_ROOT / "data" / "local" / "test-saves" / "reference"

#: Where the pinned save is mirrored alone; a stable path keeps one parse cache entry.
MIRROR_ROOT = Path(tempfile.gettempdir()) / "satisfactory-mcp-test-saves"


def find_reference() -> Path | str:
    """The pinned save, or why there is none: the variable, the frozen copy, the save folder."""
    chosen = os.environ.get(REFERENCE_SAVE_ENV)
    if chosen:
        path = Path(chosen)
        return path if path.is_file() else f"{REFERENCE_SAVE_ENV} names {path}, not a file"
    frozen = FROZEN_DIR / FIXTURE_SAVE
    if frozen.is_file():
        return frozen
    root = machine_saves_root()
    found = sorted(root.rglob(FIXTURE_SAVE)) if root else []
    if found:
        return found[0]
    return (
        f"the reference save {FIXTURE_SAVE} is neither in {FROZEN_DIR} nor under "
        f"{root or 'a save folder'}; set {REFERENCE_SAVE_ENV} to a .sav to pin another"
    )


def _same_file(target: Path, source: os.stat_result) -> bool:
    try:
        found = target.stat()
    except OSError:
        return False
    return (found.st_size, found.st_mtime_ns) == (source.st_size, source.st_mtime_ns)


def mirror(source: Path) -> Path:
    """A folder holding ``source`` alone, so its newest save can only be the reference.

    Written through a ``.part`` file and a rename, because parallel workers mirror at once.
    """
    root = MIRROR_ROOT / hashlib.sha256(str(source.resolve()).encode()).hexdigest()[:12]
    target = root / source.name
    stat = source.stat()
    if _same_file(target, stat):
        return root
    root.mkdir(parents=True, exist_ok=True)
    part = root / f"{source.name}.{os.getpid()}.part"
    shutil.copy2(source, part)
    try:
        os.replace(part, target)
    except PermissionError:
        part.unlink(missing_ok=True)
        if not _same_file(target, stat):
            raise
    return root


class ReferencePin:
    """``config.saves_root`` for the test session: the mirror, found on first use."""

    def __init__(self) -> None:
        self._found: Path | str | None = None
        self.root: Path | None = None

    @property
    def source(self) -> Path:
        """The pinned ``.sav``; skips the test when there is none."""
        if self._found is None:
            self._found = find_reference()
        if isinstance(self._found, str):
            pytest.skip(f"needs the reference save: {self._found}")
        return self._found

    def saves_root(self) -> Path:
        if self.root is None:
            self.root = mirror(self.source)
        return self.root


def _overlaps(first: Path, second: Path) -> bool:
    one, two = first.resolve(), second.resolve()
    return one == two or one in two.parents or two in one.parents


def guarded_scan(scan: Callable[..., SaveScan]) -> Callable[..., SaveScan]:
    """``scan_saves`` that refuses the machine's save folder, whose newest save is whatever
    was played last. A whole-folder test walks it through ``machine_saves_root_or_skip``."""

    def scan_saves(root: str | Path | None = None) -> SaveScan:
        asked = Path(root) if root else config.saves_root()
        machine = machine_saves_root()
        if machine is not None and _overlaps(asked, machine):
            raise AssertionError(
                f"a test scanned the machine's save folder {asked}; tests read the pinned "
                "reference save (docs/DEVELOPING.md, 'The reference save')"
            )
        return scan(root)

    return scan_saves

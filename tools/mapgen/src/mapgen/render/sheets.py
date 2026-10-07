"""The drawn sheets of a run's layers, in memory-mapped files while the run lasts.

One pass draws every layer (``render/compose.py``), so the run holds all its sheets until
each one is cut: 3 bytes a pixel and layer, 3.2 GB a layer at full size. In files the system
can write them out rather than keep them in memory. docs/spatial-and-map.md section 40.
"""

from __future__ import annotations

import argparse
import shutil
from pathlib import Path

import numpy as np

from mapgen.cache import held_open
from mapgen.common import Refusal
from mapgen.render.light import SCRATCH_IN_USE, scratch_root
from satisfactory_mcp.core.arrays import U8Grid

__all__ = ["SHEETS_DIR_NAME", "SheetFiles", "claim_sheets"]

SHEETS_DIR_NAME = "sheets.cache"


class SheetFiles:
    """Each layer's sheet as a file in ``directory``, made when the draw asks for it.

    A ``SheetMaker`` for ``render_layers``; nothing is written until a sheet is made.
    """

    def __init__(self, directory: Path) -> None:
        self.directory = directory

    def __call__(self, layer: str, shape: tuple[int, int, int]) -> U8Grid:
        self.directory.mkdir(parents=True, exist_ok=True)
        path = self.directory / f"{layer}.npy"
        return np.lib.format.open_memmap(path, "w+", np.uint8, shape)

    def release(self, layer: str) -> None:
        """Delete a layer's file. Windows keeps a file still mapped: ``close`` takes it then."""
        try:
            (self.directory / f"{layer}.npy").unlink(missing_ok=True)
        except PermissionError:
            pass

    def close(self) -> None:
        shutil.rmtree(self.directory, ignore_errors=True)


def claim_sheets(args: argparse.Namespace, renders: Path) -> SheetFiles:
    """The run's sheet files beside the light's scratch, emptied of a run that died.

    The sheets of a render still running are mapped, and refused rather than emptied.
    """
    directory = scratch_root(args, renders) / SHEETS_DIR_NAME
    sheets = sorted(directory.glob("*.npy")) if directory.is_dir() else []
    if sheets and held_open(sheets):
        raise Refusal(
            SCRATCH_IN_USE,
            f"{directory} holds the sheets of a render still running. Wait for it to finish, "
            "or pass --scratch-dir with another directory.",
        )
    shutil.rmtree(directory, ignore_errors=True)
    return SheetFiles(directory)

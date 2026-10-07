"""The light a run keeps beside its raster caches, for a later run that draws the same surface.

``light.kept/`` holds one finished bake: the lighting pyramid's tiles (hard links to the
installed ones where the volume allows), the default-sun terms, and the bake's
``meta.json``, written last, whose ``key`` (``lighting.stage.light_key``) says what the bake
read. docs/spatial-and-map.md section 29, "Kept light".
"""

from __future__ import annotations

import json
import os
import shutil
from pathlib import Path
from typing import cast

from mapgen.cache import write_sidecar
from mapgen.lighting.stage import LIGHT_DIR_NAME
from satisfactory_mcp.core.gameassets.pyramid import (
    RETIRED_SUFFIX,
    STAGING_SUFFIX,
    TILES_DIR_NAME,
    swap_into_place,
)
from satisfactory_mcp.core.jsontypes import JsonObject

__all__ = ["KEPT_LIGHT_DIR_NAME", "KeptLight"]

KEPT_LIGHT_DIR_NAME = "light.kept"
META_NAME = "meta.json"


class KeptLight:
    """The bake kept under ``directory``: ``tiles/``, ``terms.npy`` and ``meta.json``."""

    def __init__(self, directory: Path) -> None:
        self.directory = directory
        self.terms = directory / "terms.npy"

    def matching(self, key: JsonObject) -> JsonObject | None:
        """The kept bake's ``_meta`` when it was baked under ``key``, else None."""
        meta = _read_meta(self.directory / META_NAME)
        if meta is None or not _same_key(meta, key) or not self.terms.is_file():
            return None
        return meta if (self.directory / TILES_DIR_NAME).is_dir() else None

    def install(self, meta: JsonObject, renders: Path) -> JsonObject:
        """The kept pyramid as ``renders/light/``, unless that holds this bake already; the
        ``_meta`` it is installed under."""
        root = renders / LIGHT_DIR_NAME
        installed = _read_meta(root / META_NAME)
        if installed is not None and _same_key(installed, cast(JsonObject, meta["key"])):
            return installed
        staging = root / (TILES_DIR_NAME + STAGING_SUFFIX)
        shutil.rmtree(staging, ignore_errors=True)
        _link_tree(self.directory / TILES_DIR_NAME, staging)
        how = swap_into_place(
            staging, root / TILES_DIR_NAME, root / (TILES_DIR_NAME + RETIRED_SUFFIX)
        )
        meta = {**meta, "tiles": {**cast(JsonObject, meta["tiles"]), "installed_by": how}}
        write_sidecar(root / META_NAME, {"_meta": meta})
        return meta

    def keep(self, meta: JsonObject, renders: Path, terms: Path) -> Path:
        """File the bake installed in ``renders/light/`` with its ``terms``; where the terms
        are now. A keep that fails leaves no ``meta.json`` and the run reads ``terms``."""
        try:
            self.forget()
            _link_tree(renders / LIGHT_DIR_NAME / TILES_DIR_NAME, self.directory / TILES_DIR_NAME)
            _move(terms, self.terms)
            write_sidecar(self.directory / META_NAME, {"_meta": meta})
        except OSError as exc:
            print(f"  light: not kept for a later run: {exc}", flush=True)
        return terms if terms.is_file() else self.terms

    def forget(self) -> None:
        """Remove the kept bake, its ``meta.json`` first."""
        (self.directory / META_NAME).unlink(missing_ok=True)
        if (self.directory / TILES_DIR_NAME).exists():
            shutil.rmtree(self.directory / TILES_DIR_NAME)
        self.terms.unlink(missing_ok=True)


def _read_meta(path: Path) -> JsonObject | None:
    try:
        meta = json.loads(path.read_text(encoding="utf-8")).get("_meta")
    except (OSError, ValueError, AttributeError):
        return None
    return meta if isinstance(meta, dict) else None


def _same_key(meta: JsonObject, key: JsonObject) -> bool:
    kept = meta.get("key")
    return isinstance(kept, dict) and kept.get("digest") == key.get("digest")


def _link_tree(source: Path, target: Path) -> None:
    """``target`` holding every file of ``source``: a hard link each, a copy across volumes."""
    target.mkdir(parents=True, exist_ok=True)
    for folder, _dirs, names in os.walk(source):
        here = target / Path(folder).relative_to(source)
        here.mkdir(parents=True, exist_ok=True)
        for name in names:
            try:
                os.link(Path(folder) / name, here / name)
            except OSError:
                shutil.copy2(Path(folder) / name, here / name)


def _move(source: Path, target: Path) -> None:
    """``source`` renamed to ``target``, or copied there from another volume."""
    try:
        os.replace(source, target)
    except OSError:
        shutil.move(source, target)

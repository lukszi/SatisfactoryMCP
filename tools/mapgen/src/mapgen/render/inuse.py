"""The refusal to render into a folder that holds tiles of a map type the server lists.

mapgen may not import the server's ``domain.maps``, so the manifest is read as plain JSON.
tools/mapgen/README.md, ``renders``, has the rule.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from satisfactory_mcp.core.gameassets.pyramid import TILES_DIR_NAME
from satisfactory_mcp.core.jsontypes import JsonObject

__all__ = ["IN_USE", "MANIFEST", "OVERRIDE", "add_in_use_flag", "held_types", "in_use_refusal"]

#: Exit code of a run refused because its folder holds a registered map type.
IN_USE = 10

#: The registry under ``data/local``; each type's ``dir`` is relative to ``data/local`` too.
MANIFEST = Path("maps") / "manifest.json"

OVERRIDE = "--overwrite-in-use"


def add_in_use_flag(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        OVERRIDE,
        action="store_true",
        help="write even into a folder holding tiles of a map type the server's registry lists",
    )


def _manifest(local: Path) -> JsonObject:
    try:
        data = json.loads((local / MANIFEST).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _real(path: Path) -> Path | None:
    try:
        return path.resolve()
    except OSError:
        return None


def held_types(local: Path, target: Path) -> list[str]:
    """Registered types with tiles in ``target`` or under it, junctions and links followed."""
    types = _manifest(local).get("types")
    root = _real(target) if target.is_dir() else None
    if root is None or not isinstance(types, dict):
        return []
    held: list[str] = []
    for ident, entry in types.items():
        rel = entry.get("dir") if isinstance(entry, dict) else None
        if not isinstance(rel, str) or not (local / rel / TILES_DIR_NAME).is_dir():
            continue
        where = _real(local / rel)
        if where is not None and where.is_relative_to(root):
            held.append(ident)
    return held


def in_use_refusal(local: Path, target: Path, overwrite: bool) -> str | None:
    """The message for a run into ``target`` while it holds a registered type, else ``None``."""
    held = [] if overwrite else held_types(local, target)
    if not held:
        return None
    default = _manifest(local).get("default")
    names = ", ".join(f"{i} (the default)" if i == default else i for i in held)
    real = _real(target)
    shown = f"{target} (which is {real})" if real is not None and real != target else str(target)
    return (
        f"{shown} holds tiles of map types the server's registry lists: {names}.\n"
        "This run would write over them. Pass --renders-name <new> to write beside them, "
        f"or {OVERRIDE} to replace them anyway."
    )

"""Building outlines and ports: ``data/building_shapes.json``, with the clearance fallback.

The table is cut from the installed game by ``tools/gen_building_shapes.py``; a class it does
not cover is drawn from its clearance boxes instead, each at its own offset. Coordinates are
metres in the actor's own frame. docs/building-shapes.md.
"""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Literal

from typing_extensions import TypedDict

from ..jsontypes import JsonValue
from .model import GameData

__all__ = [
    "SHAPES_FILE",
    "BuildingShape",
    "PortFlow",
    "PortKind",
    "PortRow",
    "ShapeRow",
    "ShapeSource",
    "building_shape",
    "load_shape_table",
    "parse_shape_table",
]

SHAPES_FILE = "building_shapes.json"

ShapeSource = Literal["mesh", "clearance"]
PortKind = Literal["belt", "pipe"]
PortFlow = Literal["in", "out", "any"]

_KINDS: tuple[PortKind, ...] = ("belt", "pipe")
_FLOWS: tuple[PortFlow, ...] = ("in", "out", "any")


class PortRow(TypedDict):
    """One belt or pipe connector: where it is and which way it faces (degrees, +x to +y)."""

    name: str
    kind: PortKind
    flow: PortFlow
    x_m: float
    y_m: float
    z_m: float
    yaw: float


class ShapeRow(TypedDict):
    """One class in the table: outline rings as ``[x, y]`` pairs, and its ports."""

    rings: list[list[list[float]]]
    ports: list[PortRow]


class BuildingShape(TypedDict):
    """What the map draws a class as: where the outline came from, the rings, the ports."""

    source: ShapeSource
    rings: list[list[list[float]]]
    ports: list[PortRow]


def _number(value: JsonValue) -> float | None:
    return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else None


def _rings(raw: JsonValue) -> list[list[list[float]]]:
    """Rings of at least three numeric pairs; anything torn is dropped, not guessed."""
    out: list[list[list[float]]] = []
    for ring in raw if isinstance(raw, list) else ():
        points: list[list[float]] = []
        for point in ring if isinstance(ring, list) else ():
            if isinstance(point, list) and len(point) == 2:
                x, y = _number(point[0]), _number(point[1])
                if x is not None and y is not None:
                    points.append([x, y])
        if len(points) >= 3:
            out.append(points)
    return out


def _port(raw: JsonValue) -> PortRow | None:
    if not isinstance(raw, dict):
        return None
    kind, flow = raw.get("kind"), raw.get("flow")
    numbers = [_number(raw.get(key)) for key in ("x_m", "y_m", "z_m", "yaw")]
    if kind not in _KINDS or flow not in _FLOWS:
        return None
    x, y, z, yaw = numbers
    if x is None or y is None or z is None or yaw is None:
        return None
    name = raw.get("name")
    return {
        "name": name if isinstance(name, str) else "",
        "kind": "belt" if kind == "belt" else "pipe",
        "flow": "in" if flow == "in" else ("out" if flow == "out" else "any"),
        "x_m": x,
        "y_m": y,
        "z_m": z,
        "yaw": yaw,
    }


def parse_shape_table(raw: JsonValue) -> dict[str, ShapeRow]:
    """``{class: row}`` out of the table's JSON; a torn row costs itself only."""
    classes = raw.get("classes") if isinstance(raw, dict) else None
    out: dict[str, ShapeRow] = {}
    for cls, row in (classes if isinstance(classes, dict) else {}).items():
        if not isinstance(row, dict):
            continue
        ports = row.get("ports")
        out[cls] = {
            "rings": _rings(row.get("rings")),
            "ports": [p for p in map(_port, ports if isinstance(ports, list) else []) if p],
        }
    return out


@lru_cache(maxsize=4)
def load_shape_table(data_dir: Path) -> dict[str, ShapeRow]:
    """The table under ``data_dir``, or ``{}`` when it was never generated or will not read."""
    try:
        raw: JsonValue = json.loads((data_dir / SHAPES_FILE).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return parse_shape_table(raw)


def building_shape(game: GameData, table: dict[str, ShapeRow], cls: str) -> BuildingShape | None:
    """The outline a class is drawn as: the table's, else its clearance boxes, else None."""
    row = table.get(cls)
    ports = row["ports"] if row else []
    if row and row["rings"]:
        return {"source": "mesh", "rings": row["rings"], "ports": ports}
    building = game.buildings.get(cls)
    if building is None or not building.clearance:
        return None
    rings = [[[x, y] for x, y in ring] for ring in building.clearance]
    return {"source": "clearance", "rings": rings, "ports": ports}

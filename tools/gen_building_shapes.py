"""Generate data/building_shapes.json: top-down outlines and ports of the drawn buildables.

    uv run --extra gen python tools/gen_building_shapes.py

For every machine, extractor, generator, storage box, fluid buffer, belt splitter/merger, pipe
fitting and facility class the save projection files, the class's mesh components are read
from the installed game, placed in the actor's own frame and traced from above into a few
simplified polygons: the footprint, and up to four height bands cut at the class's own
natural breaks in height; its belt and pipe connection components become ports. Method, frame
and limits: docs/building-shapes.md.
"""

from __future__ import annotations

import json
import math
import struct
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import TypedDict

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
for _path in (ROOT, ROOT / "src", ROOT / "tools" / "mapgen" / "src"):
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))

from mapgen.common import base_parser, require_gen
from mapgen.gamedata.install import GameReader, missing_container, open_game
from satisfactory_mcp.core.arrays import F64Grid, I64Grid
from satisfactory_mcp.core.gameassets import staticmesh
from satisfactory_mcp.core.gameassets.packages import PackageView, Transform
from satisfactory_mcp.core.gameassets.packages.blueprint import (
    Component,
    class_components,
    generated_class,
)
from satisfactory_mcp.core.gameassets.provenance import InstallNotFound, installed_build
from satisfactory_mcp.core.gamedata.shapes import (
    SHAPES_FILE,
    PortFlow,
    PortKind,
    PortRow,
    ShapeRow,
)
from satisfactory_mcp.core.jsontypes import JsonObject, JsonValue
from satisfactory_mcp.core.saveio.extract import registers
from tools.building_shapes.bands import BandOptions, height_bands
from tools.building_shapes.silhouette import Ring, silhouette

#: Grid cell, gap bridged between parts, and the vertex budget per outline.
CELL_CM = 10.0
CLOSE_CM = 40.0
MAX_VERTICES = 48

#: Up to four height bands above the footprint at the class's own breaks in height, each with
#: its own vertex budget; the rules each field sets: BandOptions, docs/building-shapes.md.
BAND_OPTIONS = BandOptions(
    budgets=(32, 28, 24, 20),
    min_floor=200.0,
    step=25.0,
    min_visible=0.06,
    min_width=60.0,
    min_rise=100.0,
    opening=30.0,
    min_area=20_000.0,
    max_overlap=0.9,
)

#: Where buildable classes live in the container, and the prefix every one of them carries.
BUILDABLE_DIR = "/FactoryGame/Buildable/"
CLASS_PREFIX = "Build_"
MOUNT = "../../../FactoryGame/Content/"

_HINTS = (
    registers.MANUFACTURER_HINTS
    + registers.EXTRACTOR_HINTS
    + registers.GENERATOR_HINTS
    + registers.ATTACHMENT_HINTS
    + registers.FITTING_HINTS
)
_LISTED = {
    *registers.STORAGE_CLASSES,
    *registers.FLUID_BUFFER_CLASSES,
    *registers.FACILITY_CLASSES,
    *registers.HYPERTUBE_ENTRANCE_CLASSES,
}

#: Connection component classes, by what they carry.
_BELT_PORT = "FGFactoryConnectionComponent"
_PIPE_PORTS = ("FGPipeConnectionComponent", "FGPipeConnectionFactory")
_BELT_FLOW: dict[str, PortFlow] = {
    "EFactoryConnectionDirection::FCD_OUTPUT": "out",
    "EFactoryConnectionDirection::FCD_ANY": "any",
}
_PIPE_FLOW: dict[str, PortFlow] = {
    "EPipeConnectionType::PCT_CONSUMER": "in",
    "EPipeConnectionType::PCT_PRODUCER": "out",
}
#: A connector that only snaps (a lift's floor hole, say) carries nothing.
_SNAP_ONLY = "EFactoryConnectionDirection::FCD_SNAP_ONLY"

#: The LOD array follows the socket list: ``38 + 4 * sockets`` bytes into the render tail.
_SOCKET_COUNT_AT = 34


class ClassReport(TypedDict):
    meshes: list[str]
    failed_meshes: list[str]
    triangles: int


def shape_classes(reader: GameReader) -> dict[str, str]:
    """``{class name: package path}`` for every buildable the projection files and draws."""
    found: dict[str, str] = {}
    for path in reader.store.by_path:
        if not path.endswith(".uasset") or BUILDABLE_DIR not in path:
            continue
        leaf = path.rsplit("/", 1)[-1].removesuffix(".uasset")
        cls = leaf + "_C"
        if not leaf.startswith(CLASS_PREFIX):
            continue
        if cls in _LISTED or any(hint in cls for hint in _HINTS):
            found[cls] = "/Game/" + path.removeprefix(MOUNT).removesuffix(".uasset")
    return dict(sorted(found.items()))


def _lod0(reader: GameReader, mesh: str) -> tuple[F64Grid, I64Grid] | None:
    """LOD 0 vertices (cm) and triangles; retried at the socket-derived LOD offset."""
    row = staticmesh.extract(reader.store, reader.scripts, reader.index, mesh)
    verts, idx = row.get("verts"), row.get("idx")
    if row.get("ok") and verts is not None and idx is not None:
        return verts.astype(np.float64), idx.astype(np.int64)
    package = reader.index.path_for(mesh)
    if not package:
        return None
    view = PackageView(reader.store.read_path(package), reader.scripts)
    export = staticmesh.static_mesh_export(view)
    bounds = staticmesh.extended_bounds(view, export) if export is not None else None
    if export is None or bounds is None:
        return None
    tail = staticmesh.render_tail(view, export)
    sockets = struct.unpack_from("<I", tail, _SOCKET_COUNT_AT)[0]
    try:
        parsed = staticmesh.parse_render_data(tail, 38 + 4 * sockets)
    except (staticmesh.ParseError, struct.error):
        return None
    got = staticmesh.lod0_buffers(tail, parsed)
    if got is None:
        return None
    low, high = bounds
    pad = staticmesh.bounds_pad(low, high)
    if staticmesh.inside_fraction(got[0], low, high, pad) < staticmesh.BOUNDS_INSIDE_MIN:
        return None
    return got[0].astype(np.float64), got[1]


def _matrix(transform: Transform) -> tuple[F64Grid, F64Grid]:
    """A transform as ``(3x3 rotation-times-scale, translation)``."""
    location, (x, y, z, w), scale = transform
    rotation = np.array(
        [
            [1 - 2 * (y * y + z * z), 2 * (x * y - z * w), 2 * (x * z + y * w)],
            [2 * (x * y + z * w), 1 - 2 * (x * x + z * z), 2 * (y * z - x * w)],
            [2 * (x * z - y * w), 2 * (y * z + x * w), 1 - 2 * (x * x + y * y)],
        ]
    )
    return rotation * np.asarray(scale)[None, :], np.asarray(location)


class MeshCache:
    """LOD 0 per mesh path, read once: the Mk2 and Mk3 miners share one mesh."""

    def __init__(self, reader: GameReader) -> None:
        self.reader = reader
        self._cache: dict[str, tuple[F64Grid, I64Grid] | None] = {}

    def get(self, mesh: str) -> tuple[F64Grid, I64Grid] | None:
        if mesh not in self._cache:
            try:
                self._cache[mesh] = _lod0(self.reader, mesh)
            except Exception:
                self._cache[mesh] = None
        return self._cache[mesh]


def placed_triangles(
    meshes: MeshCache, components: list[Component], report: ClassReport
) -> F64Grid:
    """Every mesh component's triangles in the actor frame, ``(n, 3, 3)`` cm."""
    parts: list[F64Grid] = []
    for component in components:
        mesh = component.ref("StaticMesh")
        if not mesh:
            continue
        got = meshes.get(mesh)
        if got is None:
            report["failed_meshes"].append(mesh)
            continue
        report["meshes"].append(mesh)
        verts, tris = got
        rotation, offset = _matrix(component.transform)
        placed = verts @ rotation.T + offset
        parts.append(placed[tris])
        report["triangles"] += len(tris)
    return np.concatenate(parts) if parts else np.zeros((0, 3, 3))


def _port(component: Component) -> PortRow | None:
    kind: PortKind
    flow: PortFlow
    if component.cls == _BELT_PORT:
        direction = component.fname("mDirection")
        if direction == _SNAP_ONLY:
            return None
        kind, flow = "belt", _BELT_FLOW.get(direction or "", "in")
    elif component.cls in _PIPE_PORTS:
        kind, flow = "pipe", _PIPE_FLOW.get(component.fname("mPipeConnectionType") or "", "any")
    else:
        return None
    rotation, (x, y, z) = _matrix(component.transform)
    facing = math.degrees(math.atan2(rotation[1, 0], rotation[0, 0]))
    return {
        "name": component.name,
        "kind": kind,
        "flow": flow,
        "x_m": round(x / 100.0, 2),
        "y_m": round(y / 100.0, 2),
        "z_m": round(z / 100.0, 2),
        "yaw": round(facing, 1),
    }


def shape_row(meshes: MeshCache, components: list[Component]) -> tuple[ShapeRow, ClassReport]:
    """One class's outline rings (metres, actor frame) and ports."""
    report: ClassReport = {"meshes": [], "failed_meshes": [], "triangles": 0}
    triangles = placed_triangles(meshes, components, report)
    rings = silhouette(triangles[:, :, :2], CELL_CM, CLOSE_CM, MAX_VERTICES)
    bands = height_bands(triangles, CELL_CM, CLOSE_CM, BAND_OPTIONS)
    ports = [port for port in map(_port, components) if port is not None]
    row: ShapeRow = {
        "rings": _metres(rings),
        "bands": [
            {"tier": band.tier, "z_m": round(band.floor / 100.0, 1), "rings": _metres(band.rings)}
            for band in bands
        ],
        "ports": sorted(ports, key=lambda port: port["name"]),
    }
    return row, report


def _metres(rings: list[Ring]) -> list[list[list[float]]]:
    return [[[round(x / 100.0, 1), round(y / 100.0, 1)] for x, y in ring] for ring in rings]


def build_meta(
    build_pin: str, versions: dict[str, str], reports: dict[str, ClassReport]
) -> JsonObject:
    failed: JsonObject = {}
    for cls, report in reports.items():
        if report["failed_meshes"]:
            unread: list[JsonValue] = list(report["failed_meshes"])
            failed[cls] = unread
    return {
        "description": (
            "Top-down outlines, height bands and belt/pipe ports of the buildables the map "
            "draws, in metres "
            "in each class's own actor frame (+x forward, +y right, the save's axes at yaw 0). "
            "docs/building-shapes.md."
        ),
        "licence": (
            "First-party measurements of the game's own assets, read from the reader's "
            "installed copy by tools/gen_building_shapes.py: simplified outlines and connector "
            "positions, not meshes."
        ),
        "source": {
            "container": "FactoryGame-Windows.utoc/.ucas",
            "method": (
                f"LOD 0 of every mesh component, placed by the construction-script tree, "
                f"rasterised at {CELL_CM:g} cm, closed over {CLOSE_CM:g} cm, hole-filled, "
                f"traced and simplified to at most {MAX_VERTICES} vertices per ring; bands cut "
                f"the same way from the highest point over each cell, opened over "
                f"{BAND_OPTIONS.opening:g} cm first, at natural breaks in each class's own "
                f"heights"
            ),
            "decoder": dict(versions),
        },
        "game_version_pinned": build_pin,
        "generated": datetime.now(UTC).date().isoformat(),
        "count": len(reports),
        "meshes_unread": failed,
    }


def table_text(meta: JsonObject, rows: dict[str, ShapeRow]) -> str:
    """The table as JSON with one line per class: diffable by class, not a line per number."""
    lines = [
        f"  {json.dumps(cls)}: {json.dumps(row, separators=(',', ':'))}"
        for cls, row in rows.items()
    ]
    head = json.dumps(meta, indent=1).replace("\n", "\n ")
    return '{\n "_meta": ' + head + ',\n "classes": {\n' + ",\n".join(lines) + "\n }\n}\n"


def main() -> int:
    parser = base_parser("Generate data/building_shapes.json from the installed game.")
    parser.add_argument("--out", type=Path, default=ROOT / "data" / SHAPES_FILE)
    args = parser.parse_args()
    versions = require_gen("ooz")
    try:
        build_pin, _raw = installed_build(args.game)
    except InstallNotFound as exc:
        print(f"{exc}\nPass --game if the install is somewhere else.")
        return 1
    why = missing_container(args.game)
    if why:
        print(why)
        return 1
    started = time.time()
    reader = open_game(args.game)
    meshes = MeshCache(reader)
    rows: dict[str, ShapeRow] = {}
    reports: dict[str, ClassReport] = {}
    for leaf_cls, package in shape_classes(reader).items():
        cls = generated_class(reader.store, reader.scripts, reader.index, package) or leaf_cls
        components = class_components(reader.store, reader.scripts, reader.index, package)
        if components is None:
            print(f"  {cls}: package unreadable, skipped")
            continue
        rows[cls], reports[cls] = shape_row(meshes, components)
        ring_sizes = [len(ring) for ring in rows[cls]["rings"]]
        print(f"  {cls}: rings {ring_sizes}, {len(rows[cls]['ports'])} ports")
    rows = dict(sorted(rows.items()))
    meta = build_meta(build_pin, versions, reports)
    args.out.write_text(table_text(meta, rows), "utf-8")
    print(f"wrote {args.out}  {len(rows)} classes in {time.time() - started:.0f}s")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""The water actors: which classes are water, each actor's box in the world, and box tops."""

from __future__ import annotations

import math
import struct
from collections.abc import Collection, Iterable, Sequence
from typing import TypeAlias

import numpy as np

from mapgen.gamedata.frame import GRID_PX, ORIGIN_X_CM, ORIGIN_Y_CM, SPACING_CM
from mapgen.gamedata.meshes import MeshBounds, bounds_pair
from satisfactory_mcp.core.arrays import F32Grid
from satisfactory_mcp.core.gameassets.packages import (
    ClassFacts,
    PackageView,
    class_name_of,
    property_tags,
    quat_rotate,
    world_transform,
)

__all__ = [
    "WATER_BOX_COMPONENTS",
    "WATER_CLASS_PREFIXES",
    "WATER_CLASS_TOKENS",
    "WATER_PLANE_MESH",
    "WATER_SURFACE_CLASSES",
    "Corners",
    "WaterBox",
    "box_texels",
    "is_water_class",
    "water_actor_box",
    "water_box_tops",
]

#: An axis-aligned box in world cm: ``(x0, y0, z0, x1, y1, z1)``.
WaterBox: TypeAlias = tuple[float, float, float, float, float, float]
#: A box's low and high corners, each ``[x, y, z]``.
Corners: TypeAlias = tuple[list[float], list[float]]
_Triple: TypeAlias = tuple[float, float, float]
#: A box as ``(origin, extent)``, the way ``FBoxSphereBounds`` and ``ExtendedBounds`` state it.
_BoundsPair: TypeAlias = tuple[_Triple, _Triple]
_Transform: TypeAlias = tuple[_Triple, tuple[float, float, float, float], _Triple]
_IDENTITY: _Transform = ((0.0, 0.0, 0.0), (0.0, 0.0, 0.0, 1.0), (1.0, 1.0, 1.0))


#: A water actor is one whose class name carries a water word and one of the game's own
#: class prefixes. Deliberately a shape rather than a list: 849 actors on build 495413
#: across nine classes, and a build that adds a tenth should be found, not missed.
WATER_CLASS_TOKENS = ("Water", "Ocean", "Lake", "River")
WATER_CLASS_PREFIXES = ("BP_", "FG", "BPW")

#: Which of those classes are a water SURFACE, i.e. whose box top may set a level. The
#: exclusions are the argument. ``BP_WaterFallTool_02_C`` is water and is not a surface --
#: its box top is the lip of the fall, tens of metres above the pool it feeds -- and the
#: two ``BP_WaterPlane_C`` are developer backdrops carrying no transform at all.
WATER_SURFACE_CLASSES = frozenset(
    {
        "FGWaterVolume",
        "BP_Water_C",
        "BP_LakeWater_C",
        "BPW_OceanSplineTool_02_C",
        "BP_TranslucentWater_C",
        "BP_River_PROT_C",
    }
)

#: Component classes that can state a box. Order of preference is in ``_component_box``.
WATER_BOX_COMPONENTS = frozenset(
    {
        "BoxComponent",
        "BrushComponent",
        "StaticMeshComponent",
        "InstancedStaticMeshComponent",
        "HierarchicalInstancedStaticMeshComponent",
    }
)
_MESH_COMPONENTS = WATER_BOX_COMPONENTS - {"BoxComponent", "BrushComponent"}

#: The plane the water blueprints draw themselves with. Their cooked instances name no
#: ``StaticMesh`` -- the construction script assigns it -- so this asset's own
#: ``ExtendedBounds`` stands in: of 215 such planes, the 187 whose centre falls inside an
#: ``FGWaterVolume`` sit on that volume's top to a median of 1.3 cm. Read from the container
#: rather than hard-coded, so a resized plane moves it.
WATER_PLANE_MESH = "/Game/FactoryGame/World/Environment/Water/Mesh/WaterPlane"


def is_water_class(name: str) -> bool:
    """Whether a class name is one of the world's water actors. A shape, not a list."""
    return name.startswith(WATER_CLASS_PREFIXES) and any(t in name for t in WATER_CLASS_TOKENS)


def _box_sphere_bounds(payload: bytes, names: list[str]) -> _BoundsPair | None:
    """An ``FBoxSphereBounds``, unwrapping the ``CachedBounds`` container it arrives in."""
    entries, _end = property_tags(payload, names, 0)
    found = {tag.name: tag.payload for tag in entries if tag.name is not None}
    if "Value" in found:
        return _box_sphere_bounds(found["Value"], names)
    pair: _BoundsPair | None = bounds_pair(found)
    return pair


def _agg_geom_box(payload: bytes, names: list[str]) -> Corners | None:
    """The union of every convex element's ``ElemBox`` in a cooked ``FKAggregateGeom``.

    This is where an ``FGWaterVolume`` keeps its shape. A cooked BSP brush holds its
    vertices in WORLD space and its component transform is legitimately the identity, so 270
    of these decode with no ``RelativeLocation`` anywhere on the actor. An ``FBox`` is 3
    doubles of min, 3 of max and a validity byte.
    """
    entries, _end = property_tags(payload, names, 0)
    low = [math.inf] * 3
    high = [-math.inf] * 3
    found = 0
    for name, _kind, array, _flags, _index in entries:
        if name not in ("ConvexElems", "BoxElems") or len(array) < 4:
            continue
        count = struct.unpack_from("<I", array, 0)[0]
        position = 4
        for _ in range(count):
            elements, position = property_tags(array, names, position)
            for inner, _k, blob, _flags, _index in elements:
                if inner == "ElemBox" and len(blob) >= 48:
                    minimum = struct.unpack_from("<3d", blob, 0)
                    maximum = struct.unpack_from("<3d", blob, 24)
                    for axis in range(3):
                        low[axis] = min(low[axis], minimum[axis])
                        high[axis] = max(high[axis], maximum[axis])
                    found += 1
            if position >= len(array):
                break
    return (low, high) if found else None


def _corners_to_world(low: list[float], high: list[float], transform: _Transform) -> Corners:
    """A local box through a world transform, eight corners at a time.

    Corner by corner rather than centre-plus-extent, because a rotated volume's world AABB
    is the box AROUND the rotated box, not the unrotated box moved. 486 of the 837 water
    actors are rotated.
    """
    location, rotation, scale = transform
    out_low = [math.inf] * 3
    out_high = [-math.inf] * 3
    for x in (low[0], high[0]):
        for y in (low[1], high[1]):
            for z in (low[2], high[2]):
                turned = quat_rotate(rotation, (x * scale[0], y * scale[1], z * scale[2]))
                for axis in range(3):
                    value = location[axis] + turned[axis]
                    out_low[axis] = min(out_low[axis], value)
                    out_high[axis] = max(out_high[axis], value)
    return out_low, out_high


def _mesh_box(
    view: PackageView, slot: int, name: str, meshes: MeshBounds
) -> tuple[_BoundsPair | None, str]:
    """An instanced or static mesh component's ``(origin, extent)`` and its source's name."""
    props = view.props(slot)
    if name != "StaticMeshComponent":
        cached = props.get("CachedBounds")
        pair = _box_sphere_bounds(cached, view.pkg.names) if cached else None
        return pair, "InstancedStaticMeshComponent.CachedBounds"
    mesh = view.import_path(props.get("StaticMesh", b"")) if "StaticMesh" in props else None
    own: _BoundsPair | None = meshes.extended_bounds(mesh) if mesh else None
    if own is not None:
        return own, "StaticMesh.ExtendedBounds"
    plane: _BoundsPair | None = meshes.extended_bounds(WATER_PLANE_MESH)
    return plane, "WaterPlane.ExtendedBounds (assumed)"


def _component_box(
    view: PackageView, slot: int, name: str, meshes: MeshBounds
) -> tuple[Corners, str] | None:
    """One component's LOCAL box and where it came from, or ``None``.

    Four sources, tried in the order they are trustworthy: the component's own
    ``BoxExtent``, a BSP volume's cooked ``BrushBodySetup.AggGeom``, an instanced
    component's ``CachedBounds``, and a ``StaticMeshComponent``'s mesh ``ExtendedBounds``.
    That last falls back to the water plane's when the cooked instance names no mesh, which
    is the normal case here and is flagged in the returned source name rather than hidden.
    """
    props = view.props(slot)
    if len(props.get("BoxExtent", b"")) == 24:
        extent: tuple[float, float, float] = struct.unpack("<3d", props["BoxExtent"])
        return ([-e for e in extent], list(extent)), "BoxComponent.BoxExtent"
    if name == "BrushComponent":
        setup = view.export_ref(props.get("BrushBodySetup", b""))
        geometry = view.props(setup).get("AggGeom") if setup is not None else None
        box = _agg_geom_box(geometry, view.pkg.names) if geometry else None
        return (box, "BrushBodySetup.AggGeom") if box else None
    if name not in _MESH_COMPONENTS:
        return None
    pair, source = _mesh_box(view, slot, name, meshes)
    if pair is None:
        return None
    origin, extent = pair
    low = [origin[axis] - extent[axis] for axis in range(3)]
    high = [origin[axis] + extent[axis] for axis in range(3)]
    return (low, high), source


def water_actor_box(
    view: PackageView, actor: int, classes: ClassFacts, meshes: MeshBounds
) -> tuple[WaterBox | None, set[str]]:
    """One water actor's world AABB in centimetres, and the box sources it came from.

    The union over every box-like component in the actor's export subtree, each taken to
    world space through its own composed ``AttachParent`` chain.

    The last block is a refusal: a mesh's ``ExtendedBounds`` is centred on the mesh's own
    origin, so an actor whose only box is an assumed plane and which states no transform
    anywhere would land at the world origin -- a parse artefact, not a placement. It cannot
    catch a ``BrushComponent``, whose vertices are already world-space and whose identity
    transform is correct.
    """
    stack = [actor]
    seen: set[int] = set()
    low = [math.inf] * 3
    high = [-math.inf] * 3
    sources: set[str] = set()
    positioned = view.props(actor).get("RelativeLocation") is not None
    while stack:
        slot = stack.pop()
        if slot in seen:
            continue
        seen.add(slot)
        stack.extend(view.children.get(slot, []))
        name = class_name_of(view.class_of.get(slot))
        if name not in WATER_BOX_COMPONENTS:
            continue
        found = _component_box(view, slot, name, meshes)
        if found is None:
            continue
        (local_low, local_high), source = found
        placed: _Transform | None = world_transform(view, slot, classes)[0]
        transform = _IDENTITY if placed is None else placed
        if view.props(slot).get("RelativeLocation") is not None or transform[0] != (0.0, 0.0, 0.0):
            positioned = True
        corner_low, corner_high = _corners_to_world(local_low, local_high, transform)
        for axis in range(3):
            low[axis] = min(low[axis], corner_low[axis])
            high[axis] = max(high[axis], corner_high[axis])
        sources.add(source)
    if not sources or not all(math.isfinite(v) for v in low + high):
        return None, sources
    if not positioned and all("assumed" in source for source in sources):
        return None, set()
    return (low[0], low[1], low[2], high[0], high[1], high[2]), sources


def box_texels(box: Sequence[float], shape: tuple[int, int]) -> tuple[slice, slice] | None:
    """The texels a box covers, vertex-aligned: a texel's own point lies inside it."""
    x0, y0, _z0, x1, y1, _z1 = box
    col0 = max(0, math.ceil((x0 - ORIGIN_X_CM) / SPACING_CM))
    col1 = min(shape[1], math.floor((x1 - ORIGIN_X_CM) / SPACING_CM) + 1)
    row0 = max(0, math.ceil((y0 - ORIGIN_Y_CM) / SPACING_CM))
    row1 = min(shape[0], math.floor((y1 - ORIGIN_Y_CM) / SPACING_CM) + 1)
    return None if col1 <= col0 or row1 <= row0 else (slice(row0, row1), slice(col0, col1))


def water_box_tops(
    boxes: Iterable[tuple[str, Sequence[float]]],
    classes: Collection[str] = WATER_SURFACE_CLASSES,
    shape: tuple[int, int] = (GRID_PX, GRID_PX),
) -> tuple[F32Grid, int]:
    """The highest top of the ``classes`` boxes over each texel in metres, ``nan`` where none,
    and how many boxes reached the grid.

    A box's top IS the surface of the volume it bounds, so where several overlap in plan the
    highest is the one visible from above. The save's 23 water extractors all sit inside a
    volume and every one of them stands on its box's top to within 0.005 cm.
    """
    tops = np.full(shape, np.nan, np.float32)
    used = 0
    for name, box in boxes:
        texels = box_texels(box, shape) if name in classes else None
        if texels is None:
            continue
        used += 1
        window = tops[texels]
        top = np.float32(box[5] / 100.0)
        np.maximum(window, top, out=window, where=np.isfinite(window))
        window[~np.isfinite(window)] = top
    return tops, used

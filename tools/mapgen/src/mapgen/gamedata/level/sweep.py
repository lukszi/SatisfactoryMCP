"""The level sweep: one walk of every level for its foliage, water and landscape."""

from __future__ import annotations

import struct
import time

import numpy as np

from mapgen.gamedata.level.landscape import grass_data_heights
from mapgen.gamedata.water.actors import is_water_class, water_actor_box
from mapgen.gamedata.water.rivers import RIVER_CLASS, river_actor
from satisfactory_mcp.core.gameassets.levels import level_paths, walk_levels
from satisfactory_mcp.core.gameassets.packages import (
    class_name_of,
    property_tags,
    quat_rotate,
    read_int32,
    root_component,
    world_transform,
)

__all__ = [
    "FOLIAGE_CLASSES",
    "LEVEL_DIR",
    "LEVEL_SUFFIX",
    "TOP_FOLIAGE_MESHES",
    "first_override",
    "flagged_tags",
    "foliage_instances",
    "instance_matrices",
    "is_top_foliage",
    "sweep_levels",
]


#: Which packages are swept. Everything terrain lives under one world.
LEVEL_DIR = "/GameLevel01/"
LEVEL_SUFFIX = ".umap"


#: Foliage-painted rocks with a blocking collision trimesh. They are instances inside
#: foliage components, so the placement sweep never sees them; only ``top.i16.z`` carries them.
TOP_FOLIAGE_MESHES = frozenset(
    {
        "SM_Boulder_01",
        "SM_Boulder_02",
        "SM_Boulder_03",
        "SM_Boulder_04",
        "SM_Boulder_06",
        "SM_SeaRock_06",
    }
)
FOLIAGE_CLASSES = frozenset({"FoliageInstancedStaticMeshComponent", "FGFoliageInstancedSMC"})


# --------------------------------------------------------------------------------------
# Stages 1 and 2: one sweep of the world's packages, two harvests out of it.
# --------------------------------------------------------------------------------------


def flagged_tags(body: bytes, names: list[str], pos: int = 1) -> tuple[dict[str, bytes], int]:
    """Top-level ``{name: payload}`` of a tag stream whose tags carry the 5.x flag byte.

    ``property_tags`` reads the byte after ``Size`` as a bool value; on a foliage component
    it is a flag set that announces an array index, a GUID or an extension block, and
    skipping those wrongly loses ``StaticMesh``. Indexed array elements are left out.
    """
    out: dict[str, bytes] = {}
    limit = len(body)

    def skip_type(at: int) -> int:
        inner = struct.unpack_from("<i", body, at + 8)[0]
        at += 12
        for _ in range(inner):
            at = skip_type(at)
        return at

    while pos + 8 <= limit:
        index, number = struct.unpack_from("<II", body, pos)
        slot = index & 0x3FFFFFFF
        name = names[slot] if (index >> 30) == 0 and slot < len(names) else None
        pos += 8
        if (index == 0 and number == 0) or (name == "None" and number == 0):
            break
        try:
            pos = skip_type(pos)
            size = struct.unpack_from("<i", body, pos)[0]
            flags = body[pos + 4]
        except (struct.error, IndexError, RecursionError):
            break
        pos += 5
        array_index = 0
        if flags & 1:
            array_index = struct.unpack_from("<i", body, pos)[0]
            pos += 4
        if flags & 2:
            pos += 16
        if flags & 4:
            extension = body[pos]
            pos += 1
            if extension & 2:
                pos += 2
        if size < 0 or pos + size > limit:
            break
        if name and array_index == 0:
            out[name] = body[pos : pos + size]
        pos += size
    return out, pos


def instance_matrices(tail: bytes, expect: int | None) -> np.ndarray | None:
    """``PerInstanceSMData`` as (n, 4, 4) float64 world-relative matrices, or ``None``.

    Bulk-serialised as an element size of 128 (one FMatrix of doubles) and a count, found by
    scanning the first 4 KiB past the tags; a candidate must have a unit last column.
    """
    for off in range(min(len(tail) - 8, 4096)):
        element, count = struct.unpack_from("<ii", tail, off)
        if element != 128 or count <= 0 or off + 8 + count * 128 > len(tail):
            continue
        if expect is not None and count != expect:
            continue
        mats = np.frombuffer(tail, "<f8", count=count * 16, offset=off + 8).reshape(count, 4, 4)
        if np.allclose(mats[:, 3, 3], 1.0) and np.allclose(mats[:, :3, 3], 0.0):
            return mats
    return None


def first_override(view, payload: bytes | None) -> str | None:
    """The first non-empty entry of a component's ``OverrideMaterials``, as a package path."""
    if not payload or len(payload) < 4:
        return None
    count = struct.unpack_from("<i", payload, 0)[0]
    for i in range(max(0, min(count, (len(payload) - 4) // 4))):
        path = view.import_path(payload[4 + 4 * i : 8 + 4 * i])
        if path:
            return path
    return None


def is_top_foliage(mesh: str) -> bool:
    return mesh.rsplit("/", 1)[-1] in TOP_FOLIAGE_MESHES


def foliage_instances(
    view, slot: int, classes, wanted=is_top_foliage
) -> tuple[str, np.ndarray] | None:
    """One foliage component's mesh and world matrices, for meshes ``wanted`` accepts."""
    body = view.pkg.body(view.exports[slot])
    props, end = flagged_tags(body, view.pkg.names)
    reference = props.get("StaticMesh")
    mesh = view.import_path(reference) if reference else None
    if not mesh or not wanted(mesh):
        return None
    built = props.get("NumBuiltInstances")
    expect = struct.unpack("<i", built)[0] if built and len(built) == 4 else None
    mats = instance_matrices(body[end:], expect)
    if mats is None:
        return None

    def triple(key: str, default: tuple[float, float, float]) -> np.ndarray:
        raw = props.get(key, b"")
        return np.array(struct.unpack("<3d", raw) if len(raw) == 24 else default)

    own = triple("RelativeLocation", (0.0, 0.0, 0.0))
    parent = view.export_ref(props["AttachParent"]) if "AttachParent" in props else None
    transform = world_transform(view, parent, classes)[0] if parent is not None else None
    if transform is None:
        transform = ((0.0, 0.0, 0.0), (0.0, 0.0, 0.0, 1.0), (1.0, 1.0, 1.0))
    location, quat, scale = transform
    rotation = np.stack([np.array(quat_rotate(quat, tuple(axis))) for axis in np.eye(3)])
    size = np.array(scale)
    world = mats.copy()
    world[:, :3, :3] = (mats[:, :3, :3] * size[None, None, :]) @ rotation
    world[:, 3, :3] = ((mats[:, 3, :3] + own) * size) @ rotation + np.array(location)
    return mesh, world


def _placement(view, slot) -> tuple | None:
    """``(mesh path, x, y, z, pitch, yaw, roll, sx, sy, sz)`` of a root mesh component."""
    props = view.props(slot)
    reference = props.get("StaticMesh")
    location = props.get("RelativeLocation")
    if reference is None or location is None or len(location) != 24:
        return None
    mesh = view.import_path(reference)
    if not mesh:
        return None
    rotation = props.get("RelativeRotation")
    scale = props.get("RelativeScale3D")
    turn = struct.unpack("<3d", rotation) if rotation and len(rotation) == 24 else (0.0,) * 3
    size = struct.unpack("<3d", scale) if scale and len(scale) == 24 else (1.0,) * 3
    return (mesh, *struct.unpack("<3d", location), *turn, *size)


def sweep_levels(
    store, scripts, classes, meshes, progress: bool = True, extra_foliage=None, read_actor=None
) -> dict:
    """One pass over every ``*.umap`` of the world: landscape, placements, water actors.

    All three harvests need the same ``PackageView`` of the same 4,521 packages, and
    building that view is the whole cost of the pass, so they share it. Returns raw material
    and nothing interpreted. Foliage ``extra_foliage`` accepts lands in ``extra_foliage``;
    whatever ``read_actor(view, slot, class path, classes)`` returns for a level actor, in
    ``actors``.
    """
    components: list[tuple[int, int, np.ndarray]] = []
    proxies: list[tuple[float, float, float, float, float, float]] = []
    #: (mesh id, owner id, x, y, z, pitch, yaw, roll, sx, sy, sz)
    placements: list[tuple[float, ...]] = []
    #: (class name, (x0, y0, z0, x1, y1, z1)) in world centimetres, for the water stage.
    water: list[tuple[str, tuple[float, ...]]] = []
    water_actors: dict[str, int] = {}
    water_boxless: list[tuple[str, str, str]] = []
    rivers: list[dict] = []
    box_sources: dict[str, int] = {}
    mesh_ids: dict[str, int] = {}
    owner_ids: dict[str, int] = {}
    #: Each placement's first override material, as an index into ``materials``; -1 for none.
    material_ids: dict[str, int] = {}
    chosen: list[int] = []
    foliage: dict[str, list[np.ndarray]] = {}
    extra: dict[str, list[np.ndarray]] = {}
    actors: list = []
    unreadable = 0
    malformed = 0
    started = time.time()

    def count_unreadable(_path: str, _exc: Exception) -> None:
        nonlocal unreadable
        unreadable += 1

    paths = level_paths(store, contains=LEVEL_DIR, suffix=LEVEL_SUFFIX)
    for index, total, path, view in walk_levels(
        store, scripts, paths=paths, on_unreadable=count_unreadable
    ):
        # An actor names its own root; a StaticMeshComponent that is not one is a
        # decoration hanging off something else, and its transform is relative to a parent
        # this sweep does not walk. Built first so the placement loop can just look up.
        root_owner: dict[int, str] = {}
        for slot, class_path in view.class_of.items():
            root = root_component(view, slot)
            if root is not None:
                root_owner[root] = class_name_of(class_path)

        for slot, class_path in view.class_of.items():
            if read_actor is not None and view.outer_of.get(slot) in view.level_slots:
                found = read_actor(view, slot, class_path, classes)
                if found is not None:
                    actors.append(found)
            name = class_name_of(class_path)
            if name == "LandscapeStreamingProxy":
                props = view.props(slot)
                offset = props.get("LandscapeSectionOffset")
                root = view.export_ref(props.get("RootComponent", b""))
                if not offset or len(offset) != 8 or root is None:
                    continue
                section_x, section_y = struct.unpack("<2i", offset)
                location = view.props(root).get("RelativeLocation")
                scale = view.props(root).get("RelativeScale3D")
                if not location or len(location) != 24 or not scale or len(scale) != 24:
                    continue
                lx, ly, lz = struct.unpack("<3d", location)
                sx, sy, sz = struct.unpack("<3d", scale)
                proxies.append((section_x - lx / sx, section_y - ly / sy, lz, sx, sy, sz))
            elif name == "LandscapeComponent":
                props = view.props(slot)
                base_x = read_int32(props.get("SectionBaseX", b"\0\0\0\0"))
                base_y = read_int32(props.get("SectionBaseY", b"\0\0\0\0"))
                body = view.pkg.body(view.exports[slot])
                _tags, end = property_tags(body, view.pkg.names)
                heights = grass_data_heights(body[end:])
                if heights is None:
                    malformed += 1
                    continue
                components.append((base_x, base_y, heights))
            elif name == "StaticMeshComponent":
                placed = _placement(view, slot) if slot in root_owner else None
                if placed is None:
                    continue
                mesh_id = mesh_ids.setdefault(placed[0], len(mesh_ids))
                owner_id = owner_ids.setdefault(root_owner[slot], len(owner_ids))
                placements.append((mesh_id, owner_id, *placed[1:]))
                material = first_override(view, view.props(slot).get("OverrideMaterials"))
                chosen.append(
                    material_ids.setdefault(material, len(material_ids)) if material else -1
                )
            elif name in FOLIAGE_CLASSES:
                found = foliage_instances(
                    view,
                    slot,
                    classes,
                    wanted=lambda m: is_top_foliage(m) or bool(extra_foliage and extra_foliage(m)),
                )
                if found is not None:
                    harvest = foliage if is_top_foliage(found[0]) else extra
                    harvest.setdefault(found[0], []).append(found[1])
            elif is_water_class(name):
                water_actors[name] = water_actors.get(name, 0) + 1
                box, sources = water_actor_box(view, slot, classes, meshes)
                for source in sources:
                    box_sources[source] = box_sources.get(source, 0) + 1
                if box is None:
                    water_boxless.append(
                        (name, view.exports[slot]["name"], path.rsplit("/", 1)[-1])
                    )
                else:
                    water.append((name, box))
                if name == RIVER_CLASS:
                    rivers.append(river_actor(view, slot, classes, meshes))

        if progress and index % 500 == 0:
            print(
                f"  {index}/{total} packages, {len(components)} landscape components, "
                f"{len(placements)} placements, {time.time() - started:.0f}s",
                flush=True,
            )

    return {
        "packages": len(paths),
        "unreadable": unreadable,
        "malformed_components": malformed,
        "components": components,
        "proxies": proxies,
        "placements": np.array(placements, dtype=np.float64) if placements else np.zeros((0, 11)),
        "meshes": [m for m, _ in sorted(mesh_ids.items(), key=lambda kv: kv[1])],
        "owners": [o for o, _ in sorted(owner_ids.items(), key=lambda kv: kv[1])],
        "placement_materials": np.array(chosen, dtype=np.int32),
        "materials": [m for m, _ in sorted(material_ids.items(), key=lambda kv: kv[1])],
        "water": water,
        "water_actors": water_actors,
        "water_boxless": water_boxless,
        "water_box_sources": box_sources,
        "rivers": rivers,
        "foliage": {mesh: np.concatenate(parts) for mesh, parts in foliage.items()},
        "extra_foliage": {mesh: np.concatenate(parts) for mesh, parts in extra.items()},
        "actors": actors,
        "seconds": time.time() - started,
    }

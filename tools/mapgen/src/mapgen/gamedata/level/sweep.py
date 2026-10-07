"""The level sweep: one walk of every level for its foliage, water and landscape."""

from __future__ import annotations

import struct
import time
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from typing import NotRequired, TypeAlias, TypedDict

import numpy as np

from mapgen.gamedata.level.landscape import Proxy, grass_data_heights
from mapgen.gamedata.meshes import MeshBounds
from mapgen.gamedata.water.actors import is_water_class, water_actor_box
from mapgen.gamedata.water.rivers import RIVER_CLASS, RiverRecord, river_actor
from satisfactory_mcp.core.arrays import F64Grid, I32Grid, U16Grid
from satisfactory_mcp.core.gameassets.iostore import IoStore
from satisfactory_mcp.core.gameassets.levels import level_paths, walk_levels
from satisfactory_mcp.core.gameassets.packages import (
    ClassFacts,
    PackageView,
    ScriptObjects,
    class_name_of,
    property_tags,
    quat_rotate,
    read_int32,
    root_component,
    tagged_properties,
    world_transform,
)

__all__ = [
    "FOLIAGE_CLASSES",
    "LEVEL_DIR",
    "LEVEL_SUFFIX",
    "TOP_FOLIAGE_MESHES",
    "Sweep",
    "Transform",
    "first_override",
    "foliage_instances",
    "instance_matrices",
    "instances_to_world",
    "is_top_foliage",
    "quat_axes",
    "sweep_levels",
    "tag_payloads",
    "world_level_paths",
    "world_levels",
]

#: A component's world ``(location cm, rotation quaternion xyzw, scale)``.
Transform: TypeAlias = tuple[
    tuple[float, float, float], tuple[float, float, float, float], tuple[float, float, float]
]
#: ``read_actor(view, slot, class path, classes)``: a level actor's record, or ``None``.
ActorReader: TypeAlias = Callable[[PackageView, int, str | None, ClassFacts], object]


class Sweep(TypedDict):
    """``sweep_levels``' one walk of the world; ``terrain.rasters.sweep_world`` adds ``trees``."""

    packages: int
    unreadable: int
    malformed_components: int
    components: list[tuple[int, int, U16Grid]]
    proxies: list[Proxy]
    placements: F64Grid
    meshes: list[str]
    owners: list[str]
    placement_materials: I32Grid
    materials: list[str]
    water: list[tuple[str, tuple[float, ...]]]
    water_actors: dict[str, int]
    water_boxless: list[tuple[str, str, str]]
    water_box_sources: dict[str, int]
    rivers: list[RiverRecord]
    foliage: dict[str, F64Grid]
    extra_foliage: dict[str, F64Grid]
    actors: list[object]
    seconds: float
    trees: NotRequired[dict[str, F64Grid]]


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


def world_level_paths(store: IoStore) -> list[str]:
    """Every level package of the world, in the order every sweep walks them."""
    return level_paths(store, contains=LEVEL_DIR, suffix=LEVEL_SUFFIX)


def world_levels(
    store: IoStore,
    scripts: ScriptObjects,
    on_unreadable: Callable[[str, Exception], None] | None = None,
) -> Iterator[tuple[int, int, str, PackageView]]:
    """``(index, total, path, view)`` for every readable level package of the world."""
    return walk_levels(store, scripts, paths=world_level_paths(store), on_unreadable=on_unreadable)


def quat_axes(quat: tuple[float, float, float, float]) -> F64Grid:
    """A rotation quaternion as a matrix whose rows are the rotated local X, Y and Z axes."""
    return np.stack([np.array(quat_rotate(quat, tuple(axis))) for axis in np.eye(3)])


def instances_to_world(
    mats: F64Grid, transform: Transform, local_offset: F64Grid | None = None
) -> F64Grid:
    """Instance matrices (rows: scaled axes, then origin) carried through a parent transform.

    ``local_offset`` moves every origin in the parent's space first: a component's own
    ``RelativeLocation``.
    """
    location, quat, scale = transform
    rotation, size = quat_axes(quat), np.array(scale)
    origins = mats[:, 3, :3] if local_offset is None else mats[:, 3, :3] + local_offset
    world = mats.copy()
    world[:, :3, :3] = (mats[:, :3, :3] * size[None, None, :]) @ rotation
    world[:, 3, :3] = (origins * size) @ rotation + np.array(location)
    return world


def tag_payloads(body: bytes, names: list[str], pos: int = 1) -> tuple[dict[str, bytes], int]:
    """Top-level ``{name: payload}`` of a tag stream, and the offset after it. The elements of
    a fixed-size array past the first are left out."""
    tags, end = tagged_properties(body, names, pos)
    return {tag.name: tag.payload for tag in tags if tag.name and tag.array_index == 0}, end


def instance_matrices(tail: bytes, expect: int | None) -> F64Grid | None:
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


def first_override(view: PackageView, payload: bytes | None) -> str | None:
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
    view: PackageView,
    slot: int,
    classes: ClassFacts,
    wanted: Callable[[str], bool] = is_top_foliage,
) -> tuple[str, F64Grid] | None:
    """One foliage component's mesh and world matrices, for meshes ``wanted`` accepts."""
    body = view.pkg.body(view.exports[slot])
    props, end = tag_payloads(body, view.pkg.names)
    reference = props.get("StaticMesh")
    mesh = view.import_path(reference) if reference else None
    if not mesh or not wanted(mesh):
        return None
    built = props.get("NumBuiltInstances")
    expect = struct.unpack("<i", built)[0] if built and len(built) == 4 else None
    mats = instance_matrices(body[end:], expect)
    if mats is None:
        return None
    raw = props.get("RelativeLocation", b"")
    own = np.array(struct.unpack("<3d", raw) if len(raw) == 24 else (0.0, 0.0, 0.0))
    parent = view.export_ref(props["AttachParent"]) if "AttachParent" in props else None
    transform = world_transform(view, parent, classes)[0] if parent is not None else None
    if transform is None:
        transform = ((0.0, 0.0, 0.0), (0.0, 0.0, 0.0, 1.0), (1.0, 1.0, 1.0))
    return mesh, instances_to_world(mats, transform, own)


def _placement(view: PackageView, slot: int) -> tuple[str, *tuple[float, ...]] | None:
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


def _proxy(view: PackageView, slot: int) -> Proxy | None:
    """A ``LandscapeStreamingProxy``'s origin in landscape quads, its Z offset and scale."""
    props = view.props(slot)
    offset = props.get("LandscapeSectionOffset")
    root = view.export_ref(props.get("RootComponent", b""))
    if not offset or len(offset) != 8 or root is None:
        return None
    section_x, section_y = struct.unpack("<2i", offset)
    location = view.props(root).get("RelativeLocation")
    scale = view.props(root).get("RelativeScale3D")
    if not location or len(location) != 24 or not scale or len(scale) != 24:
        return None
    lx, ly, lz = struct.unpack("<3d", location)
    sx, sy, sz = struct.unpack("<3d", scale)
    return (section_x - lx / sx, section_y - ly / sy, lz, sx, sy, sz)


def _component(view: PackageView, slot: int) -> tuple[int, int, U16Grid] | None:
    """A ``LandscapeComponent``'s section base and height samples; ``None`` if malformed."""
    props = view.props(slot)
    base_x = read_int32(props.get("SectionBaseX", b"\0\0\0\0"))
    base_y = read_int32(props.get("SectionBaseY", b"\0\0\0\0"))
    body = view.pkg.body(view.exports[slot])
    _tags, end = property_tags(body, view.pkg.names)
    heights = grass_data_heights(body[end:])
    if base_x is None or base_y is None or heights is None:
        return None
    return base_x, base_y, heights


def _root_owners(view: PackageView) -> dict[int, str]:
    """Each actor's root component slot, mapped to the actor's class name.

    A ``StaticMeshComponent`` that is no actor's root is a decoration hanging off something
    else, its transform relative to a parent the sweep does not walk.
    """
    owners: dict[int, str] = {}
    for slot, class_path in view.class_of.items():
        root = root_component(view, slot)
        if root is not None:
            owners[root] = class_name_of(class_path)
    return owners


@dataclass
class _Harvest:
    """What one walk of the levels gathers, before it is packed into the sweep."""

    meshes: MeshBounds
    classes: ClassFacts
    extra_foliage: Callable[[str], bool] | None
    components: list[tuple[int, int, U16Grid]] = field(
        default_factory=list[tuple[int, int, U16Grid]]
    )
    proxies: list[Proxy] = field(default_factory=list[Proxy])
    #: (mesh id, owner id, x, y, z, pitch, yaw, roll, sx, sy, sz)
    placements: list[tuple[float, ...]] = field(default_factory=list[tuple[float, ...]])
    mesh_ids: dict[str, int] = field(default_factory=dict[str, int])
    owner_ids: dict[str, int] = field(default_factory=dict[str, int])
    #: Each placement's first override material, an index into ``material_ids``; -1 for none.
    chosen: list[int] = field(default_factory=list[int])
    material_ids: dict[str, int] = field(default_factory=dict[str, int])
    #: (class name, (x0, y0, z0, x1, y1, z1)) in world centimetres, for the water stage.
    water: list[tuple[str, tuple[float, ...]]] = field(
        default_factory=list[tuple[str, tuple[float, ...]]]
    )
    water_actors: dict[str, int] = field(default_factory=dict[str, int])
    water_boxless: list[tuple[str, str, str]] = field(default_factory=list[tuple[str, str, str]])
    box_sources: dict[str, int] = field(default_factory=dict[str, int])
    rivers: list[RiverRecord] = field(default_factory=list[RiverRecord])
    foliage: dict[str, list[F64Grid]] = field(default_factory=dict[str, list[F64Grid]])
    extra: dict[str, list[F64Grid]] = field(default_factory=dict[str, list[F64Grid]])
    actors: list[object] = field(default_factory=list[object])
    malformed: int = 0

    def add(
        self, view: PackageView, slot: int, name: str, root_owner: str | None, path: str
    ) -> None:
        """One export of class ``name``, kept where it is something the sweep collects."""
        if name == "LandscapeStreamingProxy":
            proxy = _proxy(view, slot)
            if proxy is not None:
                self.proxies.append(proxy)
        elif name == "LandscapeComponent":
            component = _component(view, slot)
            if component is None:
                self.malformed += 1
            else:
                self.components.append(component)
        elif name == "StaticMeshComponent":
            if root_owner is not None:
                self._placement(view, slot, root_owner)
        elif name in FOLIAGE_CLASSES:
            self._foliage(view, slot)
        elif is_water_class(name):
            self._water_actor(view, slot, name, path)

    def _placement(self, view: PackageView, slot: int, owner: str) -> None:
        placed = _placement(view, slot)
        if placed is None:
            return
        mesh, *transform = placed
        mesh_id = self.mesh_ids.setdefault(mesh, len(self.mesh_ids))
        owner_id = self.owner_ids.setdefault(owner, len(self.owner_ids))
        self.placements.append((mesh_id, owner_id, *transform))
        material = first_override(view, view.props(slot).get("OverrideMaterials"))
        self.chosen.append(
            self.material_ids.setdefault(material, len(self.material_ids)) if material else -1
        )

    def _foliage(self, view: PackageView, slot: int) -> None:
        extra = self.extra_foliage

        def wanted(mesh: str) -> bool:
            return is_top_foliage(mesh) or bool(extra and extra(mesh))

        found = foliage_instances(view, slot, self.classes, wanted=wanted)
        if found is not None:
            harvest = self.foliage if is_top_foliage(found[0]) else self.extra
            harvest.setdefault(found[0], []).append(found[1])

    def _water_actor(self, view: PackageView, slot: int, name: str, path: str) -> None:
        self.water_actors[name] = self.water_actors.get(name, 0) + 1
        box, sources = water_actor_box(view, slot, self.classes, self.meshes)
        for source in sources:
            self.box_sources[source] = self.box_sources.get(source, 0) + 1
        if box is None:
            self.water_boxless.append((name, view.exports[slot]["name"], path.rsplit("/", 1)[-1]))
        else:
            self.water.append((name, box))
        if name == RIVER_CLASS:
            self.rivers.append(river_actor(view, slot, self.classes, self.meshes))

    def packed(self, packages: int, unreadable: int, seconds: float) -> Sweep:
        """The sweep's record, keys in the order ``Sweep`` lists them."""

        def by_id(ids: dict[str, int]) -> list[str]:
            return [name for name, _ in sorted(ids.items(), key=lambda kv: kv[1])]

        return {
            "packages": packages,
            "unreadable": unreadable,
            "malformed_components": self.malformed,
            "components": self.components,
            "proxies": self.proxies,
            "placements": (
                np.array(self.placements, dtype=np.float64)
                if self.placements
                else np.zeros((0, 11))
            ),
            "meshes": by_id(self.mesh_ids),
            "owners": by_id(self.owner_ids),
            "placement_materials": np.array(self.chosen, dtype=np.int32),
            "materials": by_id(self.material_ids),
            "water": self.water,
            "water_actors": self.water_actors,
            "water_boxless": self.water_boxless,
            "water_box_sources": self.box_sources,
            "rivers": self.rivers,
            "foliage": {mesh: np.concatenate(parts) for mesh, parts in self.foliage.items()},
            "extra_foliage": {mesh: np.concatenate(parts) for mesh, parts in self.extra.items()},
            "actors": self.actors,
            "seconds": seconds,
        }


def sweep_levels(
    store: IoStore,
    scripts: ScriptObjects,
    classes: ClassFacts,
    meshes: MeshBounds,
    progress: bool = True,
    extra_foliage: Callable[[str], bool] | None = None,
    read_actor: ActorReader | None = None,
) -> Sweep:
    """One pass over every ``*.umap`` of the world: landscape, placements, water actors.

    All three harvests need the same ``PackageView`` of the same 4,521 packages, and
    building that view is the whole cost of the pass, so they share it. Returns raw material
    and nothing interpreted. Foliage ``extra_foliage`` accepts lands in
    ``extra_foliage``; whatever ``read_actor`` returns for a level actor, in ``actors``.
    """
    harvest = _Harvest(meshes, classes, extra_foliage)
    unreadable = 0
    started = time.time()

    def count_unreadable(_path: str, _exc: Exception) -> None:
        nonlocal unreadable
        unreadable += 1

    paths = world_level_paths(store)
    for index, total, path, view in walk_levels(
        store, scripts, paths=paths, on_unreadable=count_unreadable
    ):
        root_owner = _root_owners(view)
        for slot, class_path in view.class_of.items():
            if read_actor is not None and view.outer_of.get(slot) in view.level_slots:
                found = read_actor(view, slot, class_path, classes)
                if found is not None:
                    harvest.actors.append(found)
            harvest.add(view, slot, class_name_of(class_path), root_owner.get(slot), path)
        if progress and index % 500 == 0:
            print(
                f"  {index}/{total} packages, {len(harvest.components)} landscape components, "
                f"{len(harvest.placements)} placements, {time.time() - started:.0f}s",
                flush=True,
            )
    return harvest.packed(len(paths), unreadable, time.time() - started)

"""Which kind of water each water texel is: ocean, river, lake, swamp, sulfur pond and so on.

The paint command records every water actor's box and material, and every hot-spring terrace,
in ``water_bodies.json``; ``classify`` turns that into a class plane on the 1 m grid. The
rules and the classes are in docs/spatial-and-map.md section 28.
"""

from __future__ import annotations

import struct

import numpy as np
from scipy import ndimage

from mapgen.gamedata.frame import ORIGIN_X_CM, ORIGIN_Y_CM, SPACING_CM
from mapgen.gamedata.mesh import is_water_class, water_actor_box
from satisfactory_mcp.core.gameassets.packages import class_name_of, root_component

__all__ = [
    "BIOME_CLASS",
    "BOX_Z_TOLERANCE_M",
    "CLASSES",
    "DRY",
    "HOT_SPRING_BOX_MAX_M",
    "HOT_SPRING_MARK",
    "MAJORITY_SHARE",
    "MATERIAL_CLASS",
    "OCEAN",
    "OCEAN_BAND_M",
    "WATER_BODIES_NAME",
    "actor_materials",
    "body_class",
    "classify",
    "harvest",
]

WATER_BODIES_NAME = "water_bodies.json"

#: Index = value in the class plane. 0 is dry.
CLASSES = (
    "dry",
    "ocean",
    "river",
    "lake",
    "lake_blue",
    "turquoise",
    "swamp",
    "cave",
    "sulfur",
    "hot_spring",
)
DRY, OCEAN = 0, 1
_ID = {name: i for i, name in enumerate(CLASSES)}

#: A water actor's first material, by short name.
MATERIAL_CLASS = {
    "MI_SLW_River_Base_01": "river",
    "MI_SLW_River_Lake-OceanBlend_Flipped": "river",
    "MM_Lake_01": "lake",
    "MI_Lake_Blue_01": "lake_blue",
    "MI_Lake_Turquoise_01": "turquoise",
    "MI_WaterSwamp_Muddy": "swamp",
    "MI_Lake_Caves_01": "cave",
    "SulfurPond_Inst": "sulfur",
    "MM_OceanMaster": "ocean",
}

#: Inland water no material reaches, by biome; anything else is a lake.
BIOME_CLASS = {"Area_Swamp": "swamp"}

#: Wet texels this close to the ocean level that no body claims are ocean.
OCEAN_BAND_M = 1.0

#: A box claims a texel whose water level lies within this of the box's z range.
BOX_Z_TOLERANCE_M = 1.0

#: A body's majority class fills the rest of it only above this share of its texels.
MAJORITY_SHARE = 0.25

HOT_SPRING_MARK = "/HotSpring/"

#: A lake box holding a hot-spring terrace is a hot spring only up to this side.
HOT_SPRING_BOX_MAX_M = 150.0

_MATERIAL_KEYS = ("OverrideMaterials", "Material", "WaterMaterial", "OceanMaterial", "LakeMaterial")


def actor_materials(view, actor: int) -> tuple[str, ...]:
    """Short names of every material the actor's export subtree assigns, sorted."""
    found: set[str] = set()
    stack, seen = [actor], set()
    while stack:
        slot = stack.pop()
        if slot in seen:
            continue
        seen.add(slot)
        stack.extend(view.children.get(slot, []))
        props = view.props(slot)
        for key in _MATERIAL_KEYS:
            raw = props.get(key)
            if not raw:
                continue
            refs = [raw[:4]]
            if key == "OverrideMaterials" and len(raw) >= 4:
                count = struct.unpack_from("<I", raw)[0]
                refs = [raw[4 + 4 * k : 8 + 4 * k] for k in range(count)]
            for ref in refs:
                path = view.import_path(ref)
                if path:
                    found.add(path.rsplit("/", 1)[-1].split(".")[0])
    return tuple(sorted(found))


def harvest(view, classes, meshes, out: dict) -> None:
    """Add one level's water actors and hot-spring terraces to ``out``."""
    roots = {root_component(view, slot) for slot in view.class_of}
    for slot, class_path in view.class_of.items():
        name = class_name_of(class_path)
        if is_water_class(name):
            box, _sources = water_actor_box(view, slot, classes, meshes)
            if box is not None:
                box = [round(v, 1) for v in box]
                out["actors"].append([name, box, list(actor_materials(view, slot))])
        elif name == "StaticMeshComponent" and slot in roots:
            props = view.props(slot)
            location = props.get("RelativeLocation")
            mesh = view.import_path(props.get("StaticMesh", b"")) if "StaticMesh" in props else None
            if mesh and HOT_SPRING_MARK in mesh and location and len(location) == 24:
                out["hot_springs"].append([round(v, 1) for v in struct.unpack("<3d", location)])


def body_class(name: str, materials, box, hot_springs: np.ndarray) -> str | None:
    """One actor's class: its material's, a lake holding a terrace becomes a hot spring."""
    found = next((MATERIAL_CLASS[m] for m in materials if m in MATERIAL_CLASS), None)
    if found != "lake" or not len(hot_springs):
        return found
    x0, y0, z0, x1, y1, z1 = box
    if max(x1 - x0, y1 - y0) > HOT_SPRING_BOX_MAX_M * 100:
        return found
    pad = BOX_Z_TOLERANCE_M * 100
    inside = (
        (hot_springs[:, 0] >= x0)
        & (hot_springs[:, 0] <= x1)
        & (hot_springs[:, 1] >= y0)
        & (hot_springs[:, 1] <= y1)
        & (hot_springs[:, 2] >= z0 - pad)
        & (hot_springs[:, 2] <= z1 + pad)
    )
    return "hot_spring" if inside.any() else found


def classify(
    level_m: np.ndarray, wet: np.ndarray, bodies: dict, biome: tuple, ocean_level_m: float
) -> tuple[np.ndarray, dict]:
    """The class plane (uint8, ``CLASSES`` index) on the 1 m grid, and counts.

    ``level_m`` is the water level per texel (nan where none), ``wet`` the texels the
    channel calls water, ``biome`` the biome index grid and the names it indexes.
    """
    rows, cols = wet.shape
    springs = np.asarray(bodies.get("hot_springs") or np.zeros((0, 3)), np.float64)
    claims = []
    for name, box, materials in bodies.get("actors", []):
        found = body_class(name, materials, box, springs)
        if found is not None:
            claims.append(((box[3] - box[0]) * (box[4] - box[1]), _ID[found], box))
    plane = np.zeros((rows, cols), np.uint8)
    # Big boxes first, so a small pond inside a big one keeps its own class.
    for _area, cid, (x0, y0, z0, x1, y1, z1) in sorted(claims, key=lambda c: -c[0]):
        c0 = max(int((x0 - ORIGIN_X_CM) / SPACING_CM), 0)
        c1 = min(int(np.ceil((x1 - ORIGIN_X_CM) / SPACING_CM)) + 1, cols)
        r0 = max(int((y0 - ORIGIN_Y_CM) / SPACING_CM), 0)
        r1 = min(int(np.ceil((y1 - ORIGIN_Y_CM) / SPACING_CM)) + 1, rows)
        if c0 >= c1 or r0 >= r1:
            continue
        level = level_m[r0:r1, c0:c1]
        tol = BOX_Z_TOLERANCE_M
        hit = wet[r0:r1, c0:c1] & (level >= z0 / 100 - tol) & (level <= z1 / 100 + tol)
        plane[r0:r1, c0:c1][hit] = cid
    unclaimed = wet & (plane == DRY)
    plane[unclaimed & (np.abs(level_m - ocean_level_m) <= OCEAN_BAND_M)] = OCEAN
    _fill_by_majority(plane, wet)
    left = wet & (plane == DRY)
    if left.any():
        index, names = biome
        lut = np.array([_ID[BIOME_CLASS.get(n, "lake")] for n in names], np.uint8)
        plane[left] = lut[index[left]]
    counts = np.bincount(plane.ravel(), minlength=len(CLASSES))
    return plane, {
        "classes": {CLASSES[i]: int(n) for i, n in enumerate(counts) if i and n},
        "bodies_claimed": len(claims),
        "hot_spring_terraces": len(springs),
        "filled_by_biome": int(left.sum()),
    }


def _fill_by_majority(plane: np.ndarray, wet: np.ndarray) -> None:
    """Unclaimed texels of an inland body take the body's majority class, if it has one."""
    inland = wet & (plane != OCEAN)
    labels, count = ndimage.label(inland, structure=np.ones((3, 3)))
    if not count:
        return
    claimed = inland & (plane != DRY)
    key = labels[claimed].astype(np.int64) * len(CLASSES) + plane[claimed]
    votes = np.bincount(key, minlength=(count + 1) * len(CLASSES)).reshape(count + 1, -1)
    size = np.bincount(labels.ravel(), minlength=count + 1)
    major = votes.argmax(1).astype(np.uint8)
    major[votes.max(1) < MAJORITY_SHARE * size] = DRY
    todo = inland & (plane == DRY)
    plane[todo] = major[labels[todo]]

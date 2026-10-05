"""Which cliff material family each placed rock wears, and what that family's top layer is.

A cliff mesh is placed with an override material whose parent chain ends in one of the
``Cliff_<Layer>`` instances: that instance names the ground texture laid over the rock's
up-facing faces, and the chain carries the cliff's ``Color Tint``. docs/spatial-and-map.md
section 28 has the measurements.
"""

from __future__ import annotations

import struct

import numpy as np

from satisfactory_mcp.core.gameassets.packages import PackageView, property_tags

__all__ = [
    "FAMILIES",
    "FAMILY_ROOTS",
    "MAX_CHAIN",
    "TINT_PARAMETER",
    "TOP_TEXTURE_PARAMETERS",
    "family_of",
    "family_sources",
    "material_params",
    "material_parent",
    "mesh_material",
    "placement_families",
]

#: Family codes, as the direct raster's family plane stores them. 0 is "not a cliff family".
FAMILIES = ("none", "cliff", "forest", "grass", "redgrass", "sand", "wetsand", "redjungle")

#: The material instances that root a family, by leaf name.
FAMILY_ROOTS = {
    "Cliff": "cliff",
    "Cliff_Forest": "forest",
    "Cliff_Grass": "grass",
    "Cliff_RedGrass": "redgrass",
    "Cliff_Sand": "sand",
    "Cliff_WetSand": "wetsand",
    "Cliff_RedJungle": "redjungle",
}
ROOT_DIR = "/Game/FactoryGame/World/Environment/Rock/Cliff/Material/"
TINT_PARAMETER = "Color Tint"
#: The family's top-layer texture, the far one first: the map sees the rock from afar.
TOP_TEXTURE_PARAMETERS = ("Far Albedo", "Albedo")
MAX_CHAIN = 8


def material_params(view) -> tuple[dict[str, tuple], dict[str, str]]:
    """A material instance's vector and texture parameters, by parameter name."""
    props = view.props(0)
    vectors: dict[str, tuple] = {}
    textures: dict[str, str] = {}
    for key, out in (("VectorParameterValues", vectors), ("TextureParameterValues", textures)):
        payload = props.get(key, b"")
        count = struct.unpack_from("<I", payload, 0)[0] if len(payload) >= 4 else 0
        pos = 4
        for _ in range(count):
            tags, pos = property_tags(payload, view.pkg.names, pos)
            name, value = None, None
            for tag, kind, raw, _v in tags:
                if tag == "ParameterInfo" and kind == "StructProperty":
                    inner, _end = property_tags(raw, view.pkg.names, 0)
                    name = next((view._fname(r) for k, ik, r, _iv in inner if k == "Name"), name)
                elif tag == "ParameterValue" and len(raw) == 16 and key.startswith("Vector"):
                    value = tuple(float(v) for v in struct.unpack("<4f", raw)[:3])
                elif tag == "ParameterValue" and key.startswith("Texture"):
                    value = view.import_path(raw)
            if name and value:
                out[name] = value
    return vectors, textures


def material_parent(view) -> str | None:
    """The ``Parent`` a material instance names, or ``None`` for a base material."""
    raw = view.props(0).get("Parent")
    return view.import_path(raw) if raw else None


def _view(store, scripts, index, package: str):
    path = index.path_for(package)
    if not path:
        return None
    try:
        return PackageView(store.read_path(path), scripts)
    except Exception:  # an unreadable material is a rock with no family, not a failed run
        return None


def family_of(store, scripts, index, material: str | None, cache: dict) -> int:
    """The family code a material belongs to, by walking its parents to a family root."""
    if not material:
        return 0
    if material in cache:
        return cache[material]
    code, current = 0, material
    for _ in range(MAX_CHAIN):
        leaf = current.rsplit("/", 1)[-1]
        if leaf in FAMILY_ROOTS:
            code = FAMILIES.index(FAMILY_ROOTS[leaf])
            break
        view = _view(store, scripts, index, current)
        current = material_parent(view) if view is not None else None
        if not current:
            break
    cache[material] = code
    return code


def mesh_material(store, scripts, index, mesh: str, cache: dict) -> str | None:
    """A mesh's own first material, which a placement without an override wears."""
    if mesh not in cache:
        view = _view(store, scripts, index, mesh)
        found = None
        if view is not None:
            found = next(
                (
                    p
                    for p in view.pkg.imported_packages
                    if "/Material" in p and "PhysicalMaterial" not in p
                ),
                None,
            )
        cache[mesh] = found
    return cache[mesh]


def placement_families(store, scripts, index, sweep: dict) -> np.ndarray:
    """One family code per row of ``sweep["placements"]``."""
    meshes, rows = sweep["meshes"], sweep["placements"]
    chosen = sweep.get("placement_materials")
    materials = sweep.get("materials", [])
    by_mesh: dict[str, str | None] = {}
    by_material: dict[str, int] = {}
    codes = np.zeros(len(rows), np.uint8)
    for i, row in enumerate(rows):
        pick = int(chosen[i]) if chosen is not None and i < len(chosen) else -1
        material = materials[pick] if pick >= 0 else None
        if material is None:
            material = mesh_material(store, scripts, index, meshes[int(row[0])], by_mesh)
        codes[i] = family_of(store, scripts, index, material, by_material)
    return codes


def family_sources(store, scripts, index) -> dict[str, dict]:
    """Per family: its ``Color Tint`` (the nearest one up the chain) and top-layer texture."""
    out: dict[str, dict] = {}
    for leaf, family in FAMILY_ROOTS.items():
        tint, top, current = None, None, ROOT_DIR + leaf
        for _ in range(MAX_CHAIN):
            view = _view(store, scripts, index, current)
            if view is None:
                break
            vectors, textures = material_params(view)
            tint = tint or vectors.get(TINT_PARAMETER)
            if current.endswith("/" + leaf):
                top = next((textures[p] for p in TOP_TEXTURE_PARAMETERS if p in textures), None)
            current = material_parent(view)
            if not current or tint is not None:
                break
        out[family] = {"material": ROOT_DIR + leaf, "tint": tint, "top_texture": top}
    return out

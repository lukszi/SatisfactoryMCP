"""The pools on the hot-spring terraces: each terrace mesh's water sections, rasterised as the
terrace class with ``TERRACE_POOL`` above it. docs/map/painted.md section 30, "The hot-spring
terraces and their pools".
"""

from __future__ import annotations

from collections.abc import Mapping, MutableMapping
from typing import TYPE_CHECKING

import numpy as np

from mapgen.gamedata.materials import mesh_materials
from satisfactory_mcp.core.arrays import BoolMask, F32Grid, I64Grid
from satisfactory_mcp.core.gameassets import staticmesh
from satisfactory_mcp.core.gameassets.iostore import IoStore
from satisfactory_mcp.core.gameassets.packages import AssetIndex, PackageView, ScriptObjects

if TYPE_CHECKING:
    from mapgen.terrain.render_meshes import MeshGroup

__all__ = ["POOL_MATERIAL_MARK", "POOL_SUFFIX", "TERRACE_POOL", "pool_masks", "split_pools"]

#: A terrace texel's code above its class where a pool's surface is the top.
TERRACE_POOL = 1
#: The terraces' water materials: ``Water_Plateau``, ``_Plateau2``, ``_Plateau3``, ``_Origin``.
POOL_MATERIAL_MARK = "/HotSpring/Material/Water_"
#: The key a terrace's pools are drawn under, after the mesh's own.
POOL_SUFFIX = "#pools"


def pool_triangles(view: PackageView, tris: I64Grid) -> BoolMask | None:
    """Which of ``tris`` belong to a pool section of the mesh in ``view``; None where the mesh
    has no pool or ``tris`` are not its LOD 0, whose sections name the materials."""
    export = staticmesh.static_mesh_export(view)
    if export is None:
        return None
    tail = staticmesh.render_tail(view, export)
    parsed = staticmesh.parse_render_data(tail)
    got = staticmesh.lod0_buffers(tail, parsed)
    if got is None or got[1].shape != tris.shape or not np.array_equal(got[1], tris):
        return None
    materials = mesh_materials(view, export)
    mask = np.zeros(len(tris), bool)
    for section in parsed["lods"][0].sections:
        material = materials[section.material] if section.material < len(materials) else None
        if material and POOL_MATERIAL_MARK in material:
            first = section.first_index // 3
            mask[first : first + section.triangles] = True
    return mask if mask.any() else None


def pool_masks(
    store: IoStore,
    scripts: ScriptObjects,
    index: AssetIndex,
    shapes: Mapping[str, tuple[F32Grid, I64Grid]],
) -> dict[str, BoolMask]:
    """Per hot-spring mesh among ``shapes`` with pools, its pool triangles."""
    masks: dict[str, BoolMask] = {}
    for mesh, (_verts, tris) in shapes.items():
        package = index.path_for(mesh) if "/HotSpring/" in mesh else None
        if not package:
            continue
        try:
            mask = pool_triangles(PackageView(store.read_path(package), scripts), tris)
        except staticmesh.ParseError:
            mask = None
        if mask is not None:
            masks[mesh] = mask
    return masks


def split_pools(
    items: MutableMapping[str, MeshGroup],
    shapes: MutableMapping[str, tuple[F32Grid, I64Grid]],
    masks: Mapping[str, BoolMask],
    shift: int,
) -> dict[str, int]:
    """Each masked mesh's pools drawn apart, under ``mesh + POOL_SUFFIX``, its instances' codes
    with ``TERRACE_POOL`` ``shift``ed above the class; the pool triangles per mesh leaf."""
    counts: dict[str, int] = {}
    for mesh, mask in masks.items():
        group = items.get(mesh)
        if group is None:
            continue
        verts, tris = shapes[mesh]
        shapes[mesh] = (verts, tris[~mask])
        shapes[mesh + POOL_SUFFIX] = (verts, tris[mask])
        codes = group.codes | np.uint16(TERRACE_POOL << shift)
        items[mesh + POOL_SUFFIX] = group._replace(codes=codes)
        counts[mesh.rsplit("/", 1)[-1]] = int(mask.sum())
    return counts

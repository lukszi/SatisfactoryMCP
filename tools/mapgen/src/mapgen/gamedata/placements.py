"""A placed mesh: its row in the sweep, its transform, and the culls by owner, name, arch, size."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import TypeVar, cast

import numpy as np
from numpy.typing import NDArray

from satisfactory_mcp.core.arrays import F32Grid, F64Grid, I32Grid

__all__ = [
    "ARCH_MARK",
    "EXCLUDED_MESHES",
    "EXCLUDED_OWNERS",
    "OVERSIZE_CM",
    "PLACEMENT_LOCATION",
    "PLACEMENT_MESH",
    "PLACEMENT_OWNER",
    "PLACEMENT_ROTATION",
    "PLACEMENT_SCALE",
    "arch_mesh_ids",
    "cliff_cull",
    "is_arch",
    "mesh_name",
    "placement_material",
    "placement_matrix4",
    "placement_transform",
    "rotation_matrix",
]

FloatT = TypeVar("FloatT", np.float32, np.float64)


#: One row of ``sweep["placements"]``: mesh id, owner id, location (cm), pitch, yaw and roll
#: (degrees), scale.
PLACEMENT_MESH = 0
PLACEMENT_OWNER = 1
PLACEMENT_LOCATION = slice(2, 5)
PLACEMENT_ROTATION = slice(5, 8)
PLACEMENT_SCALE = slice(8, 11)

#: Actors whose meshes must never enter the field. The resource-node mesh is the whole list
#: and the reason is circularity: the field is validated against the node table.
EXCLUDED_OWNERS = frozenset({"NodeMeshActor_C"})

#: A mesh basename containing this is an arch, and an arch is a roof: a max-Z fold would put
#: it over the ground beneath it, so it is dropped before the fold rather than masked after.
#: Masking after blanks a texel an arch won even where a real rock stood second in it.
ARCH_MARK = "Arc"

#: Scaled local extent past which a placement is scenery rather than terrain: the sky dome
#: and the ocean shells. 600 m is an order of magnitude above the largest real rock.
OVERSIZE_CM = 60000.0

#: Rock meshes kept out of every layer by name. CliffPillar_03 is passable in game.
EXCLUDED_MESHES = frozenset({"CliffPillar_03"})


def rotation_matrix(pitch: float, yaw: float, roll: float) -> F64Grid:
    """UE's ``FRotationMatrix``: rows are the local X, Y, Z axes in world space.

    Written out rather than composed from three rotations, because UE's order and sign
    conventions are its own.
    """
    p, y, r = np.radians([pitch, yaw, roll])
    sp, cp = np.sin(p), np.cos(p)
    sy, cy = np.sin(y), np.cos(y)
    sr, cr = np.sin(r), np.cos(r)
    return np.array(
        [
            [cp * cy, cp * sy, sp],
            [sr * sp * cy - cr * sy, sr * sp * sy + cr * cy, -sr * cp],
            [-(cr * sp * cy + sr * sy), cy * sr - cr * sp * sy, cr * cp],
        ]
    )


def placement_transform(
    row: F64Grid, dtype: type[FloatT]
) -> tuple[NDArray[FloatT], NDArray[FloatT], NDArray[FloatT]]:
    """``(rotation, scale, location)`` of a placement row: world = (local * scale) @ rotation
    + location."""
    pitch, yaw, roll = (float(v) for v in row[PLACEMENT_ROTATION])
    return (
        rotation_matrix(pitch, yaw, roll).astype(dtype),
        row[PLACEMENT_SCALE].astype(dtype),
        row[PLACEMENT_LOCATION].astype(dtype),
    )


def placement_matrix4(row: F64Grid) -> F64Grid:
    """The placement as one 4x4 row-vector matrix, scale folded into the rotation rows."""
    pitch, yaw, roll = (float(v) for v in row[PLACEMENT_ROTATION])
    matrix = np.eye(4, dtype=np.float64)
    matrix[:3, :3] = rotation_matrix(pitch, yaw, roll) * row[PLACEMENT_SCALE][:, None]
    matrix[3, :3] = row[PLACEMENT_LOCATION]
    return matrix


def mesh_name(mesh: str) -> str:
    """A mesh path's last component, which the name rules and the sidecars key on."""
    return mesh.rsplit("/", 1)[-1]


def is_arch(mesh: str) -> bool:
    return ARCH_MARK in mesh_name(mesh)


def arch_mesh_ids(meshes: Sequence[str]) -> set[int]:
    """The ids, into ``sweep["meshes"]``, of every arch mesh."""
    return {i for i, mesh in enumerate(meshes) if is_arch(mesh)}


def cliff_cull(
    row: F64Grid,
    meshes: Sequence[str],
    owners: Sequence[str],
    geometry: Mapping[str, tuple[F32Grid, *tuple[object, ...]]],
    arch_ids: set[int],
) -> str | None:
    """Why the cliff layer drops this placement, or ``None`` to keep it.

    The reasons in the order they cost least, spelled as the ``dropped`` counters are keyed.
    """
    mesh_id = int(row[PLACEMENT_MESH])
    mesh = meshes[mesh_id]
    if owners[int(row[PLACEMENT_OWNER])] in EXCLUDED_OWNERS:
        return "owner"
    if mesh_name(mesh) in EXCLUDED_MESHES:
        return "excluded_mesh"
    if mesh not in geometry:
        return "no_geometry"
    if mesh_id in arch_ids:
        return "arch"
    scale = row[PLACEMENT_SCALE].astype(np.float32)
    if float(np.abs(geometry[mesh][0] * scale).max()) > OVERSIZE_CM:
        return "oversize"
    return None


def placement_material(sweep: Mapping[str, object], row: int) -> str | None:
    """The override material the sweep recorded for placement ``row``, if any."""
    chosen = cast("I32Grid | None", sweep.get("placement_materials"))
    pick = int(chosen[row]) if chosen is not None and row < len(chosen) else -1
    return cast("list[str]", sweep.get("materials", []))[pick] if pick >= 0 else None

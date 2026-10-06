"""A placed mesh: its rotation, and the culls by owner, mesh name, arch and size."""

from __future__ import annotations

import numpy as np

__all__ = [
    "ARCH_MARK",
    "EXCLUDED_MESHES",
    "EXCLUDED_OWNERS",
    "OVERSIZE_CM",
    "placement_material",
    "rotation_matrix",
]


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


def rotation_matrix(pitch: float, yaw: float, roll: float) -> np.ndarray:
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


def placement_material(sweep: dict, row: int) -> str | None:
    """The override material the sweep recorded for placement ``row``, if any."""
    chosen = sweep.get("placement_materials")
    pick = int(chosen[row]) if chosen is not None and row < len(chosen) else -1
    return sweep.get("materials", [])[pick] if pick >= 0 else None

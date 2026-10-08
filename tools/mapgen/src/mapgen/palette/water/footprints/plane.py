"""The run's land plane for the render-only meshes, and its reading at each pixel of a piece.

The seabed rule kept coral, shells and terraces pixel by pixel where the water plane is dry,
and that plane's outline is the artwork's 3.66 m mask, so it cut reefs into walls along it.
Here the footprint decides: the mesh texels any layer draws, joined 8-wise on the field's
1 m grid, are land together where one of them stands on land, and left to the seabed together
where all stand in the sea. docs/map/painted.md section 27, "Whole footprints".
"""

from __future__ import annotations

import numpy as np
from scipy import ndimage

from mapgen.cache import MeshPlanes
from mapgen.gamedata.frame import BOUNDS_M
from mapgen.jit import gpu_on
from mapgen.palette.water.footprints import reference
from mapgen.palette.water.footprints.reference import (
    CORAL_GROUP,
    SEA_MARK,
    TERRACE_GROUP,
    AxisCover,
    TexelPlanes,
    axis_cover,
    nearest_texel,
)
from mapgen.terrain.sample import frame_coordinates, grid_position
from satisfactory_mcp.core.arrays import BoolMask, F32Grid, F64Grid, U8Grid
from satisfactory_mcp.core.jsontypes import JsonObject
from satisfactory_mcp.domain.spatial import heightfield as hf

__all__ = ["CHUNK_ROWS", "TexelPlanes", "mesh_land", "piece_land"]

#: Texel rows marked at a time: at full size about 280 rows of the mesh raster, 120 MB.
CHUNK_ROWS = 64

#: Each group and its name in the record.
_GROUP_NAMES = ((CORAL_GROUP, "coral_and_shells"), (TERRACE_GROUP, "terraces"))


def mesh_land(
    meshes: MeshPlanes, field: hf.Field, size: int, texels: TexelPlanes
) -> tuple[U8Grid, JsonObject]:
    """The land plane on the field's grid, and what it decided.

    Each texel of a footprint on land carries its group's bit (``reference.GROUPS``).
    ``meshes`` is the run's mesh raster, a ``size`` square on the frame, and ``texels`` the
    field's planes each texel is marked from.
    """
    marks = _marks(meshes, field, size, texels)
    land = np.zeros(marks.shape, np.uint8)
    record: JsonObject = {"connectivity": 8, "texel_m": field.spacing_cm / 100.0}
    for group, name in _GROUP_NAMES:
        record[name] = _decide(marks, group, land)
    return land, record


def piece_land(
    meshes: MeshPlanes, cut: tuple[slice, slice], field_y: F64Grid, field_x: F64Grid
) -> BoolMask | None:
    """Whether each pixel of a piece is a mesh whose footprint stands on land, read at its
    nearest texel; None without the land plane. ``cut`` is the piece's rows and columns of the
    mesh raster, ``field_y`` and ``field_x`` its pixels' positions on the field's axes."""
    if meshes.land is None:
        return None
    cls = np.asarray(meshes.cls[cut], np.uint8)
    rows, cols = nearest_texel(field_y), nearest_texel(field_x)
    if gpu_on():
        from mapgen.palette.water.footprints import gpu

        return gpu.pixel_land(meshes.land, rows, cols, cls)
    return reference.pixel_land(meshes.land, rows, cols, cls)


def _marks(meshes: MeshPlanes, field: hf.Field, size: int, texels: TexelPlanes) -> U8Grid:
    """Every texel's marks (``reference.texel_marks``), ``CHUNK_ROWS`` texel rows at a time."""
    x_cm, y_cm = frame_coordinates(size)
    spacing = field.spacing_cm
    y = grid_position(y_cm, field.y0_cm, spacing, field.height)
    x = grid_position(x_cm, field.x0_cm, spacing, field.width)
    rows = axis_cover(y, _half_pixel("y", size, spacing), field.height)
    cols = axis_cover(x, _half_pixel("x", size, spacing), field.width)
    marks = np.empty((field.height, field.width), np.uint8)
    for top in range(0, field.height, CHUNK_ROWS):
        part = slice(top, min(top + CHUNK_ROWS, field.height))
        first = int(rows.start[part].min())
        last = max(int(rows.stop[part].max()), first)
        local = AxisCover(rows.start[part] - first, rows.stop[part] - first)
        cls = np.asarray(meshes.cls[first:last], np.uint8)
        z_cm = np.asarray(meshes.z_cm[first:last], np.float32)
        marks[part] = _texel_marks(cls, z_cm, local, cols, texels.rows(part))
    return marks


def _half_pixel(axis: str, size: int, spacing_cm: float) -> float:
    """Half a pixel of a ``size`` square on the frame along ``axis``, in texels."""
    extent_m = BOUNDS_M[f"{axis}_max_m"] - BOUNDS_M[f"{axis}_min_m"]
    return extent_m * 100.0 / size / 2.0 / spacing_cm


def _texel_marks(
    cls: U8Grid, z_cm: F32Grid, rows: AxisCover, cols: AxisCover, texels: TexelPlanes
) -> U8Grid:
    if gpu_on():
        from mapgen.palette.water.footprints import gpu

        return gpu.texel_marks(cls, z_cm, rows, cols, texels)
    return reference.texel_marks(cls, z_cm, rows, cols, texels)


def _decide(marks: U8Grid, group: int, land: U8Grid) -> JsonObject:
    """``group``'s drawn texels labelled into footprints, 8-wise, and its bit set in ``land``
    over every footprint any texel of which stands on land; the counts."""
    drawn = (marks & group) != 0
    labels, count = ndimage.label(drawn, structure=np.ones((3, 3), bool))
    sea = (marks & SEA_MARK) != 0
    on_land = np.zeros(count + 1, bool)
    on_land[labels[drawn & ~sea]] = True
    at_sea = np.zeros(count + 1, bool)
    at_sea[labels[drawn & sea]] = True
    kept = on_land[labels]
    land[kept] |= np.uint8(group)
    return {
        "footprints": int(count),
        "on_land": int(on_land.sum()),
        "on_land_and_in_the_sea": int((on_land & at_sea).sum()),
        "in_the_sea": int((at_sea & ~on_land).sum()),
        "texels": int(drawn.sum()),
        "texels_kept_in_the_sea": int((kept & sea).sum()),
    }

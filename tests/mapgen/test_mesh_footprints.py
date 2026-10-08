"""Coral, shells and hot-spring terraces are kept or left to the seabed a whole footprint at a
time, never cut at the water plane's outline.

docs/map/painted.md section 27, "Whole footprints". Synthetic fixtures: no install, no field
on disk. The CUDA twins' tests skip, saying why, where numba, CuPy or a device is missing.
"""

from __future__ import annotations

from types import SimpleNamespace

import numpy as np
import pytest

from mapgen import jit
from mapgen.cache import MeshPlanes
from mapgen.gamedata.frame import BOUNDS_M
from mapgen.palette.painted.band import painted_ndl
from mapgen.palette.water.footprints import reference
from mapgen.palette.water.footprints.plane import mesh_land, piece_land
from mapgen.palette.water.footprints.reference import (
    CORAL_GROUP,
    SEA_MARK,
    TERRACE_GROUP,
    AxisCover,
    TexelPlanes,
    axis_cover,
    nearest_texel,
)
from mapgen.palette.water.shore import OCEAN_LEVEL_M, seabed_keeps
from mapgen.terrain.render_meshes import MESH_CORAL, MESH_ROCK, MESH_SHELL, MESH_TERRACE
from satisfactory_mcp.domain.spatial import heightfield as hf
from tests.support.draw import render_layer

#: One texel of the field a pixel.
N = 96
SEA_DM = round(OCEAN_LEVEL_M * hf.DM_PER_M)
LAKE_DM = 50

#: pytest puts its own filters before ``mapgen.jit``'s, which quiets this one in a render.
pytestmark = pytest.mark.filterwarnings("ignore:CUDA path could not be detected:UserWarning")

#: The scene's footprints: rows, columns, class and top in metres.
SHORE_CORAL = ((8, 20), (26, 40), MESH_CORAL, 8.0)
SEA_CORAL = ((40, 52), (56, 70), MESH_SHELL, -15.0)
SHORE_TERRACE = ((72, 78), (3, 12), MESH_TERRACE, 6.0)
LAKE_TERRACE = ((82, 88), (12, 26), MESH_TERRACE, 5.5)
LAND_CORAL = ((83, 87), (26, 29), MESH_CORAL, 7.0)
FOOTPRINTS = (SHORE_CORAL, SEA_CORAL, SHORE_TERRACE, LAKE_TERRACE, LAND_CORAL)


def _scene() -> SimpleNamespace:
    """Land to the west at 3 m, the sea 20 m deep east of column 32 with a patch the artwork
    calls dry inside it, a hot-spring lake at 5 m in the land, and the footprints over them."""
    rows, cols = np.mgrid[0:N, 0:N]
    sea = cols >= 32
    patch = (rows >= 40) & (rows < 52) & (cols >= 56) & (cols < 62)
    lake = (rows >= 70) & (rows < 90) & (cols >= 5) & (cols < 26)
    height = np.where(sea, -200, 30).astype(np.int16)
    wet = (sea & ~patch) | lake
    water = np.where(sea & ~patch, SEA_DM, np.where(lake, LAKE_DM, hf.NODATA)).astype(np.int16)
    grades = np.where(wet, hf.WATER_MEASURED, hf.WATER_DRY).astype(np.uint8)
    step_cm = (BOUNDS_M["x_max_m"] - BOUNDS_M["x_min_m"]) * 100 / N
    field = SimpleNamespace(
        height_dm=height, provenance_plane=np.ones((N, N), np.uint8),
        water_raster=lambda: water, water_quality_raster=lambda: grades,
        x0_cm=BOUNDS_M["x_min_m"] * 100 + step_cm / 2,
        y0_cm=BOUNDS_M["y_min_m"] * 100 + step_cm / 2, spacing_cm=step_cm, width=N, height=N,
    )  # fmt: skip
    mesh_z = np.zeros((N, N), np.float32)
    cls = np.zeros((N, N), np.uint8)
    for (r0, r1), (c0, c1), kind, top_m in FOOTPRINTS:
        mesh_z[r0:r1, c0:c1], cls[r0:r1, c0:c1] = top_m * 100.0, kind
    reach = (cols >= 28).astype(np.uint8)
    texels = TexelPlanes(wet.astype(np.uint8), reach, height.astype(np.float32), water)
    return SimpleNamespace(field=field, meshes=MeshPlanes(mesh_z, cls), texels=texels)


def _box(footprint, inset: int = 0) -> tuple[slice, slice]:
    (r0, r1), (c0, c1) = footprint[:2]
    return slice(r0 + inset, r1 - inset), slice(c0 + inset, c1 - inset)


def test_a_texel_is_sea_where_wet_or_under_the_ocean_within_its_reach():
    """The artwork's dry patch under the sea's level is sea within the ocean's reach only."""
    wet = np.array([[1, 0, 0, 0, 0, 0, 1]], np.uint8)
    reach = np.array([[1, 1, 1, 0, 1, 1, 0]], np.uint8)
    ground = np.array([[-200, -200, -200, -200, -160, hf.NODATA, 30]], np.float32)
    level = np.array([[SEA_DM] + [hf.NODATA] * 5 + [LAKE_DM]], np.int16)
    top_cm = np.array([[-1700, -1730, -1780, -1780, -1780, -1780, 450]], np.float32)
    cls = np.array([[MESH_CORAL] * 6 + [MESH_TERRACE]], np.uint8)
    one = AxisCover(np.arange(7), np.arange(1, 8))
    marks = reference.texel_marks(
        cls, top_cm, AxisCover(np.array([0]), np.array([1])), one,
        TexelPlanes(wet, reach, ground, level),
    )  # fmt: skip
    sea, coral, terrace = SEA_MARK, CORAL_GROUP, TERRACE_GROUP
    assert marks.tolist() == [[sea | coral, sea | coral, sea, coral, coral, coral, sea | terrace]]


def test_a_footprint_on_land_is_kept_whole_and_one_in_the_sea_left_whole():
    scene = _scene()
    land, record = mesh_land(scene.meshes, scene.field, N, scene.texels)
    for footprint, kept in ((SHORE_CORAL, CORAL_GROUP), (SEA_CORAL, 0), (LAND_CORAL, CORAL_GROUP)):
        assert (land[_box(footprint)] == kept).all(), footprint
    assert (land[_box(SHORE_TERRACE)] == TERRACE_GROUP).all(), "a terrace on its lake's rim"
    assert (land[_box(LAKE_TERRACE)] == 0).all(), "one in the lake, beside coral on land"
    assert (land[np.asarray(scene.meshes.cls) == 0] == 0).all()
    coral, terraces = record["coral_and_shells"], record["terraces"]
    assert (coral["footprints"], coral["on_land"], coral["in_the_sea"]) == (3, 2, 1)
    assert coral["on_land_and_in_the_sea"] == 1 and coral["texels_kept_in_the_sea"] == 12 * 8
    assert (terraces["footprints"], terraces["on_land_and_in_the_sea"]) == (2, 1)
    assert terraces["in_the_sea"] == 1


class _Surface:
    """What the pass hands the light: the heights the seabed rule draws."""

    def __init__(self) -> None:
        self.z = np.full((N, N), np.nan, np.float32)

    def put(self, row, z_m, land, columns=slice(None), slabs=None):
        self.z[row : row + len(z_m), columns] = z_m


def _seabed_surface(scene: SimpleNamespace, meshes: MeshPlanes) -> np.ndarray:
    surface = _Surface()
    borrow = (np.broadcast_to(np.int8(0), (8192, 8192)), np.zeros((N, N), np.uint8))
    render_layer(
        "terrain", scene.field, 1, borrow, N, False,
        scene.texels.ground, meshes=meshes, reach=scene.texels.reach, unlit=True,
        surface=surface,
    )  # fmt: skip
    return surface.z


def test_the_seabed_rule_draws_each_footprint_whole():
    scene = _scene()
    land, _record = mesh_land(scene.meshes, scene.field, N, scene.texels)
    whole = _seabed_surface(scene, scene.meshes._replace(land=land))
    cut = _seabed_surface(scene, scene.meshes)
    shore, at_sea = _box(SHORE_CORAL, 2), _box(SEA_CORAL, 2)
    assert (whole[shore] > 7.0).all(), "the shore coral stands whole, its sea half too"
    assert (cut[shore][:, -4:] < -10.0).all(), "where the water plane's rule cut it"
    assert (whole[at_sea] < -19.0).all(), "the sea coral is left whole to the seabed"
    patch = (slice(42, 50), slice(58, 60))
    assert (cut[patch] > -16.0).all(), "where the artwork's dry patch kept a piece of it"


def test_the_seabed_rule_follows_the_footprint_and_rocks_keep_theirs():
    level = np.array([[OCEAN_LEVEL_M, OCEAN_LEVEL_M, np.nan, np.nan]], np.float32)
    top = np.array([[-16.0, -18.0, -16.0, 5.0]], np.float32)
    coral = np.full((1, 4), MESH_CORAL, np.uint8)
    rock = np.full((1, 4), MESH_ROCK, np.uint8)
    land = np.array([[True, False, False, True]])
    assert seabed_keeps(coral, top, level).tolist() == [[False, False, True, True]]
    assert seabed_keeps(coral, top, level, land).tolist() == land.tolist()
    assert seabed_keeps(rock, top, level, land).tolist() == [[True, False, True, True]]


def test_painted_keeps_its_own_sun_only_on_meshes_left_to_the_seabed():
    z = (np.arange(81, dtype=np.float32).reshape(9, 9) // 9) - np.float32(15.0)
    weight = np.ones(z.shape, np.float32)
    kept = np.full(z.shape, MESH_CORAL, np.uint8)
    sea = np.full(z.shape, OCEAN_LEVEL_M, np.float32)
    flat = painted_ndl(z, 1.0, True, (None, None, sea, None))
    in_sea = painted_ndl(z, 1.0, True, (weight, kept, sea, np.zeros(z.shape, bool)))
    on_land = painted_ndl(z, 1.0, True, (weight, kept, sea, np.ones(z.shape, bool)))
    assert not np.array_equal(in_sea, flat), "left to the seabed, it keeps its own sun"
    np.testing.assert_array_equal(on_land, flat)


def test_every_pixel_folds_onto_texels_its_nearest_among_them():
    for size, texels in ((7, 23), (64, 23), (23, 23)):
        position = np.clip((np.arange(size) + 0.5) * texels / size - 0.5, 0, texels - 1)
        half = texels / size / 2
        cover = axis_cover(position, half, texels)
        nearest = nearest_texel(position)
        for pixel, texel in enumerate(nearest):
            assert cover.start[texel] <= pixel < cover.stop[texel], (size, pixel)
        assert (cover.start < cover.stop).all(), f"{size}: every texel has a pixel"
        if half >= 0.5:
            assert (cover.stop - cover.start <= 2).all(), "a coarse pixel lands on each it covers"


def test_a_piece_reads_its_texels_at_their_nearest():
    land = np.zeros((4, 4), np.uint8)
    land[1, 2], land[2, 1:3] = CORAL_GROUP, TERRACE_GROUP
    cls = np.array([[MESH_CORAL, MESH_TERRACE], [MESH_TERRACE, MESH_ROCK]], np.uint8)
    meshes = MeshPlanes(np.zeros((2, 2), np.float32), cls, None, land)
    got = piece_land(meshes, (slice(0, 2), slice(0, 2)), np.array([1.2, 1.6]), np.array([2.4, 0.9]))
    assert got is not None and got.tolist() == [[True, False], [True, False]]
    unread = meshes._replace(land=None)
    assert piece_land(unread, (slice(0, 2),) * 2, np.zeros(2), np.zeros(2)) is None


# ------------------------------------------------------------------------------ the GPU


@pytest.fixture(scope="module")
def device() -> None:
    """The CUDA kernels' preconditions, checked once a worker: numba, CuPy, a device."""
    problem = jit.gpu_problem()
    if problem is not None:
        pytest.skip(problem)


def _random_marks_inputs(seed: int, size: int, texels: int):
    rng = np.random.default_rng(seed)
    position = np.clip((np.arange(size) + 0.5) * texels / size - 0.5, 0, texels - 1)
    cover = axis_cover(position, texels / size / 2, texels)
    cls = rng.choice(np.array([0, MESH_CORAL, MESH_SHELL, MESH_ROCK, MESH_TERRACE], np.uint8),
                     (size, size + 3))  # fmt: skip
    z_cm = rng.normal(-1700, 120, (size, size + 3)).astype(np.float32)
    z_cm[rng.random(z_cm.shape) < 0.05] = np.nan
    cols = axis_cover(np.clip(position, 0, texels - 1), texels / size / 2, texels)
    wet = (rng.random((texels, texels)) < 0.5).astype(np.uint8)
    reach = (rng.random((texels, texels)) < 0.7).astype(np.uint8)
    ground = rng.normal(-170, 20, (texels, texels)).astype(np.float32)
    ground[rng.random(ground.shape) < 0.1] = hf.NODATA
    level = rng.normal(-170, 8, (texels, texels)).astype(np.int16)
    level[rng.random(level.shape) < 0.1] = hf.NODATA
    return cls, z_cm, cover, cols, TexelPlanes(wet, reach, ground, level)


@pytest.mark.usefixtures("device")
@pytest.mark.parametrize(("seed", "size", "texels"), [(1, 300, 70), (2, 40, 130), (3, 129, 129)])
def test_the_texel_marks_are_the_reference_bit_for_bit(seed, size, texels):
    from mapgen.palette.water.footprints import gpu

    cls, z_cm, rows, cols, planes = _random_marks_inputs(seed, size, texels)
    expected = reference.texel_marks(cls, z_cm, rows, cols, planes)
    got = gpu.texel_marks(cls, z_cm, rows, cols, planes)
    assert (got.dtype, got.shape) == (expected.dtype, expected.shape)
    assert got.tobytes() == expected.tobytes()
    assert len(np.unique(expected)) > 4, "the fixture reaches every mark"


@pytest.mark.usefixtures("device")
def test_a_piece_s_land_is_the_reference_bit_for_bit(monkeypatch):
    rng = np.random.default_rng(4)
    land = rng.integers(0, 4, (90, 200), np.uint8)
    cls = rng.choice(np.array([0, MESH_CORAL, MESH_SHELL, MESH_ROCK, MESH_TERRACE], np.uint8),
                     (60, 131))  # fmt: skip
    meshes = MeshPlanes(np.zeros(cls.shape, np.float32), cls, None, land)
    cut = (slice(0, 60), slice(0, 131))
    field_y, field_x = np.linspace(10.2, 40.7, 60), np.linspace(33.0, 170.4, 131)
    monkeypatch.setenv(jit.KERNEL_SWITCH, jit.REFERENCE)
    expected = piece_land(meshes, cut, field_y, field_x)
    monkeypatch.setenv(jit.KERNEL_SWITCH, jit.GPU)
    assert jit.gpu_on()
    got = piece_land(meshes, cut, field_y, field_x)
    assert got is not None and expected is not None
    assert (got.dtype, got.shape) == (expected.dtype, expected.shape)
    assert got.tobytes() == expected.tobytes() and expected.any() and not expected.all()


@pytest.mark.usefixtures("device")
def test_a_call_the_device_has_no_memory_for_runs_the_reference(monkeypatch):
    from mapgen.palette.water.footprints import gpu

    def out_of_memory(*_args: object) -> None:
        raise MemoryError("out of device memory")

    cls, z_cm, rows, cols, planes = _random_marks_inputs(5, 50, 40)
    monkeypatch.setattr(gpu, "_texel_marks", out_of_memory)
    got = gpu.texel_marks(cls, z_cm, rows, cols, planes)
    assert got.tobytes() == reference.texel_marks(cls, z_cm, rows, cols, planes).tobytes()

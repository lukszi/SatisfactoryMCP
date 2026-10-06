"""Where ``tools/gen_map_image.py`` and ``tools/gen_map_renders.py`` meet the server.

A generator writes a tree and a sidecar; ``/api/maptiles`` reads them back. Nothing else
joins the two -- no shared module, no schema -- so the join is a set of file names, key names
and one piece of arithmetic, and it is asserted here against the tools' OWN output rather
than against a hand-typed sample that could drift away from what they really write.

Split out of ``test_web_tiles.py`` rather than living beside the routes: this is the tools
side of the contract, it needs numpy and scipy where the route tests need neither, and
keeping the two apart is what keeps either file readable.

``importorskip`` at module scope, not a marker: half of these tests drive the endpoint
through the ``client`` fixture, so the optional ``web`` extra is needed to collect the file.
The tree builders come from ``test_web_tiles`` -- ONE definition, and it is the one built
through the server's own directory names.
"""

from __future__ import annotations

import json
import types

import pytest

fastapi = pytest.importorskip("fastapi")

from test_web_tiles import _PNG, _fake_pyramid

from mapgen.cache import (
    DIRECT_CACHE_SIDECAR,
    DIRECT_COVERAGE_NAME,
    DIRECT_Z_NAME,
    cached_direct,
    direct_cache_stamp,
)
from mapgen.common import LOCAL_DIR, RENDERS_DIR_NAME
from mapgen.enhance.pixels import (
    COLOUR_FIX_SIGMA,
    FAINT_HI,
    FAINT_LO,
    PRESHARPEN_AMOUNT,
    PRESHARPEN_HI,
    PRESHARPEN_ROUNDS,
    colour_fix_pixels,
    faint_band,
    faint_depth,
    faint_mask,
    presharpen_mask,
    presharpen_pixels,
)
from mapgen.enhance.upscaler import (
    ENHANCE_MODEL,
    ENHANCE_OVERLAP_PX,
    ENHANCE_SCALE,
    ENHANCE_SHA256,
    ENHANCE_TILE_PX,
    ENHANCE_URL,
)
from mapgen.gamedata.frame import BOUNDS_M, RENDER_2X_PX, RENDER_PX
from mapgen.gamedata.mesh import EXCLUDED_OWNERS
from mapgen.lighting.hillshade import (
    SHADE_FLOOR,
    SHADE_RANGE,
    WATER_SHADE_FLOOR,
    WATER_SHADE_RANGE,
    hillshade,
)
from mapgen.palette.styles import (
    BIOME_COLOURS,
    NO_MANS_LAND_RGB,
    SEA_RGB,
    UNKNOWN_BIOME_RGB,
    WATER_DEEP,
    WATER_SHALLOW,
    terrain_colours,
    with_sea,
)
from mapgen.palette.water import (
    WATER_EDGE_M,
    water_alpha,
    water_depth_fraction,
    water_over,
    water_planes,
)
from mapgen.pipeline import LAYERS
from mapgen.terrain.fill import (
    SOURCE_HOLE,
    SOURCE_NONE,
    SOURCE_ROCK,
    SOURCE_SEAM,
    fill_field,
    ground_lattice,
    terrain_lattice,
)
from mapgen.terrain.measure import SEAM_MID, SEAM_SWITCH_CEILING, RegimeCoverage, SeamTrace
from mapgen.terrain.rasters import (
    direct_placements,
    pixel_coverage,
    rasterise_direct_band,
    rasterise_top_band,
    reduce_direct,
)
from mapgen.terrain.sample import direct_weight, sample_surface, taps_cubic, taps_linear, taps_pchip
from mapgen.tiles import artwork_output
from mapgen.tiles import sidecar as render_sidecar
from mapgen.tiles.artwork_output import (
    IMAGE_NAME,
    SIDECAR_NAME,
    enhancement_downgrades,
    pinned_build,
    pinned_enhanced,
    pinned_recipe,
)
from mapgen.tiles.compose import DIRECT_LIFT_KNEE_M, blend_regimes, composite_top
from mapgen.tiles.recipes import ENHANCE_RECIPE, ENHANCE_RECIPES, UNNUMBERED_RECIPE
from mapgen.tiles.sidecar import RENDER_SIDECAR_NAME, pinned_field_build
from satisfactory_mcp import config
from satisfactory_mcp.core.gameassets.container import SHEET_PX, UBULK_BYTES
from satisfactory_mcp.core.gameassets.pyramid import (
    PYRAMID_TILE_2X_PX,
    PYRAMID_TILE_PX,
    TILES_2X_DIR_NAME,
    TILES_DIR_NAME,
    TILES_RETIRED,
    TILES_STAGING,
    PyramidError,
    enhanced_top_z,
    install_pyramid,
    merge_enhanced,
    pyramid_top_z,
    tile_relpath,
)
from satisfactory_mcp.domain.maps import presets, registry
from satisfactory_mcp.domain.spatial import heightfield as hf
from satisfactory_mcp.interfaces.web.routers import tiles as web_tiles


def test_the_render_generator_writes_where_the_layered_route_looks(tmp_path, monkeypatch):
    """``tools/gen_map_renders.py`` and this endpoint agree about names, or nothing works.

    Nothing else joins the two, so the join is asserted against the tool's own constants and
    its own sidecar builder -- the same posture the artwork's sidecar test takes next door.
    Four names have to match, and the shape of the record the endpoint reads back has to be
    the one the tool actually writes rather than a hand-typed sample that could drift.
    """
    assert RENDERS_DIR_NAME == web_tiles.MAP_RENDERS_DIR_NAME
    assert RENDER_SIDECAR_NAME == web_tiles.MAP_RENDER_SIDECAR_NAME
    assert set(LAYERS) == set(presets.RENDER_LAYERS)
    assert {layer for layer in registry.LEGACY if layer != web_tiles.MAP_LAYER_DEFAULT} <= set(
        LAYERS
    )
    assert BOUNDS_M == web_tiles.DEFAULT_MAP_BOUNDS_M
    # The tile grid is the cutter's, not this generator's: it hands its sheet to
    # ``core.gameassets.pyramid`` and the endpoint has to be configured for what THAT cuts.
    # The tile SIZE and the directory names are no longer asserted equal, because the
    # endpoint imports them from the cutter and an assertion that a name equals itself
    # cannot fail. What is still worth pinning is the arithmetic, which is a real claim
    # about two different sheets: the default depth stays z5, which is what an 8192 sheet
    # divides into and what a pyramid whose sidecar says nothing is assumed to be, while
    # the renders are 32768 and say so in their own sidecar.
    assert pyramid_top_z(SHEET_PX) == web_tiles.MAP_TILE_MAX_Z == 5
    assert pyramid_top_z(RENDER_PX) == 7
    # And the @2x tree is NOT simply one level shallower any more, which is the one place
    # the two trees stopped being the same arithmetic. 512 * 2**z runs out of a 32768 sheet
    # at z6, and cutting it there would cost as much again as the whole 1x pyramid for
    # pixels a retina client gets by asking for the 1x tile one level deeper -- so the dense
    # tree is cut from RENDER_2X_PX and stays exactly where it has always been, at z5.
    assert PYRAMID_TILE_2X_PX == 2 * PYRAMID_TILE_PX
    assert RENDER_2X_PX == RENDER_PX // 2
    assert pyramid_top_z(RENDER_2X_PX, PYRAMID_TILE_2X_PX) == 5
    assert pyramid_top_z(RENDER_PX, PYRAMID_TILE_2X_PX) == 6

    pin = "buildVersion 495413 (engine branch ++FactoryGame+rel-main-1.2.0), the installed build"
    sidecar = render_sidecar.build_sidecar(
        layer="terrain",
        field_meta={
            "generator": "tools/gen_world_heightmap.py",
            "sources": {"game": {"game_version_pinned": pin}},
        },
        tiles={
            "tile_px": 256,
            "max_z": 7,
            "count": 21845,
            "bytes": 900_000_000,
            "game_version_pinned": pin,
        },
        tiles_2x={"tile_px": 512, "max_z": 5, "count": 1365, "bytes": 240_000_000},
        render={"width_px": 32768},
        extra={},
    )
    assert pinned_field_build(sidecar) == pin
    assert pinned_field_build({}) is None
    assert pinned_field_build({"_meta": {"sources": {}}}) is None

    # The endpoint reads that file, unmodified, out of the place the tool writes it to.
    directory = tmp_path / registry.local_dir().name / web_tiles.MAP_RENDERS_DIR_NAME / "terrain"
    directory.mkdir(parents=True)
    (directory / web_tiles.MAP_RENDER_SIDECAR_NAME).write_text(
        json.dumps(sidecar), encoding="utf-8"
    )
    monkeypatch.setattr(config, "data_dir", lambda: tmp_path)
    read_back = web_tiles._map_pyramid("terrain")
    assert (read_back["tile_px"], read_back["max_z"]) == (256, 7)
    assert (read_back["tile_2x_px"], read_back["max_2x_z"]) == (512, 5)
    assert web_tiles._map_bounds("terrain") == web_tiles.DEFAULT_MAP_BOUNDS_M

    # A layer with no @2x block says so with None rather than with a zero, because zero is
    # a depth a real pyramid can have and "there is no such tree" is not a depth.
    plain = json.loads(json.dumps(sidecar))
    del plain["_meta"]["tiles_2x"]
    (directory / web_tiles.MAP_RENDER_SIDECAR_NAME).write_text(json.dumps(plain), encoding="utf-8")
    without = web_tiles._map_pyramid("terrain")
    assert (without["tile_2x_px"], without["max_2x_z"]) == (None, None)
    # ...and the two trees' numbers are in one cache tag, so recutting either changes both.
    assert without["build"] != read_back["build"]


def test_the_sun_is_in_the_north_west_and_the_shore_is_not_a_staircase():
    """The two rules of the render a wrong answer would still look like terrain.

    Hillshade first. An inverted light source draws every valley as a ridge and the picture
    is still a plausible relief map, so the direction is asserted on slopes whose answer is
    known by construction: a hill face tilted toward the north-west must come back brighter
    than the same face tilted toward the south-east, and a flat plain must sit between them.
    Rows run south and columns run east, which is the half of it that compass angles hide.

    Then the shore. ``submerged`` is a step function on a 1 m grid, so a hard composite
    draws every coastline as metre blocks; the feather has to be a real blend -- water where
    it is deep, ground where there is none, and strictly between the two in the band -- or
    it is decoration rather than the antialiasing it is there to be.
    """
    numpy = pytest.importorskip("numpy")
    pytest.importorskip("scipy")

    rows, cols = numpy.mgrid[0:9, 0:9].astype(numpy.float32)
    flat = hillshade(numpy.zeros((9, 9), numpy.float32), 1.0)
    # Ground falling away to the north-west: high in the south-east, so the face looks at
    # the sun. The opposite sign is the same slope turned away from it.
    toward = hillshade(rows + cols, 1.0)
    away = hillshade(-(rows + cols), 1.0)
    assert toward.mean() > flat.mean() > away.mean()
    assert away.min() >= SHADE_FLOOR, "a shadowed face keeps its colour, it does not go black"
    assert toward.max() <= SHADE_FLOOR + SHADE_RANGE + 1e-6
    # A north-facing slope and a west-facing one are lit alike; north-east and south-west
    # are the two the azimuth has to separate.
    assert hillshade(rows, 1.0).mean() == pytest.approx(hillshade(cols, 1.0).mean())

    # And the shore. A wide lake with a beach on one side: the depth feather has a band to
    # work in, so the coverage climbs through it rather than switching.
    ground = numpy.zeros((7, 40), numpy.float32)
    depth = numpy.linspace(-2.0, 6.0, 40, dtype=numpy.float32)
    water = numpy.broadcast_to(depth, (7, 40)).copy()
    wet = (water > ground).astype(numpy.float32)
    measured = numpy.ones_like(wet)
    alpha = water_alpha(ground, water, wet, measured, 0.8)
    assert alpha.min() == pytest.approx(0.0, abs=0.02), "dry ground is not tinted"
    assert alpha.max() == pytest.approx(1.0, abs=0.02), "open water is not half-painted"
    assert numpy.all(numpy.diff(alpha[3]) >= -1e-6), "coverage rises with depth, never falls"
    assert 0.05 < alpha[3][numpy.argmin(numpy.abs(depth - WATER_EDGE_M / 2))] < 0.95

    # A cliff into deep water has no depth band at all, and the spatial blur is what keeps
    # that edge from being a staircase: the pixels either side of it are partial.
    cliff = numpy.zeros((7, 40), numpy.float32)
    cliff[:, :20] = 50.0
    level = numpy.full((7, 40), 20.0, numpy.float32)
    hard = water_alpha(
        cliff, level, (cliff < 20.0).astype(numpy.float32), numpy.ones((7, 40), numpy.float32), 0.8
    )
    assert set(numpy.round(hard[3, :14], 3)) == {0.0} and hard[3, -1] == pytest.approx(
        1.0, abs=0.02
    )
    assert any(0.05 < value < 0.95 for value in hard[3, 18:22]), "the edge is antialiased"

    dry_rgb = numpy.full((7, 40, 3), 200.0, numpy.float32)
    shade = numpy.ones((7, 40), numpy.float32)
    shallow_rgb, deep_rgb = WATER_SHALLOW, WATER_DEEP
    tint = water_depth_fraction(ground, water, measured)
    out = water_over(dry_rgb, tint, alpha, shade, shallow_rgb, deep_rgb)
    assert (out[3, 0] == 200.0).all(), "ground above the water is untouched"
    # And where it IS water it is water and only water, tinted by its own depth.
    shallow = shallow_rgb * (WATER_SHADE_FLOOR + WATER_SHADE_RANGE)
    assert out[3, -1] == pytest.approx(shallow, abs=12.0)


def test_water_whose_depth_was_never_measured_is_still_drawn_as_water():
    """The rule that stopped 3.572 km2 of ocean being rendered as land.

    Over the fill province the ground under the water is a 3.9 m-quantised raster that
    routinely rounds ABOVE a sea surface 17 m down, so ``water_m - z_m`` there is a negative
    number and the depth feather run on it answers "no water". The quality byte exists to say
    that the level is known and the depth is not, and the two consequences are asserted here
    because both of them are invisible in a picture that is merely plausible: such a texel is
    drawn at **full alpha**, and it is tinted at the **deep** end rather than the shallow one.

    The second is a measurement rather than a preference -- 95.2% of level-only water on the
    shipped field stands over the fill province and 98% of its surface levels sit in a 0.7 m
    band around the ocean's own -16.99 m -- but what has to hold in code is only that the
    unknown depth is never run through the ramp.
    """
    numpy = pytest.importorskip("numpy")
    pytest.importorskip("scipy")

    # A sea surface at -17 over "ground" the fill layer rounded to -15: above the water.
    ground = numpy.full((7, 40), -15.0, numpy.float32)
    surface = numpy.full((7, 40), -17.0, numpy.float32)
    wet = numpy.ones((7, 40), numpy.float32)
    unknown = numpy.zeros((7, 40), numpy.float32)

    drowned = water_alpha(ground, surface, wet, unknown, 0.8)
    assert drowned.min() == pytest.approx(1.0, abs=1e-3), (
        "water whose depth is unknown is fully water; the comparison that says otherwise is "
        "the arithmetic the quality byte was added to stop being the answer"
    )
    assert water_depth_fraction(ground, surface, unknown).min() == pytest.approx(1.0), (
        "and it is tinted deep, not the pale green of an ankle-deep sheet"
    )

    # The same texels with the depth MEASURED are the old behaviour exactly: dry.
    known = numpy.ones((7, 40), numpy.float32)
    assert water_alpha(ground, surface, wet, known, 0.8).max() == pytest.approx(0.0, abs=1e-3)

    # And a field with no quality byte at all falls back to the comparison rather than
    # reading missing as dry -- which is all such a field can say.
    class _Old:
        _height_dm = numpy.array([[0, 0], [0, 0]], numpy.int16)
        _prov = numpy.zeros((2, 2), numpy.uint8)

        def _water_raster(self):
            return numpy.array([[5, hf.NODATA], [5, 5]], numpy.int16)

        def _water_quality_raster(self):
            return None

    plane, measured, note = water_planes(_Old())
    assert plane.tolist() == [[1, 0], [1, 1]] and measured is plane
    assert "predates the quality byte" in note


class _Field:
    """The five things the two-regime stages ask of a loaded field, over a tiny grid.

    A stand-in rather than a written field, for the same reason ``_FakeSheet`` below is a
    stand-in for Pillow: these stages are arithmetic over three planes and a spacing, and
    driving them through a 7500 px container would test zlib.
    """

    spacing_cm = 100.0

    def __init__(self, height_dm, prov, density=None):
        numpy = pytest.importorskip("numpy")
        self._height_dm = numpy.asarray(height_dm, numpy.int16)
        self._prov = numpy.asarray(prov, numpy.uint8)
        self._density = None if density is None else numpy.asarray(density, numpy.uint8)
        self.height, self.width = self._height_dm.shape
        self.x0_cm = self.y0_cm = 0.0

    def density_raster(self):
        return self._density


def test_the_density_plane_decides_per_texel_and_says_nothing_when_it_is_absent():
    """``direct_weight``: the rule scales with the output texel, and absent is not zero.

    Two claims, and the second one is the one that could ship a wrong picture quietly. The
    rule is "one source vertex under an output texel", so halving the texel quadruples the
    density a texel needs -- which is exactly why fewer texels are direct at z7 than at z6,
    and it has to fall out of the arithmetic rather than out of a second constant.

    And a field written before ``density.u8.z`` existed knows nothing about its own
    density. ``None`` there is not "no samples anywhere": read that way, every cliff on the
    map would silently drop to the kernel under a sidecar claiming the two-regime recipe.
    """
    numpy = pytest.importorskip("numpy")
    pytest.importorskip("scipy")

    density = numpy.zeros((41, 41), numpy.uint8)
    density[14:27, 14:27] = 10  # ten vertices in each of a 13 m square's metres
    prov = numpy.full((41, 41), hf.PROV_CLIFF_DIRECT, numpy.uint8)
    field = _Field(numpy.zeros((41, 41), numpy.int16), prov, density)

    coarse, coarse_meta = direct_weight(field, 0.4578)  # a z6 texel
    fine, fine_meta = direct_weight(field, 0.2289)  # a z7 texel
    assert coarse_meta["density_min_per_field_texel"] == pytest.approx(4.77, abs=0.01)
    assert fine_meta["density_min_per_field_texel"] == pytest.approx(19.09, abs=0.01)
    # Ten vertices is enough for a z6 texel and not for a z7 one, from the same plane.
    assert coarse.max() > 0, "a texel with ten samples answers a 0.458 m question"
    assert fine.max() == 0, "and does not answer a 0.229 m one"
    # A mask and not a weight: it labels the texels that qualify and nothing beside them,
    # because a provenance label is a yes or a no and the average of two labels is neither.
    assert set(numpy.unique(coarse)) == {0, 255}
    assert coarse[20, 20] == 255 and coarse[20, 27] == 0

    absent, absent_meta = direct_weight(_Field(field._height_dm, prov), 0.2289)
    assert absent is None and hf.DENSITY_NAME in absent_meta["absent"]


def test_the_rocks_are_composited_onto_the_lattice_and_can_only_raise_it():
    """``blend_regimes``: the field's own composition rule, at the render's spacing.

    Four things, and every one of them is a picture that would still look like terrain if it
    were wrong. The weight has to be a coverage in [0, 1], or the height leaves both
    surfaces. A rock has to be able to RAISE the ground and never lower it, or the tail of a
    coverage reaching a texel the rock passes under draws a trench around the base of every
    formation. That lift has to be smooth, or the hillshade draws a line where the rock
    meets the ground. And where the lattice has nothing and the rock has something, the rock
    is the whole answer and the pixel stops being no-data.
    """
    numpy = pytest.importorskip("numpy")
    pytest.importorskip("scipy")

    size = 24
    ground = numpy.full((size, size), 100.0, numpy.float32)
    # A rock 100 m tall over the left half, and over the right half geometry that lies
    # BELOW the ground -- an overhang's underside, which must not be allowed to dig.
    z_cm = numpy.full((size, size), 5000.0, numpy.float32)
    z_cm[:, : size // 2] = 20000.0
    coverage = numpy.ones((size, size), numpy.uint8)
    taps = (
        taps_linear(numpy.arange(size, dtype=numpy.float64), size),
        taps_linear(numpy.arange(size, dtype=numpy.float64), size),
    )
    missing = numpy.zeros((size, size), bool)

    z_m, still_missing, w, switched = blend_regimes(ground, missing, (z_cm, coverage), taps, 1)
    assert 0.0 <= w.min() and w.max() <= 1.0, "a coverage outside [0, 1] is not a coverage"
    assert z_m[:, 0] == pytest.approx(200.0, abs=0.2), "the rock stands where it stands"
    assert z_m[:, -1] == pytest.approx(100.0, abs=0.2), "and never digs below the ground"
    assert (z_m >= ground - 1e-3).all(), "no texel anywhere was lowered by the geometry"
    assert not still_missing.any()

    # The lift is smooth: across the step from 200 m of rock to 50 m of underside, the
    # drawn height must not be the hard max, which has a corner exactly where they cross.
    knee = DIRECT_LIFT_KNEE_M
    interior = slice(2, size // 2 - 2)
    hard = numpy.maximum(z_cm / 100.0, ground)
    delta = (z_m - hard)[:, interior]
    assert (delta >= -1e-4).all(), "away from the silhouette it is never below the hard max"
    assert delta.max() <= knee / 2 + 1e-4, "and never more than a half-knee above it"
    assert set(numpy.unique(switched[:, interior].round(3))) == {200.0}
    assert set(numpy.unique(switched[:, -6:].round(3))) == {100.0}

    # Where a rock covers a pixel the lattice knows nothing about, the rock is the answer
    # and the pixel stops being no-data; where neither has anything, it stays so.
    blank = numpy.ones((size, size), bool)
    none = numpy.zeros((size, size), numpy.uint8)
    z_m, still_missing, w, _switch = blend_regimes(ground, blank, (z_cm, coverage), taps, 1)
    assert z_m[:, 0] == pytest.approx(200.0) and not still_missing.any()
    _z, all_missing, _w, _s = blend_regimes(ground, blank, (z_cm, none), taps, 1)
    assert all_missing.all()


def test_the_kernel_interpolates_the_lattice_and_not_the_fold_it_produced():
    """``ground_lattice``: the one thing that decides whether a rim can smooth at all.

    Interpolating the composed field over a rim reconstructs the FOLD -- a texel just
    outside a rock is still a cliff-top height, because a cliff-top texel is one of the four
    the stencil reads -- so the drop stays on the 1 m staircase the fold put it on however
    fine the output grid is. That is why two attempts at this left the ragged rim in place.
    What the kernel has to be given is the surface UNDERNEATH: the landscape and fill
    lattices, which are continuous geometry the game evaluates itself.
    """
    numpy = pytest.importorskip("numpy")
    pytest.importorskip("scipy")

    height = numpy.full((6, 6), 100, numpy.int16)
    height[:, 2:4] = 400  # a rock, two texels wide, 30 m up
    prov = numpy.full((6, 6), hf.PROV_LANDSCAPE, numpy.uint8)
    prov[:, 2:4] = hf.PROV_CLIFF
    field = _Field(height, prov)
    ground, meta = ground_lattice(field, height.astype(numpy.float32))

    assert (ground[:, 2:4] == hf.NODATA).all(), "the cliff province is not the ground"
    assert (ground[:, :2] == 100).all() and (ground[:, 4:] == 100).all()
    assert meta["removed_share_of_the_field"] == pytest.approx(100 * 2 / 6, abs=0.01)
    # And the whole point of it: a stencil just outside the rock now reads only ground.
    taps = (
        taps_linear(numpy.arange(6, dtype=numpy.float64), 6),
        taps_linear(numpy.arange(6, dtype=numpy.float64), 6),
    )
    values, missing = sample_surface(ground, taps, taps, hf.NODATA)
    assert values[:, 1] == pytest.approx(100.0), "no cliff-top height leaks into the ground"
    assert missing[:, 2:4].all(), "and under the rock the lattice says nothing, not zero"


def test_a_rock_pixel_is_its_own_triangle_and_never_leaks_across_the_silhouette():
    """Rock heights are gated on triangle coverage per pixel and never blurred.

    A covered pixel is the exact surface even where the triangle is wider than the pixel,
    and an uncovered neighbour stays ground: spreading a rock's height across its own
    silhouette is the smear this render exists to avoid. Sub-samples are the only
    antialiasing, as a share of hits.
    """
    numpy = pytest.importorskip("numpy")
    pytest.importorskip("scipy")

    size = 5
    z_cm = numpy.zeros((size, size), numpy.float32)
    coverage = numpy.zeros((size, size), numpy.uint8)
    z_cm[2, 2], z_cm[2, 3] = 5000.0, 4100.0
    coverage[2, 2] = coverage[2, 3] = 1
    taps = (
        taps_linear(numpy.arange(size, dtype=numpy.float64), size),
        taps_linear(numpy.arange(size, dtype=numpy.float64), size),
    )
    ground = numpy.full((size, size), 10.0, numpy.float32)
    blank = numpy.zeros((size, size), bool)
    z_m, _missing, w, _s = blend_regimes(ground, blank, (z_cm, coverage), taps, 1)
    assert z_m[2, 2] == pytest.approx(50.0, abs=1e-3) and z_m[2, 3] == pytest.approx(41.0, abs=1e-3)
    assert (z_m[coverage == 0] == 10.0).all(), "no neighbour borrows a rock height"
    assert set(numpy.unique(w)) == {0.0, 1.0}
    quarter = pixel_coverage(numpy.array([[1, 4]], numpy.uint8), 2)
    assert quarter.tolist() == [[0.25, 1.0]]


def test_pchip_is_exact_at_the_vertices_and_never_overshoots_a_step():
    """The sampler: through every 1 m sample, and never outside a cell's own range.

    Catmull-Rom over the same step rings, which is what makes the second claim a test.
    """
    numpy = pytest.importorskip("numpy")
    pytest.importorskip("scipy")

    size = 12
    lattice = numpy.zeros((size, size), numpy.float32)
    lattice[:, 6:] = 400.0  # a 40 m cliff, in decimetres
    lattice[3, 2] = 55.0  # and a lone bump on the low side
    vertex = numpy.arange(size, dtype=numpy.float64)
    vlinear = (taps_linear(vertex, size),) * 2
    vtaps = (taps_pchip(vertex, size),) * 2
    at_vertex, missing = sample_surface(lattice, vtaps, vlinear, hf.NODATA)
    assert not missing.any() and numpy.array_equal(at_vertex, lattice), "exact at every vertex"

    fine = numpy.linspace(0.0, size - 1.0, 4 * size + 1)
    linear = (taps_linear(fine, size),) * 2
    cell = numpy.clip(numpy.floor(fine).astype(int), 0, size - 2)
    r, c = cell[:, None], cell[None, :]
    corners = numpy.stack(
        [lattice[r, c], lattice[r, c + 1], lattice[r + 1, c], lattice[r + 1, c + 1]]
    )
    for kernel, rings in ((taps_pchip, False), (taps_cubic, True)):
        taps = (kernel(fine, size),) * 2
        values, _missing = sample_surface(lattice, taps, linear, hf.NODATA)
        over = numpy.maximum(values - corners.max(0), corners.min(0) - values)
        assert bool(over.max() > 1.0) == rings, kernel.__name__
    # And it is still a ramp through the riser, not a staircase of flat cells.
    taps = (taps_pchip(fine, size),) * 2
    values, _missing = sample_surface(lattice, taps, linear, hf.NODATA)
    across = values[0, (fine > 5) & (fine < 6)]
    assert numpy.all(numpy.diff(across) > 0), "monotone through the riser"


def _fill_fixture():
    """A 121 m square: landscape west, raster fill east, a rock, a hole, and open sea."""
    numpy = pytest.importorskip("numpy")
    size = 121
    y, x = numpy.mgrid[0:size, 0:size].astype(numpy.float64)

    def truth(xm, ym):
        return 20.0 + 6.0 * numpy.sin(xm / 17.0) + 4.0 * numpy.cos(ym / 23.0) + 0.05 * xm

    land = truth(x, y)
    prov = numpy.full((size, size), hf.PROV_LANDSCAPE, numpy.uint8)
    prov[:, 60:] = hf.PROV_FILL
    prov[:, 112:] = hf.PROV_NODATA  # the open sea, touching the field's edge
    prov[20:30, 20:30] = hf.PROV_CLIFF_DIRECT  # a rock with no landscape under it
    prov[80:88, 30:38] = hf.PROV_NODATA  # an interior hole
    # What the field stores on the fill province: the nearest raster texel, 1 m low.
    stored = land.copy()
    stored[:, 60:] = numpy.round(land[:, 60:] / 3.0) * 3.0 - 1.0
    height = numpy.round(stored * 10).astype(numpy.int16)
    height[prov == hf.PROV_CLIFF_DIRECT] = 900  # 90 m of rock
    height[prov == hf.PROV_NODATA] = hf.NODATA
    ground = numpy.where(prov == hf.PROV_LANDSCAPE, land * 10, hf.NODATA).astype(numpy.float32)
    ground[prov == hf.PROV_FILL] = height[prov == hf.PROV_FILL]
    # The raster: 3 m texels over the same square, holding the surface 1 m low.
    px = 40
    centres = (numpy.arange(px) + 0.5) * (size * 100.0 / px) / 100.0 - 0.5
    raster = truth(centres[None, :], centres[:, None]) - 1.0
    kwargs = {
        "ground_dm": ground,
        "height_dm": height,
        "prov": prov,
        "water_quality": numpy.zeros((size, size), numpy.uint8),
        "water_dm": numpy.full((size, size), hf.NODATA, numpy.int16),
        "raster_m": raster,
        "raster_ok": numpy.ones((px, px), bool),
        "field_origin_cm": (0.0, 0.0),
        "spacing_cm": 100.0,
        "raster_box_cm": (-50.0, size * 100.0 - 50.0, -50.0, size * 100.0 - 50.0),
        "nodata": hf.NODATA,
        "fill_value": hf.PROV_FILL,
        "rock_values": hf.PROV_CLIFF_VALUES,
    }
    return kwargs, land, prov


def test_the_fill_is_rebuilt_from_the_raster_and_meets_the_landscape_without_a_step():
    """``map_fill.fill_field`` on a fixture whose truth is known everywhere.

    The rebuilt fill beats the stored nearest texel, the seam column jumps by about what the
    truth does, and the hole is filled close to the hidden surface.
    """
    numpy = pytest.importorskip("numpy")
    pytest.importorskip("scipy")

    kwargs, land, prov = _fill_fixture()
    heights, ground, source, meta = fill_field(**kwargs)
    fill = prov == hf.PROV_FILL
    rebuilt = numpy.median(numpy.abs(ground[fill] / 10.0 - land[fill]))
    stored = numpy.median(numpy.abs(kwargs["height_dm"][fill] / 10.0 - land[fill]))
    assert rebuilt < 0.25 and rebuilt < stored / 3

    rows = slice(5, 115)
    jump = numpy.abs(ground[rows, 60] - ground[rows, 59]) / 10.0
    true_jump = numpy.abs(land[rows, 60] - land[rows, 59])
    assert numpy.median(numpy.abs(jump - true_jump)) < 0.05, "the seam is not a step"
    assert (source[fill] == SOURCE_SEAM).any()

    hole = numpy.zeros_like(fill)
    hole[80:88, 30:38] = True
    assert (source[hole] == SOURCE_HOLE).all()
    assert numpy.abs(ground[hole] / 10.0 - land[hole]).mean() < 0.2
    assert meta["holes"]["holes"] == 2, "the hole and the ground under the rock"
    landscape = prov == hf.PROV_LANDSCAPE
    assert numpy.array_equal(ground[landscape], kwargs["ground_dm"][landscape])
    assert numpy.array_equal(heights[landscape], kwargs["ground_dm"][landscape])


def test_rock_is_copied_unchanged_and_never_smoothed_into_the_ground():
    """Rock heights pass through the fill untouched, and none of them reach the ground."""
    numpy = pytest.importorskip("numpy")
    pytest.importorskip("scipy")

    kwargs, land, prov = _fill_fixture()
    heights, ground, source, _meta = fill_field(**kwargs)
    rock = prov == hf.PROV_CLIFF_DIRECT
    assert numpy.array_equal(heights[rock], kwargs["height_dm"][rock].astype(numpy.float32))
    assert (source[rock] == SOURCE_ROCK).all()
    # The ground under the rock is filled from the ground around it, not from the rock.
    assert numpy.abs(ground[rock] / 10.0 - land[rock]).max() < 3.0
    beside = numpy.zeros_like(rock)
    beside[18:32, 18:32] = True
    beside &= ~rock
    assert numpy.array_equal(ground[beside], kwargs["ground_dm"][beside])


def test_the_open_sea_past_the_data_stays_the_page_s_colour():
    """Nothing is invented where the field has no data out to its edge.

    The texels stay no-data in both lattices, the sampler calls them missing, and the
    painter draws the page's sea there, not water over an invented bed.
    """
    numpy = pytest.importorskip("numpy")
    pytest.importorskip("scipy")

    kwargs, _land, prov = _fill_fixture()
    heights, ground, source, meta = fill_field(**kwargs)
    sea = numpy.zeros(prov.shape, bool)
    sea[:, 112:] = True
    assert (heights[sea] == hf.NODATA).all() and (ground[sea] == hf.NODATA).all()
    assert (source[sea] == SOURCE_NONE).all()
    assert "no depth is invented" in meta["open_sea"]

    size = prov.shape[0]
    positions = numpy.arange(size, dtype=numpy.float64)
    linear = (taps_linear(positions, size),) * 2
    smooth = (taps_pchip(positions, size),) * 2
    z_dm, missing = sample_surface(heights, smooth, linear, hf.NODATA)
    assert missing[:, 112:].all() and not missing[:, :112].any()
    z_m = numpy.where(missing, 0.0, z_dm / hf.DM_PER_M).astype(numpy.float32)
    ones = numpy.ones(z_m.shape, numpy.float32)
    zeros = numpy.zeros_like(z_m)
    water = {"cover": zeros, "depth": zeros, "depth_m": zeros, "ocean": zeros, "edge": zeros}
    scene = {"z_m": z_m, "shade": ones, "borrow": ones, "ramp_lo": 0.0, "ramp_hi": 100.0,
             "water": water}  # fmt: skip
    rgb = with_sea(terrain_colours(scene), missing)
    assert (rgb[missing] == SEA_RGB).all()


def test_the_seam_trace_measures_the_join_and_says_what_it_cannot_measure():
    """``SeamTrace``: what it reports, and the one thing it is honest about not being.

    The design asked for a bound and there is no valid one to have here, which is a result
    rather than an omission. Compositing a rock onto a lattice by the rock's own coverage
    puts every join on a geometric feature, so a comparison against the ground beside it
    measures the world; and the counterfactual that does isolate the join -- the hard max
    over the same texels -- cannot fail, because a convex blend of two surfaces is bounded
    by the extreme points of that blend and rounding the weight to 0 or 1 is exactly those.

    So this pins the two things that ARE claims. The identity: a switch spends the whole
    ceiling and reads 1.0. And the description: a fade over forty texels spends a small
    fraction of it, which is the number the run prints and the sidecar records.
    """
    numpy = pytest.importorskip("numpy")
    pytest.importorskip("scipy")

    rows, cols = 8, 400
    spacing = 0.2289
    x = numpy.arange(cols) * spacing
    lattice = numpy.tile(2.0 * numpy.sin(x / 20.0), (rows, 1)).astype(numpy.float32)
    rock = (lattice + 4.0).astype(numpy.float32)
    delta = rock - lattice

    def measure(w):
        trace = SeamTrace()
        drawn = w * rock + (1.0 - w) * lattice
        switched = numpy.where(w >= SEAM_MID, rock, lattice)
        trace.add(drawn, switched, w, spacing, delta)
        return trace.result()

    t = numpy.clip((numpy.arange(cols) - 180) / 40.0, 0.0, 1.0)
    faded = measure(numpy.tile(t * t * (3.0 - 2.0 * t), (rows, 1)).astype(numpy.float32))
    assert faded["measured"], faded
    ceiling = SEAM_SWITCH_CEILING
    assert faded["share_of_a_hard_switch"] < 0.1, faded
    assert faded["share_of_a_hard_switch"] <= ceiling
    # The reference the design named is still computed and still reported beside it, with
    # the reason it is not the one that decides.
    assert faded["against_the_pure_regimes"] is not None
    assert faded["against_the_terrain_where_the_surfaces_agree"] is None, (
        "four metres apart at the join is not two reconstructions of one surface"
    )
    assert "TERRAIN" in faded["reading"]

    # A join that IS a switch spends the whole ceiling, exactly, which is the identity that
    # makes this a description rather than a gate.
    inside = (t > 0) & (t < 1)
    alternating = numpy.where(numpy.arange(cols) % 2 == 0, 1.0, 0.0)
    hard = numpy.tile(numpy.where(inside, alternating, t), (rows, 1)).astype(numpy.float32)
    assert measure(hard)["share_of_a_hard_switch"] == pytest.approx(ceiling)

    # And a render with no rocks in it says so rather than dividing by nothing.
    none = numpy.zeros((rows, cols), numpy.float32)
    assert measure(none)["measured"] is False


def test_the_regime_table_counts_by_province_and_reports_the_unbucketed_weight():
    """``RegimeCoverage``: three buckets and the mean behind them.

    The direct bucket is split by what the density plane says the drawn answer WAS -- a
    texel a source vertex landed in, or the plane of a triangle wider than the texel -- and
    that split is the whole of what the plane does here. ``mean_w`` beside the buckets is
    the unbucketed answer, because a bucket boundary at 0.98 hides a coverage doing real
    work at 0.7.
    """
    numpy = pytest.importorskip("numpy")

    prov = numpy.array([[hf.PROV_CLIFF_DIRECT] * 2 + [hf.PROV_LANDSCAPE] * 2] * 2, numpy.uint8)
    w = numpy.array([[1.0, 0.5, 0.0, 0.0], [1.0, 0.5, 0.0, 0.0]], numpy.float32)
    # The first cliff column is a measurement, the second is a facet the rasteriser
    # interpolated -- both fully covered, and the table has to tell them apart.
    measured = numpy.array([[True, True, False, False]] * 2)
    table = RegimeCoverage()
    table.add(prov, w, measured)
    out = table.result()
    cliff = out["per_province_pct_of_sheet"][hf.PROV_NAMES[hf.PROV_CLIFF_DIRECT]]
    land = out["per_province_pct_of_sheet"][hf.PROV_NAMES[hf.PROV_LANDSCAPE]]
    assert (cliff["direct_measured"], cliff["faded"], cliff["kernel"]) == (25.0, 25.0, 0.0)
    assert cliff["direct_facet"] == 0.0
    assert (land["direct_measured"], land["direct_facet"], land["kernel"]) == (0.0, 0.0, 50.0)
    assert cliff["mean_w"] == pytest.approx(0.75) and land["mean_w"] == pytest.approx(0.0)
    assert out["sheet_pct"]["mean_w"] == pytest.approx(0.375)


def test_a_direct_cache_from_another_render_is_rebuilt_rather_than_drawn_from(tmp_path):
    """``cached_direct``: three keys, and a mismatch on any of them is a different picture.

    The raster is written once and read by both layers, which is the only reason it is on
    disk at all -- so the question a reader has is whether the bytes under this run's
    sidecar are of this run's grid, this run's sub-sampling and this run's build. Anything
    else is last week's rocks, and the answer to that is to rasterise again rather than to
    draw them.
    """
    numpy = pytest.importorskip("numpy")

    stamp = direct_cache_stamp(8, 1, "build 495413")
    tmp_path.mkdir(parents=True, exist_ok=True)
    numpy.zeros((8, 8), numpy.float32).tofile(tmp_path / DIRECT_Z_NAME)
    numpy.zeros((8, 8), numpy.uint8).tofile(tmp_path / DIRECT_COVERAGE_NAME)
    (tmp_path / DIRECT_CACHE_SIDECAR).write_text(
        json.dumps({**stamp, "seconds": 1.0}), encoding="utf-8"
    )
    assert cached_direct(tmp_path, stamp) is not None
    for other in (
        direct_cache_stamp(16, 1, "build 495413"),
        direct_cache_stamp(8, 2, "build 495413"),
        direct_cache_stamp(8, 1, "build 500000"),
    ):
        assert cached_direct(tmp_path, other) is None
    assert cached_direct(tmp_path / "nowhere", stamp) is None


def test_the_direct_pass_applies_the_field_s_own_culls_and_lands_where_it_says(tmp_path):
    """The placement culls are the generator's, and the raster is on the render's grid.

    Two things are being pinned. First that the four culls are the same four the field was
    built with -- an excluded owner, a mesh with no geometry, an arch, an oversized shell --
    because a render blended with a field whose rocks are a different set of rocks is a
    picture of a different world. Second that the grid the triangles land on is the frame's
    own: a half-texel offset here would be invisible in every statistic and would draw every
    rim in the wrong place.
    """
    numpy = pytest.importorskip("numpy")

    # One unit square of two triangles, lying flat at z = 500 cm, one metre on a side.
    verts = numpy.array([[0, 0, 500], [100, 0, 500], [100, 100, 500], [0, 100, 500]], numpy.float32)
    tris = numpy.array([[0, 1, 2], [0, 2, 3]], numpy.int64)
    geometry = {
        "/World/Environment/Rock/Slab": (verts, tris),
        # An arch decodes exactly as well as a slab does. It is dropped for what it IS --
        # a max-Z field puts a roof over the ground beneath it -- so it has to be in the
        # geometry, or the cull under test is the missing-geometry one wearing its name.
        "/World/Environment/Rock/Arc_Slab": (verts, tris),
    }
    identity = (0.0, 0.0, 0.0)
    unit = (1.0, 1.0, 1.0)
    x0, y0 = BOUNDS_M["x_min_m"] * 100, BOUNDS_M["y_min_m"] * 100
    sweep = {
        "meshes": [
            "/World/Environment/Rock/Slab",
            "/World/Environment/Rock/Arc_Slab",
            "/World/Environment/Rock/Missing",
        ],
        "owners": ["RockActor_C", next(iter(EXCLUDED_OWNERS))],
        "placements": numpy.array(
            [
                (0, 0, x0 + 1000, y0 + 1000, 0, *identity, *unit),  # drawn
                (0, 1, x0 + 2000, y0 + 1000, 0, *identity, *unit),  # excluded owner
                (1, 0, x0 + 3000, y0 + 1000, 0, *identity, *unit),  # an arch is a roof
                (2, 0, x0 + 4000, y0 + 1000, 0, *identity, *unit),  # no cooked geometry
                (0, 0, x0 + 5000, y0 + 1000, 0, *identity, 1e4, 1e4, 1e4),  # a sky dome
            ],
            numpy.float64,
        ),
    }
    prepared, dropped = direct_placements(sweep, geometry)
    assert len(prepared) == 1
    assert dropped == {"owner": 1, "no_geometry": 1, "arch": 1, "oversize": 1}

    # And it rasterises onto the frame's own grid at the frame's own spacing. A 1 m slab
    # placed 10 m east of the frame's western edge covers exactly the z7 texels whose own
    # centres fall in that metre, and no others -- which is the claim a half-texel offset
    # would break invisibly, since a rim drawn one texel out still looks like a rim.
    step_cm = (BOUNDS_M["x_max_m"] - BOUNDS_M["x_min_m"]) * 100 / 32768
    # Four rows, because a 1 m slab is 4.4 texels of 0.229 m tall and the band is anchored
    # at its northern edge: asking about a fifth row would be asking about ground the slab
    # does not stand on.
    rows, cols = 4, 64
    first = int(numpy.ceil(1000.0 / step_cm - 0.5))
    last = int(numpy.ceil(1100.0 / step_cm - 0.5))
    band = rasterise_direct_band(prepared, geometry, x0, y0 + 1000, step_cm, rows, cols, 1)
    z_cm, coverage = reduce_direct(band, rows, cols, 1)
    assert coverage[:, first:last].all(), "every texel centred inside the slab is covered"
    assert not coverage[:, :first].any() and not coverage[:, last:].any(), "and none outside"
    assert z_cm[coverage > 0] == pytest.approx(500.0)


class _TerrainField(_Field):
    """``_Field`` plus a bare-landscape plane on its own grid, one row and column in."""

    def __init__(self, height_dm, prov, raw):
        super().__init__(height_dm, prov)
        numpy = pytest.importorskip("numpy")
        self._raw = numpy.asarray(raw, numpy.uint16)
        self._terrain_grid = {"zero": 32768.0, "units_per_m": 128.0, "offset_m": 1.0}
        self._terrain_grid.update(row_off=1, col_off=1)

    def _plane(self, name):
        return self._raw if name == hf.TERRAIN_NAME else None


def test_the_kernel_reads_the_landscape_at_its_own_vertical_step_under_the_rocks_too():
    """``terrain_lattice``: the decimetre plane draws contours on gentle ground.

    A 0.1 m step across a 1 m texel is a 5.7 degree facet, so the kernel has to be handed
    the raw uint16 landscape at 7.8 mm. Under the cliff province the real terrain replaces
    the hole the rocks left; fill and landscape holes keep what they had.
    """
    numpy = pytest.importorskip("numpy")
    pytest.importorskip("scipy")

    height = numpy.full((4, 4), 103, numpy.int16)
    prov = numpy.full((4, 4), hf.PROV_LANDSCAPE, numpy.uint8)
    prov[1, 2] = hf.PROV_CLIFF_DIRECT
    prov[2, 1] = hf.PROV_FILL
    ground, _meta = ground_lattice(_Field(height, prov), height.astype(numpy.float32))
    assert ground[1, 2] == hf.NODATA

    raw = numpy.full((3, 3), 32768 + 128 * 9 + 37, numpy.uint16)  # 10.289 m, between dm
    raw[2, 2] = 0  # a hole in the landscape
    out, meta = terrain_lattice(_TerrainField(height, prov, raw), ground)

    exact_dm = (9 + 37 / 128 + 1.0) * 10
    assert out[1, 1] == pytest.approx(exact_dm, abs=1e-4), "landscape at 7.8 mm, not 0.1 m"
    assert out[1, 2] == pytest.approx(exact_dm, abs=1e-4), "and under the rock as well"
    assert out[2, 1] == ground[2, 1], "fill keeps its own value"
    assert out[3, 3] == ground[3, 3], "a landscape hole keeps the decimetre plane"
    assert out[0, 0] == ground[0, 0], "outside the terrain grid nothing changes"
    assert ground[1, 1] == 103, "the caller's lattice is not written through"
    assert (meta["landscape_texels"], meta["under_cliff_texels"]) == (6, 1)

    same, absent = terrain_lattice(_Field(height, prov), ground)
    assert same is ground and "absent" in absent


def test_the_top_overlay_raises_the_ground_smoothly_and_lands_on_pixel_centres():
    """Arches and boulders: drawn on the render's own grid and composited like the rocks.

    The overlay only ever raises the surface, through the same knee the rocks use. A foliage
    instance lands on the pixel centres its footprint covers, and an arch keeps triangles a
    facing cull would drop, because its deck is often an open shell.
    """
    numpy = pytest.importorskip("numpy")
    pytest.importorskip("scipy")

    z_m = numpy.full((9, 9), 10.0, numpy.float32)
    top_z = numpy.zeros((9, 9), numpy.float32)
    coverage = numpy.zeros((9, 9), numpy.uint8)
    assert numpy.array_equal(composite_top(z_m, top_z, coverage), z_m)
    top_z[:, :5] = 1500.0
    top_z[:, 5:] = 500.0  # below the ground: must not dig
    coverage[:] = 1
    raised = composite_top(z_m, top_z, coverage)
    assert raised[:, 1] == pytest.approx(15.0, abs=0.01)
    assert (raised >= z_m).all()
    assert raised[:, -1].max() <= 10.0 + DIRECT_LIFT_KNEE_M / 2 + 1e-4

    step_cm = 750000.0 / 32768
    flat = numpy.array([[0, 0, 300], [100, 0, 300], [100, 100, 300], [0, 100, 300]], numpy.float32)
    up = numpy.array([[0, 1, 2], [0, 2, 3]], numpy.int64)
    matrix = numpy.eye(4, dtype=numpy.float32)
    matrix[3, :3] = (1000.0, 0.0, 0.0)
    items = {
        "arches": [
            (
                "Arc",
                0,
                numpy.eye(3, dtype=numpy.float32),
                numpy.ones(3, numpy.float32),
                numpy.array([3000.0, 0.0, 200.0], numpy.float32),
                0.0,
                0.0,
                100.0,
            )
        ],
        "boulders": {"Boulder": (matrix[None], numpy.array([-200.0]), numpy.array([200.0]))},
        "shapes": {"Arc": (flat, up[:, ::-1].copy()), "Boulder": (flat, up)},
    }
    band = rasterise_top_band(items, 0.0, 0.0, step_cm, 4, 160, 1)
    z_cm, cover = reduce_direct(band, 4, 160, 1)
    first = int(numpy.ceil(1000.0 / step_cm - 0.5))
    last = int(numpy.ceil(1100.0 / step_cm - 0.5))
    assert cover[:, first:last].all() and not cover[:, last : last + 2].any()
    assert z_cm[:, first] == pytest.approx(300.0)
    arch = int(numpy.ceil(3000.0 / step_cm - 0.5))
    assert cover[:, arch].all() and z_cm[0, arch] == pytest.approx(500.0)


def test_the_biome_palette_is_this_file_s_own_and_covers_what_the_game_ships():
    """The satellite layer's colours are designed, and every area the game names has one.

    The asset ships 37 RGBA entries and they are a minimap legend -- flat primaries, cyan,
    magenta, pure white -- so they are decoded for the record and never drawn. What has to
    hold is that the replacement is complete (an area with no colour would fall back to a
    neutral and quietly vanish into the coast) and that it really is a satellite palette
    rather than the legend under another name: nothing saturated, nothing at full white.
    """
    # Pinned by name rather than against a subset. The old form compared this table with
    # ``REGION_PAIRS``, a list of wiki names that meant the same place as a game area; that
    # list is gone with the wiki trace, and what replaces it is the stronger claim: these
    # are the seventeen area stems build 495413 names, and every one of them has a colour.
    # ``tests/test_gameassets_maparea.py`` pins the same seventeen against the container.
    assert set(BIOME_COLOURS) == {
        "Area_AbyssCliffs",
        "Area_DesertCanyons",
        "Area_DuneDesert",
        "Area_GrassFields",
        "Area_LakeForest",
        "Area_MazeCanyons",
        "Area_NorthernForest",
        "Area_RedBambooFields",
        "Area_RedJungle",
        "Area_RockyDesert",
        "Area_Savanna",
        "Area_SouthernForest",
        "Area_SpireCoast",
        "Area_Swamp",
        "Area_TitanForest",
        "Area_WesternDuneForest",
        "Area_crater",
    }
    for name, colour in BIOME_COLOURS.items():
        assert len(colour) == 3 and all(0 <= c <= 255 for c in colour), name
        assert max(colour) - min(colour) <= 110, f"{name} is more saturated than imagery gets"
        assert max(colour) <= 220, f"{name} is brighter than imagery gets"
    # The fallbacks are the same kind of colour, so an area a later build adds looks
    # unremarkable rather than wrong.
    for colour in (NO_MANS_LAND_RGB, UNKNOWN_BIOME_RGB):
        assert max(colour) - min(colour) <= 40


class _FakeSheet:
    """The three things ``cut_pyramid`` asks of a Pillow image, and nothing else.

    Pillow is the generators' dependency -- the optional ``gen`` extra -- and this suite
    runs whether or not it is installed, so the cutting is exercised against a stand-in:
    what is under test here is the tree that comes out -- the levels, the names, the
    count -- not anybody's Lanczos filter.
    """

    def __init__(self, width: int):
        self.width = width

    def resize(self, size, _filter):
        return _FakeSheet(size[0])

    def crop(self, box):
        return _FakeTile(box)


class _FakeTile:
    def __init__(self, box):
        self.box = box

    def save(self, path, **_kwargs):
        path.write_bytes(_PNG)


def test_the_pyramid_is_renamed_into_place_so_a_reader_never_meets_half_of_one(tmp_path):
    """An interrupted run must leave no tree at all rather than a tree missing levels.

    So the cut goes to a staging directory and is renamed over the old one, and the count
    is checked against what is really on disk before the swap. Both leftovers of a run that
    died mid-swap -- the staging tree and the retired one -- are cleared rather than merged
    into, and a level the new pyramid does not have cannot survive from the old one.
    """
    assert pyramid_top_z(8192) == 5
    assert pyramid_top_z(2048) == 3
    assert pyramid_top_z(256) == 0
    with pytest.raises(PyramidError):
        pyramid_top_z(5000)

    tiles = tmp_path / TILES_DIR_NAME
    (tiles / "9").mkdir(parents=True)
    (tiles / "9" / "0_0.png").write_bytes(b"a level the new cut does not have")
    (tmp_path / TILES_STAGING / "3").mkdir(parents=True)
    (tmp_path / TILES_STAGING / "3" / "0_0.png").write_bytes(b"half of a dead run")

    imaging = types.SimpleNamespace(LANCZOS="the filter, which the stand-in ignores")
    stats = install_pyramid(_FakeSheet(1024), imaging, tmp_path)

    assert (stats["max_z"], stats["count"]) == (2, 1 + 4 + 16)
    assert stats["tile_px"] == PYRAMID_TILE_PX
    assert not (tmp_path / TILES_STAGING).exists(), "staging is not left behind"
    assert not (tmp_path / TILES_RETIRED).exists(), "nor is the tree it replaced"
    assert sorted(p.name for p in tiles.iterdir()) == ["0", "1", "2"]
    assert len(list(tiles.rglob("*.png"))) == stats["count"]
    assert (tiles / tile_relpath(2, 3, 3)).read_bytes() == _PNG


def test_the_generated_sidecar_is_read_by_the_server_provenance_and_all(
    client, tmp_path, monkeypatch
):
    """``tools/gen_map_image.py`` writes the sidecar and this endpoint reads it.

    Nothing else joins those two, and they agree on four key names, two file names and a
    directory. So the join is asserted against the tool's OWN output rather than a
    hand-typed sample that could drift away from what it really writes -- and in
    particular against the ``_meta`` block it puts beside the corners, which the reader
    has to walk past rather than trip over.
    """
    pin = "buildVersion 495413 (engine branch ++FactoryGame+rel-main-1.2.0), the installed build"
    sidecar = artwork_output.build_sidecar(
        build_pin=pin,
        build_raw={"Changelist": 495413, "BranchName": "++FactoryGame+rel-main-1.2.0"},
        image={"file": IMAGE_NAME, "width_px": SHEET_PX},
        integrity={"ubulk_bytes_expected": UBULK_BYTES},
        layout={"layout_holds": True},
        calibration={"pin_holds": True},
        versions={"pyooz": "0.0.8", "texture2ddecoder": "1.0.6", "pillow": "12.3.0"},
        tiles={
            "tile_px": PYRAMID_TILE_PX,
            "max_z": pyramid_top_z(SHEET_PX),
            "count": 1365,
            "bytes": 21_000_000,
            "game_version_pinned": pin,
        },
    )

    monkeypatch.setattr(config, "data_dir", lambda: tmp_path)
    local = tmp_path / registry.local_dir().name
    local.mkdir()
    (local / web_tiles.MAP_IMAGE_NAME).write_bytes(b"\x89PNG\r\n\x1a\n")
    (local / web_tiles.MAP_BOUNDS_NAME).write_text(json.dumps(sidecar), encoding="utf-8")

    # The provenance rides along unread: the corners still come through the probe.
    assert client.head("/api/mapimage").headers["x-map-bounds-m"] == "-3247.0,-3750.0,4253.0,3750.0"

    # The tool writes where this endpoint looks, under the names it looks for.
    assert LOCAL_DIR.name == registry.local_dir().name
    assert IMAGE_NAME == web_tiles.MAP_IMAGE_NAME
    assert SIDECAR_NAME == web_tiles.MAP_BOUNDS_NAME
    # And it pins the same square, rather than holding a second opinion about it.
    assert BOUNDS_M == web_tiles.DEFAULT_MAP_BOUNDS_M

    # The build survives the round trip through JSON, which is the whole of what lets a
    # stale picture be announced instead of silently drawn.
    written = json.loads((local / web_tiles.MAP_BOUNDS_NAME).read_text(encoding="utf-8"))
    assert pinned_build(written) == pin
    assert pinned_build({}) is None
    assert pinned_build({"_meta": {"sources": {}}}) is None

    # The pyramid half of the same join. The names and the arithmetic belong to the cutter
    # in ``core.gameassets.pyramid`` -- what the tool contributes is the sheet and the
    # record of what came out of it -- and the endpoint configures the page's tile grid
    # from that record, so the two cannot hold different opinions about what is served.
    # The names are now IMPORTED by the endpoint rather than retyped, so the two assertions
    # that used to check them agreed have gone: they compared a name with itself. The
    # arithmetic is the part that is still a claim.
    assert pyramid_top_z(SHEET_PX) == web_tiles.MAP_TILE_MAX_Z
    assert tile_relpath(3, 5, 6) == "3/5_6.png"
    assert web_tiles.map_tile_path(3, 5, 6, 5) == local / TILES_DIR_NAME / tile_relpath(3, 5, 6)

    read_back = web_tiles._map_pyramid()
    assert (read_back["tile_px"], read_back["max_z"]) == (PYRAMID_TILE_PX, 5)
    # And the build tag moves when the pyramid does, because that tag is what a browser
    # holding an immutable tile keys on.
    _fake_pyramid(local, max_z=0)
    assert client.head("/api/maptiles/0/0/0").headers["x-map-build"] == read_back["build"]
    sidecar["_meta"]["tiles"]["count"] = 1364
    (local / web_tiles.MAP_BOUNDS_NAME).write_text(json.dumps(sidecar), encoding="utf-8")
    assert client.head("/api/maptiles/0/0/0").headers["x-map-build"] != read_back["build"]

    # A sidecar that says nothing about tiles still serves them, at the defaults.
    (local / web_tiles.MAP_BOUNDS_NAME).write_text("{}", encoding="utf-8")
    bare = web_tiles._map_pyramid()
    assert (bare["tile_px"], bare["max_z"]) == (PYRAMID_TILE_PX, web_tiles.MAP_TILE_MAX_Z)


def test_the_artwork_tool_writes_the_dense_tree_the_endpoint_serves(client, tmp_path, monkeypatch):
    """``gen_map_image.py`` cuts ``tiles@2x/`` now, and the endpoint reads its record of it.

    The renders have written both trees since ``/api/maptiles`` learned to serve two, and the
    artwork tool wrote only the 1x one -- so the game's own map was the single layer a hi-dpi
    display saw soft, on a client that had been density-aware for a day. This holds the join
    the same way the sidecar test above does: against the tool's OWN output, because the two
    sides agree by a key name in a JSON file and nothing else.

    Both directions are asserted. A run that cut the tree must produce a block the endpoint
    turns into a second depth; a run that did not -- ``--no-tiles-2x``, or any pyramid from
    before this -- must produce NO block at all, because ``_map_pyramid`` reads the key's
    absence as "serve every client the 1x tile" and a block saying "absent" is still a block.
    """
    pin = "buildVersion 495413 (engine branch ++FactoryGame+rel-main-1.2.0), the installed build"
    common = {
        "build_pin": pin,
        "build_raw": {"Changelist": 495413},
        "image": {"file": IMAGE_NAME, "width_px": SHEET_PX},
        "integrity": {},
        "layout": {},
        "calibration": {},
        "versions": {},
        "tiles": {
            "tile_px": PYRAMID_TILE_PX,
            "max_z": pyramid_top_z(SHEET_PX),
            "count": 1365,
            "bytes": 21_000_000,
            "game_version_pinned": pin,
        },
    }

    # The arithmetic the tool leans on rather than typing in: the same sheet, cut into tiles
    # twice the size, is exactly one level shallower.
    dense_top = pyramid_top_z(SHEET_PX, PYRAMID_TILE_2X_PX)
    assert dense_top == pyramid_top_z(SHEET_PX) - 1 == 4

    monkeypatch.setattr(config, "data_dir", lambda: tmp_path)
    local = tmp_path / registry.local_dir().name
    local.mkdir()
    _fake_pyramid(local, max_z=0)  # something for the probe to answer about

    with_dense = artwork_output.build_sidecar(
        **common,
        tiles_2x={
            "tile_px": PYRAMID_TILE_2X_PX,
            "max_z": dense_top,
            "count": 341,
            "bytes": 20_000_000,
            "game_version_pinned": pin,
        },
    )
    (local / web_tiles.MAP_BOUNDS_NAME).write_text(json.dumps(with_dense), encoding="utf-8")
    read_back = web_tiles._map_pyramid()
    assert (read_back["tile_px"], read_back["max_z"]) == (PYRAMID_TILE_PX, 5)
    assert (read_back["tile_2x_px"], read_back["max_2x_z"]) == (PYRAMID_TILE_2X_PX, 4)
    assert client.head("/api/maptiles/0/0/0").headers["x-map-tile-2x-max-z"] == "4"

    # ...and the same run with the tree skipped writes no key, which is what makes the
    # endpoint fall back rather than advertise a depth for a directory that is not there.
    without = artwork_output.build_sidecar(**common, tiles_2x=None)
    assert "tiles_2x" not in without["_meta"]
    (local / web_tiles.MAP_BOUNDS_NAME).write_text(json.dumps(without), encoding="utf-8")
    bare = web_tiles._map_pyramid()
    assert bare["max_2x_z"] is None and bare["tile_2x_px"] is None
    assert "x-map-tile-2x-px" not in client.head("/api/maptiles/0/0/0").headers

    # The two trees' numbers ride in one cache tag, so re-cutting either moves every URL.
    assert read_back["build"] != bare["build"]


def test_the_artwork_tool_offers_the_opt_out_it_documents(tmp_path):
    """The flag is spelled one way in the help and one way in ``main``, and they must agree.

    Run as ``--help`` in a child, which is the only place argparse's own answer lives: the
    parser is built inside ``main`` and there is no object to interrogate from here. It is
    also the cheapest possible run of the tool -- argparse exits before ``require_gen``, so
    this needs neither the ``gen`` extra nor a game install.
    """
    import subprocess
    import sys

    from conftest import REPO_ROOT

    out = subprocess.run(
        [sys.executable, str(REPO_ROOT / "tools" / "gen_map_image.py"), "--help"],
        capture_output=True,
        text=True,
        check=False,
        cwd=str(REPO_ROOT),
    )
    assert out.returncode == 0, out.stderr[-2000:]
    assert "--no-tiles-2x" in out.stdout
    # Named after the directory it skips rather than after a spelling of its own.
    assert TILES_2X_DIR_NAME in out.stdout


def test_the_enhanced_pyramid_is_two_levels_deeper_and_the_server_follows_it_there(
    client, tmp_path, monkeypatch
):
    """``--enhance`` adds z6 and z7, and nothing on the serving side is hardcoded to z5.

    The depth is the sidecar's to state and the endpoint's to read: ``MAP_TILE_MAX_Z`` is
    only the answer for a sidecar that says nothing, and a pyramid that says seven must be
    served to seven. So the arithmetic is asserted where it is written down -- two levels
    for a 4x upscale, 16,384 tiles at z7, 21,845 in the whole tree -- and then a tile at
    the far corner of z7 is actually fetched through the route, with the level above it and
    the column past its edge both refused.
    """
    # Two levels for 4x, none for 1x, and a scale that is not a power of two divides no grid.
    assert enhanced_top_z(SHEET_PX) == pyramid_top_z(SHEET_PX) + 2 == 7
    assert enhanced_top_z(SHEET_PX, 1) == 5
    assert enhanced_top_z(2048, 4) == 5
    with pytest.raises(PyramidError):
        enhanced_top_z(SHEET_PX, 3)

    # z7 is 128 tiles a side of the 32768 px sheet, and the whole tree is (4**8 - 1) / 3.
    assert (1 << 7) * PYRAMID_TILE_PX == SHEET_PX * ENHANCE_SCALE
    assert (1 << 6) ** 2 == 4096
    assert (1 << 7) ** 2 == 16384
    assert sum(4**z for z in range(8)) == 21845

    # And the two halves of the record are merged, not appended to by hand: the count the
    # installer checks the tree against is re-summed from the levels a reader could count.
    plain = {
        "max_z": 5,
        "enhanced": False,
        "count": 1365,
        "bytes": 100,
        "levels": [{"z": z, "tiles": 4**z, "bytes": 10} for z in range(6)],
    }
    merged = merge_enhanced(
        plain,
        {
            "levels": [{"z": 6, "tiles": 4096, "bytes": 40}, {"z": 7, "tiles": 16384, "bytes": 50}],
            "enhancement": {"model": ENHANCE_MODEL},
        },
    )
    assert (merged["max_z"], merged["enhanced"], merged["count"]) == (7, True, 21845)
    assert merged["bytes"] == 60 + 90
    assert merged["enhancement"]["model"] == ENHANCE_MODEL
    assert plain["max_z"] == 5, "the plain record is not mutated under the caller"

    # The layout at the new depth, on both sides of the wire.
    assert tile_relpath(7, 127, 127) == "7/127_127.png"
    assert web_tiles.map_tile_path(7, 127, 127, 7) is not None
    assert web_tiles.map_tile_path(7, 128, 0, 7) is None
    assert web_tiles.map_tile_path(8, 0, 0, 7) is None
    # ... and the same coordinate is off the end of a pyramid that was never enhanced.
    assert web_tiles.map_tile_path(7, 0, 0, 5) is None

    monkeypatch.setattr(config, "data_dir", lambda: tmp_path)
    local = tmp_path / registry.local_dir().name
    local.mkdir()
    for z, x, y in ((0, 0, 0), (7, 127, 127)):
        path = local / TILES_DIR_NAME / str(z)
        path.mkdir(parents=True)
        (path / f"{x}_{y}.png").write_bytes(_PNG)
    (local / web_tiles.MAP_BOUNDS_NAME).write_text(
        json.dumps({"_meta": {"tiles": {"tile_px": 256, "max_z": 7, "enhanced": True}}}),
        encoding="utf-8",
    )

    # The probe the page builds its tile grid from now says seven, which is the whole of
    # what makes the browser ask for the two new levels at all.
    assert client.head("/api/maptiles/0/0/0").headers["x-map-tile-max-z"] == "7"
    assert client.get("/api/maptiles/7/127/127").status_code == 200
    assert client.get("/api/maptiles/7/128/127").status_code == 404
    assert client.get("/api/maptiles/8/0/0").status_code == 404


def test_the_faint_mask_covers_weak_strokes_and_leaves_everything_else_to_the_ai(tmp_path):
    """The hybrid's one rule, on arrays where the answer is known by construction.

    ``faint_mask`` decides where the upscaler is overruled, so what has to hold is that it
    is a blend weight (in [0, 1] everywhere, or the blend is not a blend) and that it fires
    on exactly the band it claims: nothing on flat fill, nothing on a stroke deep enough
    that the model renders it well, something on a stroke shallow enough that the model
    drops it. No GPU and no upscaler is involved -- this is the mask, not the pipeline.
    """
    numpy = pytest.importorskip("numpy")

    flat = numpy.full((48, 48), 200.0, numpy.float32)
    assert faint_mask(flat).max() == 0.0, "there is nothing to protect on flat fill"

    faint = flat.copy()
    band_middle = (FAINT_LO + FAINT_HI) / 2
    faint[:, 24] = 200.0 - band_middle  # squarely inside the band
    weights = faint_mask(faint)
    assert weights.min() >= 0.0 and weights.max() <= 1.0, "a blend weight, or it is not one"
    assert weights.max() > 0.0, "a faint stroke is exactly what the mask exists for"
    assert weights[:, 24].max() == weights.max(), "and it is centred on the stroke"
    assert weights[:, 0].max() == 0.0, "while the flat fill four columns away stays untouched"

    strong = flat.copy()
    strong[:, 24] = 40.0  # far past FAINT_HI: the AI renders this better than Lanczos does
    assert faint_mask(strong).max() == 0.0

    # The band is a band, not a threshold: the same stroke at both ends of it is out.
    below = flat.copy()
    below[:, 24] = 200.0 - FAINT_LO / 2
    assert faint_mask(below).max() == 0.0


def test_the_presharpen_raises_the_weak_band_and_nothing_else():
    """The stage that runs BEFORE the model, on arrays where the answer is known.

    Repairing the output cannot put back a stroke the model never drew, so the faintest
    marks are raised on the way in. Three things have to hold for that to be a rule rather
    than a wash of contrast: it fires only on the weak band, it deepens the mark it fires
    on, and past the mask and the couple of pixels its feather reaches the source comes
    through untouched -- not "nearly", exactly, because the blend weight there is zero.

    And the fourth, which is the whole reason there are two masks: the pre-sharpen's band
    stops short of the repair's. A stroke between the two ceilings is protected on the way
    out and must NOT be amplified on the way in, because handing the model more contrast on
    a mid stroke is handing it something to expand.
    """
    numpy = pytest.importorskip("numpy")

    def square(depth):
        """A flat 200 fill with one column drawn ``depth`` luma below it, as RGB."""
        page = numpy.full((64, 64, 3), 200.0, numpy.float32)
        page[:, 32] = 200.0 - depth
        return page

    # A box mean over FAINT_WINDOW turns a drawn depth d into a measured 8d/9, which is
    # what both bands are expressed in -- so the drawn numbers here are scaled to land
    # squarely inside the intervals rather than near their ends.
    weak = square(6.5 * 9 / 8)
    mid = square(12.0 * 9 / 8)
    strong = square(60.0)

    # The band is a blend weight wherever it is used, so it is bounded like one.
    for page in (weak, mid, strong):
        band = faint_band(faint_depth(page.mean(2)), PRESHARPEN_HI)
        assert band.min() >= 0.0 and band.max() <= 1.0, "a blend weight, or it is not one"

    assert not presharpen_mask(numpy.full((64, 64), 200.0, numpy.float32)).any(), (
        "there is nothing to lift on flat fill"
    )
    assert presharpen_mask(weak.mean(2))[:, 32].all(), "the weak band is what this exists for"
    assert not presharpen_mask(strong.mean(2)).any(), "the model renders a strong stroke well"

    # The two ceilings, and the gap between them that only one mask covers.
    assert PRESHARPEN_HI < FAINT_HI
    assert faint_mask(mid.mean(2)).max() > 0.0, "the repair still protects a mid stroke"
    assert not presharpen_mask(mid.mean(2)).any(), "and the pre-sharpen leaves it alone"

    lifted, mask = presharpen_pixels(weak)
    assert lifted.min() >= 0.0 and lifted.max() <= 255.0
    assert lifted[:, 32, 0].max() < weak[:, 32, 0].min(), "the mark it fires on comes out deeper"
    assert 0.0 < mask.mean() < 0.25, "and on a small part of the square, not most of it"
    # Past the mask and its feather it is the identity, not an approximation of one.
    marked = numpy.flatnonzero(mask.any(0))
    far = numpy.ones(weak.shape[1], bool)
    far[marked.min() - 3 : marked.max() + 4] = False
    assert far.sum() > 40, "and there is a real fill left over to check that on"
    assert numpy.array_equal(lifted[:, far], weak[:, far])

    untouched, empty = presharpen_pixels(strong)
    assert not empty.any()
    assert numpy.array_equal(untouched, strong), "an empty mask blends nothing at all"


def test_the_colour_fix_hands_the_flat_fills_back_to_the_source():
    """The stage that runs after the repair, on a drift constructed to be recognised.

    The model's other measured defect is that it moves the colour of a flat fill -- by up
    to a whole level of the map's own palette, which is a visible step in a picture whose
    fills ARE its levels. So the output's low frequencies are replaced by the source's, and
    the two things that has to do are: put a flat fill back exactly where the source had
    it, and leave the detail the model was entitled to invent alone.
    """
    numpy = pytest.importorskip("numpy")

    source = numpy.full((128, 128, 3), 180.0, numpy.float32)
    source[:, 60:68] = 120.0  # a stroke, at the source's own depth

    drifted = source + 9.0  # the whole fill has wandered nine levels of grey
    drifted[:, 60:68] = 100.0  # and the model has deepened the stroke, which is its business

    fixed = colour_fix_pixels(drifted, source)
    assert fixed.min() >= 0.0 and fixed.max() <= 255.0

    # Well away from the stroke and from the edges -- further than the blur reaches -- the
    # fill is back at the source's own value, and the drift is gone rather than reduced.
    fill = fixed[40:88, 100:124]
    assert abs(float(fill.mean()) - 180.0) < 0.01
    assert float(numpy.abs(fill - 180.0).max()) < 0.01
    assert abs(float(drifted[40:88, 100:124].mean()) - 189.0) < 0.01, "there was a drift to fix"

    # The stroke is still deeper than the source drew it: the fix protects the sharpening
    # rather than blurring it back, which is what sigma exceeding a stroke's width buys.
    assert fixed[:, 64, 0].max() < source[:, 64, 0].min()
    assert COLOUR_FIX_SIGMA > 4.0, "or the blur would sit inside a stroke at 4x"

    # A source that never drifted is left where it is, to within rounding.
    assert float(numpy.abs(colour_fix_pixels(source, source) - source).max()) < 1e-3


def test_an_enhanced_pyramid_is_not_quietly_replaced_by_a_plain_one(tmp_path):
    """The no-silent-downgrade rule, and the sidecar round trip it reads through.

    The cross-build guard already refuses to overwrite somebody else's artwork. This is its
    other half: a re-run that would cost the reader the two zoom levels they generated last
    time is drift too, and the same posture applies -- announce it, do not perform it. Only
    one of the four combinations is a downgrade, and asserting all four is what keeps the
    rule from quietly becoming "refuse whenever anything was enhanced".

    The flag has to survive JSON to be worth anything, so it is read back out of the file
    the tool really writes rather than out of the dict it built.
    """
    pin = "buildVersion 495413 (engine branch ++FactoryGame+rel-main-1.2.0), the installed build"
    common = dict(
        build_pin=pin,
        build_raw={"Changelist": 495413},
        image={"file": IMAGE_NAME},
        integrity={},
        layout={"layout_holds": True},
        calibration={"pin_holds": True},
        versions={"pillow": "12.3.0"},
    )
    enhancement = {
        "recipe": ENHANCE_RECIPE,
        "recipe_name": ENHANCE_RECIPES[ENHANCE_RECIPE],
        "model": ENHANCE_MODEL,
        "scale": ENHANCE_SCALE,
        "source_tile_px": ENHANCE_TILE_PX,
        "overlap_px": ENHANCE_OVERLAP_PX,
        "binary": {"url": ENHANCE_URL, "sha256": ENHANCE_SHA256},
        "presharpen": {
            "rounds": PRESHARPEN_ROUNDS,
            "amount": PRESHARPEN_AMOUNT,
            "band": [FAINT_LO, PRESHARPEN_HI],
            "mask_coverage": 0.0431,
        },
        "hybrid": {
            "band": [FAINT_LO, FAINT_HI],
            "mask_coverage": 0.0355,
        },
        "colour_fix": {"sigma_px": COLOUR_FIX_SIGMA},
        "timings_s": {"upscale": 66.7, "colour_fix": 320.4, "total": 400.0},
    }
    sharp = artwork_output.build_sidecar(
        tiles={"tile_px": 256, "max_z": 7, "enhanced": True, "enhancement": enhancement},
        **common,
    )
    plain = artwork_output.build_sidecar(
        tiles={"tile_px": 256, "max_z": 5, "enhanced": False}, **common
    )

    path = tmp_path / SIDECAR_NAME
    path.write_text(json.dumps(sharp, indent=1, allow_nan=False), encoding="utf-8")
    read_back = json.loads(path.read_text(encoding="utf-8"))
    assert pinned_enhanced(read_back) is True
    # Everything the sidecar promised about that stage is still in it, and pinned.
    written = read_back["_meta"]["tiles"]["enhancement"]
    assert written["binary"]["sha256"] == ENHANCE_SHA256
    assert written["binary"]["url"].endswith(".zip")
    assert (written["model"], written["scale"]) == (ENHANCE_MODEL, 4)
    assert (written["source_tile_px"], written["overlap_px"]) == (1024, 96)
    assert written["timings_s"]["total"] == 400.0
    # Both new stages, with the parameters that make them reproducible, and the recipe that
    # names the whole of it -- a reader must be able to tell which pipeline cut these tiles.
    assert written["recipe"] == ENHANCE_RECIPE
    assert written["recipe_name"] == ENHANCE_RECIPES[ENHANCE_RECIPE]
    assert (written["presharpen"]["rounds"], written["presharpen"]["amount"]) == (3, 0.14)
    assert written["presharpen"]["band"] == [FAINT_LO, PRESHARPEN_HI]
    assert written["hybrid"]["band"] == [FAINT_LO, FAINT_HI]
    assert written["presharpen"]["band"][1] < written["hybrid"]["band"][1]
    assert written["colour_fix"]["sigma_px"] == COLOUR_FIX_SIGMA
    # The build pin still reads through the same file, so the two guards do not shadow.
    assert pinned_build(read_back) == pin

    # Anything that is not a literal true is a plain pyramid, including every sidecar
    # written before this stage existed.
    assert pinned_enhanced(plain) is False
    assert pinned_enhanced({}) is False
    assert pinned_enhanced({"_meta": {"tiles": {}}}) is False
    assert pinned_enhanced({"_meta": {"tiles": {"enhanced": "yes"}}}) is False
    assert pinned_enhanced({"_meta": {"tiles": "not a mapping"}}) is False

    # The recipe survives the same round trip, and a sidecar from before recipes existed
    # reads as the one pipeline the bare boolean can have meant.
    older = json.loads(json.dumps(artwork_output.build_sidecar(tiles={"enhanced": True}, **common)))
    assert pinned_recipe(read_back) == ENHANCE_RECIPE
    assert pinned_recipe(older) == UNNUMBERED_RECIPE == 1
    assert pinned_recipe(plain) == 0
    assert pinned_recipe({}) == 0
    # Nothing but a whole number above zero is believed; the boolean decides the rest.
    for junk in (True, "2", 2.0, 0, -1, None):
        assert pinned_recipe({"_meta": {"tiles": {"enhancement": {"recipe": junk}}}}) == 0

    # And the rule itself, which compares recipes rather than a flag: only a run BEHIND
    # what is on disk is refused.
    assert enhancement_downgrades(read_back, enhance_now=False) is True
    assert enhancement_downgrades(read_back, enhance_now=True) is False
    assert enhancement_downgrades(plain, enhance_now=False) is False
    assert enhancement_downgrades(plain, enhance_now=True) is False
    assert enhancement_downgrades({}, enhance_now=False) is False
    # An amended pipeline over the recipe it amends is an upgrade, and must not be called
    # a downgrade -- that refusal is what a re-cut with this file would otherwise hit.
    assert ENHANCE_RECIPE > UNNUMBERED_RECIPE
    assert enhancement_downgrades(older, enhance_now=True) is False
    assert enhancement_downgrades(older, enhance_now=False) is True
    # ... and the same tiles re-cut by the recipe that drew them is a refresh, not a loss.
    assert enhancement_downgrades(read_back, enhance_now=True, recipe=ENHANCE_RECIPE) is False
    # The one case the number adds: an older checkout over a newer recipe's tiles.
    assert enhancement_downgrades(read_back, enhance_now=True, recipe=1) is True

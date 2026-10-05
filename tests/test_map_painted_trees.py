"""The painted layer's colour fixes: crowns on the canopy targets, the opaque water gated by
its class, rock families relative to their common tint, the render-only meshes, and the
ground the bake hides.

docs/spatial-and-map.md sections 30 to 32, 36 and 37. Synthetic fixtures: no install.
"""

from __future__ import annotations

import copy
from types import SimpleNamespace

import pytest

np = pytest.importorskip("numpy")
pytest.importorskip("scipy")

from mapgen.gamedata import rockfamily  # noqa: E402
from mapgen.gamedata.crowns import CROWN_RECORD, SPRITE_M  # noqa: E402
from mapgen.gamedata.waterbodies import CLASSES, OCEAN  # noqa: E402
from mapgen.palette.calibration import (  # noqa: E402
    display_to_crown,
    display_to_ground,
    scoped_planes,
    weighted_median,
)
from mapgen.palette.optics import class_optics, opaque_share, underwater, water_table  # noqa: E402
from mapgen.palette.painted import hidden_ground, painted_colours  # noqa: E402
from mapgen.palette.styles import PAINTED_PALETTE  # noqa: E402
from mapgen.palette.surfaces import (  # noqa: E402
    family_tables,
    mesh_surface,
    rock_surface,
    sunk_specks,
)
from mapgen.palette.trees import (  # noqa: E402
    IDENTITY_OP,
    crown_lab,
    crown_ops,
    hue_gate,
    over_crowns,
)
from mapgen.terrain.rasters import MESH_CORAL, MESH_ROCK, MESH_SHELL  # noqa: E402
from mapgen.terrain.sample import taps_linear  # noqa: E402

SWAMP = CLASSES.index("swamp")
STYLE = PAINTED_PALETTE["crowns"]
GREEN, RED = (0.06, 0.12, 0.035), (0.22, 0.04, 0.04)


# ----------------------------------------------------------------------- crowns


def _crowns(colours, species, xs, scale=1.0):
    """Fake crowns: one 4x4 fully covered sprite per colour, one record per tree."""
    levels = []
    for colour in colours:
        level = np.zeros((4, 4, 6), np.float32)
        level[..., 0] = 1.0
        level[..., 1:4] = colour
        levels.append([level])
    records = np.zeros(len(species), CROWN_RECORD)
    records["species"], records["x"], records["scale"] = species, xs, scale
    return SimpleNamespace(levels=levels, records=records)


def test_crowns_move_their_median_onto_each_scopes_target():
    crowns = _crowns([GREEN, RED], [0] * 30 + [1] * 30, [0.0] * 30 + [1.0] * 30)
    cells = (np.zeros(60, np.int64), np.repeat([0, 1], 30))
    plane = np.array([[0.0, 1.0]], np.float32)
    jungle = display_to_crown(PAINTED_PALETTE, "#7c4955")
    canopy = display_to_crown(PAINTED_PALETTE, "#558653")
    ops, measured = crown_ops(crowns, STYLE, cells, [(plane, jungle), (None, canopy)], 10)
    for op, colour, target in ((ops[0], RED, jungle), (ops[1], GREEN, canopy)):
        lab = crown_lab(np.array(colour, np.float32), STYLE)
        lab[0] += op[0]
        lab[1:] = op[1:5].reshape(2, 2) @ lab[1:]
        np.testing.assert_allclose(lab, target, atol=2e-3)
    assert measured["crowns@0"]["trees"] == 30 and measured["crowns@1"]["trees"] == 30
    few, _ = crown_ops(crowns, STYLE, cells, [(plane, jungle), (None, canopy)], 31)
    assert few == [None, None], "a scope with too few trees keeps no op"


def test_a_canopy_target_leaves_crowns_of_another_hue_alone():
    pink, blue = (0.44, 0.08, 0.11), (0.27, 0.40, 0.46)
    crowns = _crowns([GREEN, pink, blue], [0] * 10 + [1] * 40 + [2] * 40, [0.0] * 90)
    cells = (np.zeros(90, np.int64), np.zeros(90, np.int64))
    canopy = display_to_crown(PAINTED_PALETTE, "#558653")
    ops, measured = crown_ops(crowns, STYLE, cells, [(None, canopy)], 5)
    assert measured["crowns@0"]["trees"] == 10, "only the green crowns are measured"
    lab = crown_lab(np.array([GREEN, pink, blue], np.float32), STYLE)
    gate = hue_gate(lab, ops[0][5:7])
    assert gate[0] == 1.0 and gate[1] == 0.0 and gate[2] == 0.0


def test_a_crowns_weight_is_the_ground_it_hides():
    crowns = _crowns([GREEN, RED], [0, 1, 1], [0.0, 0.0, 0.0], scale=1.0)
    crowns.records["scale"] = [10.0, 1.0, 1.0]
    cells = (np.zeros(3, np.int64), np.zeros(3, np.int64))
    target = display_to_crown(PAINTED_PALETTE, "#558653")
    ops, _ = crown_ops(crowns, STYLE, cells, [(None, target)], 1)
    lab = crown_lab(np.array(GREEN, np.float32), STYLE)
    assert lab[0] + ops[0][0] == pytest.approx(target[0], abs=2e-3), "one big tree outweighs two"


def test_the_identity_op_draws_crowns_as_before():
    ground = np.full((1, 3, 3), 0.5, np.float32)
    shape = (1, 3)
    terms = {"cover": np.ones(shape, np.float32), "top_cm": np.full(shape, 1500.0, np.float32),
             "rgb": np.tile(np.array(GREEN, np.float32), (*shape, 1)),
             "ndl": np.full(shape, 0.7, np.float32)}  # fmt: skip
    scene = {"z_m": np.zeros(shape, np.float32), "ndl_flat": np.float32(0.7),
             "water": {"cover": np.zeros(shape, np.float32)}}  # fmt: skip
    plain = over_crowns(ground, terms, scene, PAINTED_PALETTE, 0.4, 1.0)
    same = over_crowns(ground, terms, scene, PAINTED_PALETTE, 0.4, 1.0, IDENTITY_OP)
    np.testing.assert_allclose(same, plain, atol=1e-6)
    green = crown_lab(np.array(GREEN, np.float32), STYLE)[1:]
    lift = np.array([0.1, 0, 0, 0, 0, *(green / np.hypot(*green))], np.float32)
    lifted = over_crowns(ground, terms, scene, PAINTED_PALETTE, 0.4, 1.0, IDENTITY_OP + lift)
    assert (lifted.sum(-1) > plain.sum(-1)).all()


def test_scoped_planes_and_the_weighted_median_take_any_width():
    planes = scoped_planes(IDENTITY_OP, [(np.array([[0.0, 1.0]], np.float32), IDENTITY_OP * 2)])
    assert len(planes) == 7 and planes[1].tolist() == [[1.0, 2.0]]
    values = np.array([[1.0], [2.0], [3.0]], np.float32)
    assert weighted_median(values, np.array([1.0, 1.0, 5.0]))[0] == 3.0
    assert (
        display_to_crown(PAINTED_PALETTE, "#558653")[0]
        > display_to_ground(PAINTED_PALETTE, "#558653")[0]
    ), "a crown gets no altitude lift"


# ----------------------------------------------------------------------- opaque water


def _taps(cols):
    return taps_linear(np.zeros(1, np.float32), 4), taps_linear(np.float32(cols), 4)


def test_the_class_share_of_the_opaque_water_follows_the_class_plane():
    plane = np.full((4, 4), OCEAN, np.uint8)
    plane[:, 2:] = SWAMP
    rows = water_table(PAINTED_PALETTE)
    optics = class_optics(plane, rows, {}, _taps([0, 1.5, 2, 3]), shares=(SWAMP,))
    np.testing.assert_allclose(optics["share"][SWAMP], [[0.0, 0.5, 1.0, 1.0]], atol=1e-6)
    sea = class_optics(np.full((4, 4), OCEAN, np.uint8), rows, {}, _taps([0, 1]), shares=(SWAMP,))
    assert sea is None, "a band of ocean draws the sea's own optics"
    water = {"ocean": np.zeros((1, 4), np.float32)}
    assert opaque_share(SWAMP, None, water, classified=True) is None, "ocean never takes it"
    assert (opaque_share(SWAMP, None, water, classified=False) == 1.0).all()


def _ground(opaque):
    p = PAINTED_PALETTE
    w = p["water"]
    lin = lambda c: (np.asarray(c, np.float32) / 255) ** 2.2
    return SimpleNamespace(
        palette=p, carpet=None, opaque_water=opaque, water_class=np.zeros((1, 1), np.uint8),
        water={"k": np.asarray(w["k_per_m"], np.float32), "body": lin(w["body"]),
               "sky": np.zeros(3, np.float32), "deep": lin(w["deep"]),
               "deep_tau_m": np.float32(12.0), "bed": np.float32(0.8),
               "inland_floor": np.float32(0.0), "opaque_tau_m": np.float32(0.3)},
    )  # fmt: skip


def test_ocean_inside_the_swamp_area_keeps_the_sea():
    shape = (1, 2)
    water = {"depth_m": np.full(shape, 3.0, np.float32), "ocean": np.ones(shape, np.float32)}
    scene = {"water": water, "water_optics": None}
    mud = np.array([0.4, 0.2, 0.2], np.float32)
    opaque = [(np.ones(shape, np.float32), mud, SWAMP)]
    g = np.full((*shape, 3), 0.2, np.float32)
    same = lambda plane: plane
    sea = underwater(g, scene, _ground(opaque), same, same, np.float32(1.0))
    np.testing.assert_allclose(sea, underwater(g, scene, _ground([]), same, same, 1.0))
    optics = {**_ground([]).water, "share": {SWAMP: np.array([[1.0, 0.0]], np.float32)}}
    swamp = underwater(g, dict(scene, water_optics=optics), _ground(opaque), same, same, 1.0)
    np.testing.assert_allclose(swamp[0, 0], mud, atol=1e-3)
    np.testing.assert_allclose(swamp[0, 1], sea[0, 1], atol=1e-6)


# ----------------------------------------------------------------------- rock and meshes


def test_rock_keeps_its_target_under_the_common_tint_and_a_family_keeps_its_departure():
    tint = [0.62, 0.55, 0.47]
    families = {"cliff": {"tint": tint}, "grass": {"tint": tint, "top": [0.1, 0.2, 0.05]},
                "sand": {"tint": [0.31, 0.275, 0.235]}}  # fmt: skip
    ratio, top, has = family_tables(families)
    names = rockfamily.FAMILIES
    np.testing.assert_allclose(ratio[names.index("cliff")], 1.0, atol=1e-6)
    np.testing.assert_allclose(ratio[names.index("sand")], 0.5, atol=1e-3)
    np.testing.assert_allclose(ratio[0], 1.0)
    assert has[names.index("grass")] == 1.0 and has[names.index("cliff")] == 0.0
    ground = SimpleNamespace(rock_family=np.full((4, 4), names.index("cliff"), np.uint8),
                             family_tint=ratio, family_top=top, family_has_top=has,
                             palette={"rock_top": {"up": [0.6, 0.85]}})  # fmt: skip
    rock = np.full((4, 4, 3), 0.3, np.float32)
    scene = {"z_m": np.zeros((4, 4), np.float32), "grid": (slice(0, 4), 0, 4, 0, 4, 1.0)}
    np.testing.assert_allclose(rock_surface(rock, scene, ground), rock, atol=1e-6)


def _mesh_scene(cls, cover):
    shape = (1, len(cls))
    return {
        "mesh_weight": np.ones(shape, np.float32),
        "mesh_class": np.array([cls], np.uint8),
        "water": {"cover": np.array([cover], np.float32), "ocean": np.zeros(shape, np.float32),
                  "depth_m": np.full(shape, 40.0, np.float32)},
    }  # fmt: skip


def test_wet_coral_is_seabed_dry_coral_is_coral_and_rocks_wear_the_area_rock():
    coral, shell = np.array([0.4, 0.3, 0.35], np.float32), np.array([0.1, 0.1, 0.1], np.float32)
    seabed = np.array([0.1, 0.2, 0.3], np.float32)
    ground = SimpleNamespace(mesh_rgb={MESH_CORAL: coral, MESH_SHELL: shell}, seabed_coral=seabed)
    area_rock = np.full((1, 4, 3), 0.25, np.float32)
    g = np.zeros((1, 4, 3), np.float32)
    scene = _mesh_scene([MESH_CORAL, MESH_CORAL, MESH_SHELL, MESH_ROCK], [1.0, 0.0, 0.0, 0.0])
    out = mesh_surface(g, area_rock, scene, ground, lambda plane: plane)
    np.testing.assert_allclose(out[0, 0], seabed, err_msg="coral under water")
    np.testing.assert_allclose(out[0, 1], coral, err_msg="dry land, whatever depth_m says")
    np.testing.assert_allclose(out[0, 2], shell)
    np.testing.assert_allclose(out[0, 3], 0.25, err_msg="the area's rock, no cliff family")


def test_a_coral_speck_in_the_sea_is_drawn_as_the_water_around_it():
    cls = np.zeros((5, 5), np.uint8)
    cls[2, 2] = MESH_CORAL
    cls[0:2, 0:2] = MESH_CORAL
    cover = np.ones((5, 5), np.float32)
    cover[2, 2] = 0.0
    cover[0:2, 0:2] = 0.0
    depth = np.full((5, 5), 1.5, np.float32)
    depth[2, 2] = depth[0:2, 0:2] = 0.0
    scene = {"mesh_weight": (cls > 0).astype(np.float32), "mesh_class": cls,
             "water": {"cover": cover, "depth_m": depth}}  # fmt: skip
    water = sunk_specks(scene)
    assert water["cover"][2, 2] == 1.0 and water["depth_m"][2, 2] == pytest.approx(1.5)
    assert water["cover"][0, 0] == 0.0, "coral wider than a pixel keeps its own surface"
    scene["mesh_weight"][:] = 0.0
    assert sunk_specks(scene) is scene["water"]


def test_the_painted_palette_carries_the_fixed_colours():
    p = PAINTED_PALETTE
    swamp = [e for e in p["calibration"]["areas"] if "water" in e]
    assert swamp and all(e["water_class"] in CLASSES for e in swamp)
    shell = np.asarray(p["mesh_colours"]["shell"], np.float32)
    assert shell.max() < 120 and np.ptp(shell) < 10, "the shells' own grey, not cream"
    assert p["carpet"]["blur_m"] >= 2.0, "patches, not one dot per rosette"


# ----------------------------------------------------------------------- hidden ground


def test_only_holes_the_bake_encloses_are_hidden():
    ok = np.ones((12, 12), bool)
    ok[4:7, 4:7] = False
    ok[:, 10:] = False
    hidden = hidden_ground(ok)
    assert hidden[4:7, 4:7].all() and hidden.sum() == 9, "the edge strip is no-bake, not hidden"


def test_the_painted_band_draws_with_the_whole_chain():
    p = copy.deepcopy(PAINTED_PALETTE)
    shape = (1, 2)
    w = p["water"]
    lin = lambda c: (np.asarray(c, np.float32) / 255) ** 2.2
    ground = SimpleNamespace(
        palette=p, albedo=[np.full(shape, 0.2, np.float32)] * 3, canopy=np.zeros(shape),
        canopy_rgb=np.zeros(3, np.float32), rock=[np.full(shape, 0.2, np.float32)] * 3,
        rock_family=None, crown=None, titan=None, carpet=None, mesh_rgb={},
        seabed_coral=np.zeros(3, np.float32), opaque_water=[], crown_op=None,
        ramp=(0.0, 1.0, np.linspace(0, 1, 5)),
        water={"k": np.asarray(w["k_per_m"], np.float32), "body": lin(w["body"]),
               "sky": np.zeros(3, np.float32), "deep": lin(w["deep"]),
               "deep_tau_m": np.float32(12.0), "bed": np.float32(0.8),
               "inland_floor": np.float32(0.0)},
    )  # fmt: skip
    zero = np.zeros(shape, np.float32)
    scene = {"z_m": zero, "borrow": zero + 1, "ndl": zero + 0.7, "ndl_flat": np.float32(0.7),
             "rock_weight": zero,
             "water": {"cover": np.array([[0.0, 1.0]], np.float32), "depth_m": zero + 1.0,
                       "ocean": zero + 1, "edge": zero, "above_m": zero + 9, "below_m": zero + 9}}  # fmt: skip
    out = painted_colours(scene, ground, lambda a: a, lambda a: a)
    assert out.shape == (1, 2, 3) and not np.allclose(out[0, 0], out[0, 1])
    assert SPRITE_M > 0

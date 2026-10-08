"""The game's billboards as crown sprites: the reader, the top view's placement on the mesh
footprint, the halves of its normal, and the texture reads they rest on.

docs/map/light-and-crowns.md section 36, "Crown sprites". Synthetic atlases and footprints:
no install.
"""

from __future__ import annotations

import struct

import numpy as np
import pytest

from mapgen.gamedata.ground.landscape_albedo import mip_shape, texture_size
from mapgen.gamedata.vegetation import billboards as bb
from mapgen.gamedata.vegetation import tree_surface as ts
from mapgen.sprites import align, build
from mapgen.sprites.raster import SPRITE_CM, SpritePlanes

OCTA = "/Game/FactoryGame/Tools/BillboardGenerator/MM_OctaBillboardMat"
SIDE = 24  # an atlas of 3 x 3 frames of 8


def _chain(parent, textures=None, scalar=None):
    return [{"scalar": scalar or {}, "vector": {}, "texture": textures or {}, "parent": parent}]


def test_the_billboard_is_found_by_its_master_and_an_octahedral_one_comes_first(monkeypatch):
    chains = {
        "leaf": _chain("/Game/X/MM_WindPlants"),
        "imp": _chain(
            "/Game/X/Imposter_Master", {"BaseColor_Alpha": "/Game/A", "Normal": "/Game/B"}
        ),
        "octa": _chain(OCTA, {"Albedo": "/Game/C", "Normal": "/Game/D"}, {"Brightness": 2.0}),
    }
    monkeypatch.setattr(bb, "parameter_chain", lambda _game, path: chains[path])
    found = bb.find_billboard(None, ["leaf", None, "imp", "octa"])
    assert found is not None
    assert (found.kind, found.colour, found.normal) == (bb.OCTAHEDRAL, "/Game/C", "/Game/D")
    only = bb.find_billboard(None, ["leaf", "imp"])
    assert only is not None and only.kind == bb.IMPOSTOR
    assert bb.top_view(only, lambda _p, _s: np.zeros((SIDE, SIDE, 4), np.uint8)) is None
    assert bb.find_billboard(None, ["leaf"]) is None


def test_the_top_view_is_the_corner_frame_its_alpha_inverted_and_no_gain_applied():
    colour = np.zeros((SIDE, SIDE, 4), np.uint8)
    colour[..., 3] = 255
    colour[16:, 16:, :3] = (10, 40, 20)
    colour[18:22, 18:22, 3] = 0  # covered where the alpha is low
    normal = np.zeros((SIDE // 2, SIDE // 2, 4), np.uint8)
    normal[8:, 8:, 2] = 200  # half size: zoomed onto the colour's frame
    board = bb.Billboard(bb.OCTAHEDRAL, "m", "c", "n", {"Brightness": 2.0})
    view = bb.top_view(board, lambda path, _side: colour if path == "c" else normal)
    assert view is not None
    assert view.alpha.shape == (8, 8) and view.alpha[2:6, 2:6].min() == 1.0
    assert view.alpha.sum() == 16.0
    assert np.allclose(view.colour[0, 0], np.array((10, 40, 20)) / 255.0), "linear, no gain"
    assert view.normal_plus.shape == (8, 8, 3)
    assert np.allclose(view.normal_plus[..., 2], 200 / 255.0)


def _footprint(x_cm, y_cm):
    """An L in mesh cm about the pivot: no turn or mirror maps it onto itself."""
    long_arm = (x_cm > -300) & (x_cm < 500) & (y_cm > -300) & (y_cm < -100)
    short_arm = (x_cm > -300) & (x_cm < -100) & (y_cm > -300) & (y_cm < 300)
    return (long_arm | short_arm).astype(np.float32)


def _raster():
    shape = (60, 80)
    x0, y0 = -500.0, -375.0
    xs = x0 + (np.arange(shape[1]) + 0.5) * SPRITE_CM
    ys = y0 + (np.arange(shape[0]) + 0.5) * SPRITE_CM
    alpha = _footprint(xs[None, :], ys[:, None])
    normal = np.zeros((*shape, 3), np.float32)
    normal[..., 2] = 1.0
    return SpritePlanes(x0, y0, alpha, np.zeros((*shape, 3), np.float32), normal, alpha * 900)


def _frame(width_cm, side=200):
    """The footprint as the generator would bake it, as decoded: centred on the pivot,
    ``width_cm`` across, turned back by the convention."""
    centres = (np.arange(side) + 0.5) / side * width_cm - width_cm / 2
    turned = _footprint(centres[None, :], centres[:, None])
    return np.ascontiguousarray(np.rot90(turned, -align.OCTA_TURNS))


def test_the_frame_widths_are_twice_the_top_and_the_sphere_round_the_bounds_centre():
    verts = np.array([[0, 0, 0], [300, 0, 1000], [-100, 400, 600]], np.float32)
    low, high = np.array([-100.0, 0.0, 0.0]), np.array([300.0, 400.0, 1000.0])
    widths = align.frame_widths(verts, low, high)
    assert widths["height"] == 2000.0
    assert widths["sphere"] == pytest.approx(2 * np.linalg.norm([200.0, 200.0, 500.0]))


@pytest.mark.parametrize("rule", align.FRAME_RULES)
def test_a_top_view_lands_on_its_footprint_by_the_rule_that_fits(rule):
    widths = {"height": 2400.0, "sphere": 1600.0}
    alpha = _frame(widths[rule])
    view = bb.TopView(
        colour=np.full((*alpha.shape, 3), 0.2, np.float32),
        alpha=alpha,
        normal_plus=np.dstack([np.zeros_like(alpha), np.zeros_like(alpha), alpha]),
        billboard=bb.Billboard(bb.OCTAHEDRAL, "m", "c", "n", {}),
    )
    raster = _raster()
    placement, placed = align.place_view(view, raster, widths)
    assert placement.rule == rule and placement.iou > 0.9
    assert placement.texel_m == pytest.approx(widths[rule] / 200 / 100)
    planes = build.atlas_planes(raster, placed)
    assert align.iou(planes.alpha, raster.alpha) == pytest.approx(placement.iou)
    assert np.allclose(planes.colour[planes.alpha > 0.5], 0.2, atol=1e-4)
    assert np.allclose(planes.normal[planes.alpha > 0.5], (0, 0, 1), atol=1e-3)
    assert np.all(planes.top_cm[planes.alpha > 0] == 900), "the raster's top carried over"
    mirrored = bb.TopView(
        view.colour, np.ascontiguousarray(alpha[:, ::-1]), view.normal_plus, view.billboard
    )
    assert align.place_view(mirrored, raster, widths)[0].iou < 0.6, "the turn is a convention"


def test_a_normal_s_lost_negative_half_is_put_back_on_the_axis_that_read_zero():
    halves = np.array([[[0.0, 0.0, 0.8]], [[0.6, 0.0, 0.8]], [[0.0, 0.0, 0.6]]], np.float32)
    hint = np.array([[[-1.0, 0.0, 0.0]], [[0.0, 0.0, 1.0]], [[-0.5, -0.5, 0.7]]], np.float32)
    n = align.normal_from_halves(halves, np.ones((3, 1), np.float32), hint)
    assert np.allclose(n[0, 0], (-0.6, 0.0, 0.8), atol=0.02)
    assert np.allclose(n[1, 0], (0.6, 0.0, 0.8), atol=1e-4)
    assert np.allclose(n[2, 0], (-0.5657, -0.5657, 0.6), atol=0.01), "even where both read 0"


def test_padding_and_trimming_move_the_corner_and_keep_the_footprint():
    raster = _raster()
    padded = build.pad_planes(raster, 3)
    trimmed = build.trim_planes(padded)
    assert trimmed.alpha.shape == (48 + 2, 64 + 2) and trimmed.alpha.sum() == raster.alpha.sum()
    assert trimmed.alpha[0].max() == 0 and trimmed.alpha[1].max() == 1
    left = trimmed.x0_cm + 1.5 * SPRITE_CM
    assert left == pytest.approx(-300 + SPRITE_CM / 2), "the first covered texel's centre"
    assert padded.alpha.shape == (66, 86) and padded.alpha.sum() == raster.alpha.sum()
    assert (padded.x0_cm, padded.y0_cm) == (
        raster.x0_cm - 3 * SPRITE_CM,
        raster.y0_cm - 3 * SPRITE_CM,
    )


# ------------------------------------------------------------------------ texture reads


def test_a_texture_s_size_is_read_ahead_of_its_pixel_format_and_a_mip_takes_its_aspect():
    body = b"\x07" * 9 + struct.pack("<3i", 2048, 1024, 1) + struct.pack("<i", 8) + b"PF_DXT5\0"
    assert texture_size(body, "PF_DXT5") == (2048, 1024)
    assert texture_size(b"nothing here", "PF_DXT5") is None
    assert mip_shape((2048, 1024), 512 * 256 * 16 // 4, 16, 4) == (1024, 512)
    assert mip_shape((512, 2048), 128 * 512 * 8, 8, 4) == (512, 2048)
    assert mip_shape((512, 2048), 32 * 128 * 8, 8, 4) == (128, 512)
    assert mip_shape(None, 256 * 256, 16, 4) == (256, 256), "no header: the square"
    assert mip_shape((64, 64), 64 * 64 * 4, None, 4) == (64, 64)


def test_a_slot_s_shading_is_its_normal_map_moss_and_spherical_normals():
    normal = np.zeros((8, 8, 4), np.uint8)
    normal[..., 0], normal[..., 1] = 204, 128  # x 0.6, y 0
    moss = np.full((8, 8, 4), 255, np.uint8)
    chain = [
        {
            "scalar": {"Contrast": 3.0, "Fall Off": 0.4, "Spherical Normals Influence": 0.9},
            "vector": {
                "Moss Color Tint": (0.5, 1.0, 0.25, 1.0),
                "1.2 Style wind Crown Pivot": (0.0, 0.0, 1000.0, 1.0),
            },
            "texture": {"Normal": "nor", "Baked Normal": "baked", "Moss Albedo": "moss"},
            "parent": None,
        }
    ]
    got = ts.slot_shading(chain, 8, lambda p, _s: {"nor": normal, "moss": moss}[p])
    assert got.normal_map is not None
    assert np.allclose(got.normal_map[0, 0], (0.6, 0.0, 0.8), atol=0.01), "Normal before Baked"
    assert got.moss == pytest.approx((0.5, 1.0, 0.25)), "the texture's mean, tinted"
    assert (got.moss_low, got.moss_gain) == pytest.approx((0.6, 7.5))
    assert (got.sphere, got.pivot_cm) == (pytest.approx(0.9), (0.0, 0.0, 1000.0))
    plain = ts.slot_shading(_chain(None), 8, lambda p, _s: normal)
    assert plain.normal_map is None and plain.moss is None and plain.sphere == 0.0


def test_a_leaf_s_mask_is_the_packed_map_s_blue_before_any_alpha():
    rgba = np.zeros((16, 16, 4), np.uint8)
    rgba[..., :3] = 128
    rgba[..., 3] = np.linspace(0, 255, 16).astype(np.uint8)[None, :]  # subsurface: a gradient
    orma = np.zeros((16, 16, 4), np.uint8)
    orma[..., 3] = 255
    orma[:, :6, 2] = 255
    chain = _chain(None, {"Albedo": "alb", "ORMA": "orma"}, {"Brightness": 0.5})
    found = ts.slot_texture(chain, "leaf", 16, lambda p, _s: rgba if p == "alb" else orma)
    assert found.masked and np.array_equal(found.alpha, orma[..., 2])
    linear = ((128 / 255 + 0.055) / 1.055) ** 2.4
    assert np.allclose(found.albedo, linear * 0.5, atol=1e-5)
    bark = ts.slot_texture(chain, "bark", 16, lambda p, _s: rgba if p == "alb" else orma)
    assert not bark.masked and bark.alpha.min() == 255, "a bark is never cut"
    assert ts.slot_texture(chain, "skip", 16, lambda p, _s: rgba).kind == "skip"

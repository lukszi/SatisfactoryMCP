"""The rock look's normal maps under a live sun: where the light carries the detail normal,
the painted layer keeps the rock's albedo and the light takes the maps' tilt from the surface
it captures, whatever layers a run draws, so the relief turns with the sun and the default sun
draws as before.

docs/map/painted.md section 30, "Rock textures". Synthetic fixtures: no install.
"""

from __future__ import annotations

import copy
from types import SimpleNamespace

import numpy as np
import pytest
from scipy import ndimage

from mapgen.cache import MeshPlanes
from mapgen.colour import by_luminance, linear_to_srgb, tone
from mapgen.gamedata.rocks.families import FAMILIES
from mapgen.lighting.horizon import normals
from mapgen.lighting.light_tiles import DETAIL_SCALE, normal_byte, with_detail
from mapgen.lighting.model import apply_terms, shaded_direct
from mapgen.lighting.sun import DEFAULT_SUN
from mapgen.palette.lightparams import shader_light
from mapgen.palette.painted.rock_look.atlas import KIND_CLIFF, rock_look
from mapgen.palette.painted.rock_look.surface import band_look
from mapgen.palette.painted.rock_look.tilt import RockTilt, TiltPlanes, band_tilt, tilt_of
from mapgen.palette.painted.surfaces import rock_surface
from mapgen.palette.styles import PAINTED_PALETTE
from mapgen.render.draw import painting
from mapgen.render.ground.detail import with_tilt
from mapgen.terrain.ground_detail.textures import load_detail_textures
from mapgen.terrain.render_meshes import MESH_CORAL, MESH_SHELL
from tests.mapgen.test_ground_detail_light import N, _Capture, _field, _painted_ground
from tests.support.draw import draw_layers
from tests.support.ground_textures import write_textured_store
from tests.support.rock_textures import rock_textures

pytestmark = pytest.mark.filterwarnings("ignore:CUDA path could not be detected:UserWarning")

SP = 0.25
GRASS = FAMILIES.index("grass")
#: A sun from the far side of noon's.
EVENING = (45.0, 35.0)


def _look(spacing_m: float = SP):
    """Rock textures whose normal maps tilt by up to about 20 degrees, as the install's do."""
    textures = rock_textures()
    for name, texture in textures.textures.items():
        if name.endswith(("normal", "detail")):
            soft = 128 + (texture[..., :2].astype(np.int16) - 128) * 3 // 10
            texture[..., :2] = soft.astype(np.uint8)
    return rock_look(textures, spacing_m)


def _hills(n: int) -> np.ndarray:
    """A rolling slope ``n`` pixels on a side with a one-pixel margin."""
    yy, xx = np.mgrid[0 : n + 2, 0 : n + 2].astype(np.float32) * SP
    return (3 * np.sin(xx / 4.0) * np.cos(yy / 6.0) + 0.5 * xx).astype(np.float32)


def _ground(look):
    n = len(FAMILIES)
    return SimpleNamespace(
        rock_family=None, rock_top_rule=None, top_overrides=[], family_rock={},
        family_tint=np.ones((n, 3), np.float32), family_top=np.zeros((n, 3), np.float32),
        family_has_top=np.zeros(n, np.float32), palette=copy.deepcopy(PAINTED_PALETTE),
        family_top_rgb={}, rock_look=look, arch_rgb=np.float32([0.3, 0.28, 0.25]),
        top_family_rgb={}, cliff_layer=None,
    )  # fmt: skip


def _scene(z, **extra):
    rows, cols = z.shape
    return {"z_m": z, "grid": (slice(0, rows), 0, rows, 0, cols, SP), **extra}


def _cliff(z):
    """``band_look`` of the cliff kind over every pixel of ``z``."""
    shape = z.shape
    reads = (np.full(shape, KIND_CLIFF, np.uint8), np.full(shape, -1, np.int32))
    return band_look(_look(), _scene(z), PAINTED_PALETTE, np.ones(shape, bool), reads)


def _planes(z, rock=None, top=None, mesh=None, cls=None, at=0) -> TiltPlanes:
    return TiltPlanes(z, rock, top, mesh, cls, None, (at, at, SP))


def test_the_tilt_on_the_light_s_normal_gives_the_mapped_normal():
    rng = np.random.default_rng(4)
    slope = np.radians(rng.uniform(0.0, 60.0, 2000))
    turn = rng.uniform(0.0, 2 * np.pi, 2000)
    surface = np.stack(
        [np.sin(slope) * np.cos(turn), np.sin(slope) * np.sin(turn), np.cos(slope)], -1
    ).astype(np.float32)
    mapped = surface + rng.normal(0, 0.1, surface.shape).astype(np.float32)
    mapped /= np.linalg.norm(mapped, axis=1, keepdims=True)
    tilt = tilt_of(mapped, surface)
    east, south = with_detail(
        surface[:, 0], surface[:, 1], with_tilt(np.zeros_like(tilt, np.int8), tilt)
    )
    fits = (np.abs(tilt) <= 1).all(1)
    assert fits.mean() > 0.99, "slopes to 60 degrees, as rock seen from above mostly is"
    np.testing.assert_allclose(east[fits], mapped[fits, 0], atol=2 / DETAIL_SCALE)
    np.testing.assert_allclose(south[fits], mapped[fits, 1], atol=2 / DETAIL_SCALE)


def test_the_tilt_adds_to_the_ground_s_detail_bytes_and_leaves_the_rest():
    detail = np.array([[[10, -5], [100, 0], [-120, 3]]], np.int8)
    tilt = np.array([[[0.0, 0.0], [0.5, -0.25], [-0.5, 2.0]]], np.float32)
    assert with_tilt(detail, tilt).tolist() == [[[10, -5], [127, -32], [-127, 127]]]


def test_drawn_for_the_light_the_rock_keeps_its_albedo():
    look = _look()
    z = _hills(24)[1:-1, 1:-1]
    rock = np.full((24, 24, 3), 0.35, np.float32)
    code = np.full((24, 24), GRASS, np.uint8)
    baked = rock_surface(rock, _scene(z), _ground(look), None, code)
    kept = rock_surface(rock, _scene(z, look_lit=True), _ground(look), None, code)
    got = _cliff(z)
    np.testing.assert_allclose(kept, rock * got.body, rtol=1e-5)
    np.testing.assert_allclose(baked, kept * got.shade[..., None], rtol=1e-5)
    assert got.shade.std() > 0.01, "the maps have relief to hand over"


def test_the_light_lays_the_tilt_where_the_painted_layer_lays_the_look():
    source = RockTilt(_look(), None, 1.0)
    z = _hills(16)
    ones = np.ones(z.shape, np.float32)
    family = np.full(z.shape, GRASS, np.uint8)
    whole = band_tilt(source, _planes(z, rock=ones), family)
    assert np.abs(whole).max() > 0.05
    half = band_tilt(source, _planes(z, rock=ones * 0.5), family)
    np.testing.assert_allclose(half, whole * 0.5, atol=1e-6)
    flat = band_tilt(source, _planes(z, rock=ones), None)
    assert not flat.any(), "no family plane, no look: the painted layer draws it plain"
    arch = band_tilt(source, _planes(z, top=ones), family)
    blend = band_tilt(source, _planes(z, rock=ones, top=ones * 0.5), family)
    assert not np.allclose(arch, whole, atol=1e-3)
    np.testing.assert_allclose(blend, (whole + arch) / 2, atol=1e-5)
    shells = np.full(z.shape, MESH_SHELL, np.uint8)
    shell = band_tilt(source, _planes(z, mesh=ones, cls=shells), None)
    over = band_tilt(source, _planes(z, rock=ones, mesh=ones, cls=shells), family)
    np.testing.assert_allclose(over, shell, atol=1e-6)
    coral = np.full(z.shape, MESH_CORAL, np.uint8)
    assert not band_tilt(source, _planes(z, mesh=ones, cls=coral), family).any()


def _relit(rgb: np.ndarray, east: np.ndarray, south: np.ndarray, sun) -> np.ndarray:
    """``rgb`` bytes lit in the painted style's light on these normals, open sky, no shadow."""
    shape = rgb.shape[:2]
    full = np.full(shape, 255, np.uint8)
    nrm = np.stack([normal_byte(east), normal_byte(south), full, full], -1)
    direct = shaded_direct(nrm, None, sun)
    ones = np.ones(shape, np.float32)
    return apply_terms(rgb, ones, direct, ones, shader_light("painted")).astype(np.float32)


def _bytes(linear: np.ndarray) -> np.ndarray:
    curve = PAINTED_PALETTE["tone"]
    toned = linear_to_srgb(by_luminance(linear, tone, curve["knee"], curve["white"]))
    return np.clip(toned, 0, 255).astype(np.uint8)


def _both(n: int = 96, albedo: bool = True):
    """The rock the old way (the maps' light in the colour, the surface's normal in the
    light) and the new (the albedo, the normal with the tilt), inside the margin."""
    z = _hills(n)
    inner = (slice(1, -1), slice(1, -1))
    got = _cliff(z[inner])
    colour = np.float32([0.24, 0.22, 0.17]) * (got.body if albedo else np.ones_like(got.body))
    east, south = normals(z, SP)
    family = np.full(z.shape, GRASS, np.uint8)
    tilt = band_tilt(RockTilt(_look(), None, 1.0), _planes(z, np.ones(z.shape), at=-1), family)
    bumped = with_detail(east, south, with_tilt(np.zeros((n, n, 2), np.int8), tilt[inner]))
    edge = (slice(2, -2), slice(2, -2))
    old = (_bytes(colour * got.shade[..., None]), east, south)
    new = (_bytes(colour), *bumped)
    return [tuple(part[edge] for part in way) for way in (old, new)]


def test_the_default_sun_draws_the_rock_as_it_did_with_the_maps_in_the_colour():
    old, new = _both()
    d = np.abs(_relit(*old, DEFAULT_SUN) - _relit(*new, DEFAULT_SUN)).max(-1)
    assert d.mean() < 1.0 and np.percentile(d, 99) <= 3, (d.mean(), np.percentile(d, 99))


def _relief(lit: np.ndarray) -> np.ndarray:
    """The fine relief of a lit picture: its luminance less its 9-pixel mean."""
    grey = lit.mean(-1)
    return grey - ndimage.uniform_filter(grey, 9)


def test_a_live_sun_lights_the_rock_s_relief_from_its_own_side():
    old, new = _both(albedo=False)
    noon, evening = _relief(_relit(*new, DEFAULT_SUN)), _relief(_relit(*new, EVENING))
    kept = _relief(_relit(*old, EVENING))
    assert np.corrcoef(noon.ravel(), evening.ravel())[0, 1] < 0.0, "the far sun turns it over"
    assert np.corrcoef(noon.ravel(), kept.ravel())[0, 1] > 0.5, "baked, it kept noon's side"


@pytest.fixture(scope="module")
def gravel(tmp_path_factory):
    weights = {"Gravel_WeightLayerInfo": np.full((N, N), 255, np.uint8)}
    found = load_detail_textures(write_textured_store(tmp_path_factory.mktemp("p"), weights), 0.2)
    assert found is not None
    return found


def test_the_light_takes_the_tilt_whatever_layers_a_run_draws(monkeypatch, gravel):
    seen = []

    def painter(scene, ground, sample, sample_rock):
        seen.append(scene["look_lit"])
        return np.zeros(scene["z_m"].shape + (3,), np.float32)

    monkeypatch.setattr(painting, "painted_colours", painter)
    field = _field()
    borrow = (np.broadcast_to(np.int8(0), (8192, 8192)), np.zeros((N, N), np.uint8))
    top_cm = (field.height_dm.astype(np.float32) / 10.0 + 2.0) * 100.0
    cls = np.zeros((N, N), np.uint8)
    cls[20:40, 10:30] = MESH_SHELL
    meshes = MeshPlanes(top_cm, cls)
    source = RockTilt(_look(7500.0 / N), None, 1.0)
    captured = {}
    for layer, tilt in (("terrain", None), ("terrain", source), ("painted", source)):
        capture = _Capture()
        draw_layers(
            (layer,), field, 1, borrow, N, False, field.height_dm.astype(np.float32),
            unlit=True, surface=capture, textures=gravel, meshes=meshes, rock_tilt=tilt,
            painted=_painted_ground() if layer == "painted" else None,
        )  # fmt: skip
        captured[layer, tilt is not None] = capture.detail
    plain, tilted = captured["terrain", False], captured["terrain", True]
    assert np.array_equal(tilted, captured["painted", True]), "one capture"
    shell = cls > 0
    assert (tilted[~shell] == plain[~shell]).all() and (tilted[shell] != plain[shell]).any()
    assert seen and all(seen), "and the painted layer leaves the maps' light to it"

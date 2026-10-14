"""The light is one capture whatever layers a run draws, and in whatever order.

Every layer hands the light the surface the seabed rule draws, and the trees cast from the
crown sprites whether or not the painted layer is drawn. docs/spatial-and-map.md section 29.
Synthetic fixtures: no install, no field on disk.
"""

from __future__ import annotations

import json
from types import SimpleNamespace

import numpy as np
import pytest

from mapgen.cache import MeshPlanes
from mapgen.gamedata.frame import BOUNDS_M
from mapgen.gamedata.ground.paint_store import META_NAME
from mapgen.palette.painted.band import PaintedParts
from mapgen.palette.relief import ReliefGround
from mapgen.palette.styles import RELIEF_PALETTES
from mapgen.palette.water.shore import OCEAN_LEVEL_M
from mapgen.render.draw import painting
from mapgen.render.draw.light import crown_occluder, crown_tops
from mapgen.terrain.render_meshes import MESH_CORAL, MESH_ROCK
from satisfactory_mcp.domain.spatial import heightfield as hf
from tests.support.draw import draw_layers, render_layer

#: One band of output, one texel of the field a pixel.
N = 96

#: The layers in the order a full run draws them.
LAYERS = ("terrain", "painted", "relief-dark")


class _Surface:
    """What ``render_layer`` hands the light: the drawn heights and land weight."""

    def __init__(self) -> None:
        self.z = np.full((N, N), np.nan, np.float32)
        self.land = np.full((N, N), np.nan, np.float32)

    def put(self, row, z_m, land, columns=slice(None)):
        self.z[row : row + len(z_m), columns] = z_m
        self.land[row : row + len(z_m), columns] = land


def _scene() -> SimpleNamespace:
    """Land to the west and a measured sea 3 m deep, with render-only meshes: coral a hand
    under the sea's surface, a rock standing out of it, and coral on the land."""
    step_cm = (BOUNDS_M["x_max_m"] - BOUNDS_M["x_min_m"]) * 100 / N
    rows, cols = np.mgrid[0:N, 0:N]
    land = cols < N // 3
    height = np.where(land, 300 + 2 * rows, -200).astype(np.int16)
    ocean_dm = round(OCEAN_LEVEL_M * hf.DM_PER_M)
    water = np.where(land, hf.NODATA, ocean_dm).astype(np.int16)
    grades = np.where(land, hf.WATER_DRY, hf.WATER_MEASURED).astype(np.uint8)
    field = SimpleNamespace(
        height_dm=height, provenance_plane=np.ones((N, N), np.uint8),
        water_raster=lambda: water, water_quality_raster=lambda: grades,
        x0_cm=BOUNDS_M["x_min_m"] * 100 + step_cm / 2,
        y0_cm=BOUNDS_M["y_min_m"] * 100 + step_cm / 2, spacing_cm=step_cm, width=N, height=N,
    )  # fmt: skip
    mesh_z = np.zeros((N, N), np.float32)
    cls = np.zeros((N, N), np.uint8)
    for (r0, r1, c0, c1), top_m, kind in (
        ((10, 30, 50, 70), OCEAN_LEVEL_M - 0.3, MESH_CORAL),
        ((50, 60, 50, 60), OCEAN_LEVEL_M + 7.0, MESH_ROCK),
        ((60, 80, 5, 20), 45.0, MESH_CORAL),
    ):
        mesh_z[r0:r1, c0:c1], cls[r0:r1, c0:c1] = top_m * 100.0, kind
    return SimpleNamespace(field=field, heights=height.astype(np.float32),
                           meshes=MeshPlanes(mesh_z, cls))  # fmt: skip


def _painted_ground() -> SimpleNamespace:
    """The parts of the painted ground ``render_layer`` reads before the painter."""
    rock = [np.zeros((N // 4, N // 4), np.float32)]
    return SimpleNamespace(rock=rock, rock_family=None, rock_top_rule=None, titan=None,
                           crowns=None, titan_crowns=None,
                           water_optics=lambda taps, river=None: None)  # fmt: skip


def _draw(scene, layer: str) -> _Surface:
    surface = _Surface()
    borrow = (np.broadcast_to(np.int8(0), (8192, 8192)), np.zeros((N, N), np.uint8))
    relief = None
    if layer in RELIEF_PALETTES:
        relief = ReliefGround(RELIEF_PALETTES[layer][0], scene.field, None, [])
    render_layer(
        layer, scene.field, 1, borrow, N, False,
        scene.heights, meshes=scene.meshes, unlit=True, surface=surface,
        painted=_painted_ground() if layer == "painted" else None, relief=relief,
    )  # fmt: skip
    return surface


def test_every_layer_hands_the_light_the_same_surface(monkeypatch):
    seen: dict = {}

    def painter(scene, ground, sample, sample_rock):
        seen["z_m"], seen["ndl"] = scene["z_m"].copy(), scene["ndl"].copy()
        return np.zeros(scene["z_m"].shape + (3,), np.float32)

    monkeypatch.setattr(painting, "painted_colours", painter)
    scene = _scene()
    captured = {layer: _draw(scene, layer) for layer in LAYERS}
    first = captured["terrain"]
    assert np.isfinite(first.z).all() and np.isfinite(first.land).all()
    for layer in LAYERS[1:]:
        assert captured[layer].z.tobytes() == first.z.tobytes(), layer
        assert captured[layer].land.tobytes() == first.land.tobytes(), layer

    coral, rock = (slice(12, 28), slice(52, 68)), (slice(52, 58), slice(52, 58))
    assert (seen["z_m"][coral] > first.z[coral] + 2.0).all(), "the painted layer draws the coral"
    assert (first.land[coral] < 0.01).all(), "and the light has the sea there"
    assert (first.land[rock] > 0.99).all(), "a rock out of the sea is land in it"
    lit = seen["ndl"] != np.float32(np.sin(np.deg2rad(45.0)))
    assert lit.any() and lit[10:30, 50:70].sum() == lit.sum(), "the coral keeps the default sun"


def test_one_pass_draws_every_layer_as_alone_painted_over_its_own_meshes(monkeypatch):
    def painter(scene, ground, sample, sample_rock):
        rgb = np.stack([scene["z_m"], scene["water"]["cover"] * 200, scene["ndl"] * 100], -1)
        return rgb.astype(np.float32)

    monkeypatch.setattr(painting, "painted_colours", painter)
    scene = _scene()
    borrow = (np.broadcast_to(np.int8(0), (8192, 8192)), np.zeros((N, N), np.uint8))
    reliefs = {layer: ReliefGround(RELIEF_PALETTES[layer][0], scene.field, None, [])
               for layer in LAYERS if layer in RELIEF_PALETTES}  # fmt: skip
    surface = _Surface()
    drawn = draw_layers(
        LAYERS, scene.field, 1, borrow, N, False,
        scene.heights, meshes=scene.meshes, unlit=True, surface=surface,
        painted=_painted_ground(), relief=reliefs,
    )  # fmt: skip
    for layer in LAYERS:
        alone = render_layer(
            layer, scene.field, 1, borrow, N, False,
            scene.heights, meshes=scene.meshes, unlit=True,
            painted=_painted_ground() if layer == "painted" else None, relief=reliefs.get(layer),
        )  # fmt: skip
        assert drawn[layer].tobytes() == alone.tobytes(), layer
    coral = (slice(12, 28), slice(52, 68))
    assert (drawn["painted"][coral] != drawn["terrain"][coral]).any(), "two grounds, not one"
    first = _draw(scene, "terrain")
    assert surface.z.tobytes() == first.z.tobytes(), "the pass captures the seabed rule"
    assert surface.land.tobytes() == first.land.tobytes()


def test_a_pass_in_column_pieces_draws_and_captures_what_whole_rows_do(monkeypatch):
    def painter(scene, ground, sample, sample_rock):
        rgb = np.stack([scene["z_m"], scene["water"]["cover"] * 200, scene["ndl"] * 100], -1)
        return rgb.astype(np.float32)

    monkeypatch.setattr(painting, "painted_colours", painter)
    scene = _scene()
    borrow = (np.broadcast_to(np.int8(0), (8192, 8192)), np.zeros((N, N), np.uint8))
    reliefs = {layer: ReliefGround(RELIEF_PALETTES[layer][0], scene.field, None, [])
               for layer in LAYERS if layer in RELIEF_PALETTES}  # fmt: skip
    drawn = {}
    for columns in (N, 23, 7):
        surface = _Surface()
        sheets = draw_layers(
            LAYERS, scene.field, 1, borrow, N, False,
            scene.heights, meshes=scene.meshes, unlit=True, surface=surface,
            painted=_painted_ground(), relief=reliefs, threads=3, columns=columns,
        )  # fmt: skip
        drawn[columns] = ({layer: sheet.tobytes() for layer, sheet in sheets.items()}, surface)
    whole, whole_surface = drawn.pop(N)
    for columns, (sheets, surface) in drawn.items():
        assert sheets == whole, columns
        assert surface.z.tobytes() == whole_surface.z.tobytes(), columns
        assert surface.land.tobytes() == whole_surface.land.tobytes(), columns


def test_a_pass_hands_the_painted_layer_apart_at_its_trees_with_each_band(monkeypatch):
    """The parts the painter draws reach the sink as bytes beside the picture, which is the
    picture drawn whole; the other layers come whole."""

    def picture(scene, ground, sample, sample_rock):
        rgb = np.stack([scene["z_m"], scene["water"]["cover"] * 200, scene["ndl"] * 100], -1)
        return rgb.astype(np.float32)

    def parts(scene, ground, sample, sample_rock):
        rgb = picture(scene, ground, sample, sample_rock)
        return PaintedParts(rgb, rgb * 0.5, rgb + 20.0, np.clip(rgb[..., 0] / 255.0, 0.0, 1.0))

    monkeypatch.setattr(painting, "painted_colours", picture)
    monkeypatch.setattr(painting, "painted_parts", parts)
    scene = _scene()
    borrow = (np.broadcast_to(np.int8(0), (8192, 8192)), np.zeros((N, N), np.uint8))
    args = (("terrain", "painted"), scene.field, 1, borrow, N, False, scene.heights)
    keywords = {"meshes": scene.meshes, "unlit": True, "painted": _painted_ground()}
    handed: list[tuple] = []
    draw_layers(*args, **keywords, bands=lambda *band: handed.append(band), split=("painted",))
    whole = draw_layers(*args, **keywords)
    assert [set(split) for _top, _bands, split in handed] == [{"painted"}]
    _top, bands, split = handed[0]
    assert bands["painted"].tobytes() == whole["painted"].tobytes()
    ground, trees = split["painted"]
    assert ground.shape == (N, N, 3) and trees.shape == (N, N, 4)
    red = whole["painted"][..., 0].astype(int)
    assert (np.abs(ground.astype(int) - whole["painted"] // 2) <= 1).all()
    shown = red > 0
    assert shown.any() and (np.abs(trees[..., 3].astype(int) - red) <= 1).all()
    assert (np.abs(trees[..., 0][shown].astype(int) - (red[shown] + 20)) <= 1).all()
    with pytest.raises(ValueError, match="apart"):
        draw_layers(*args, **keywords, split=("painted",))
    with pytest.raises(ValueError, match="unlit"):
        draw_layers(*args, **{**keywords, "unlit": False}, bands=print, split=("painted",))


def _sprites(names: list[str], titan: np.ndarray | None = None):
    from tests.support.crown_sprites import square_sprites

    return square_sprites(names, titan=titan)


def _titan(x_cm: float, y_cm: float, z_cm: float) -> np.ndarray:
    from mapgen.gamedata.vegetation.crown_sprites import CROWN_RECORD

    found = np.zeros(1, CROWN_RECORD)
    found["x"], found["y"], found["z"] = x_cm, y_cm, z_cm
    found["scale"] = found["scale_z"] = found["axis_z"] = 4.0
    found["axis_z"] = 1.0
    return found


def test_the_light_stamps_the_crown_sprites_whatever_layers_are_drawn(tmp_path):
    from tests.support.paint_store import write_store

    store = write_store(tmp_path / "paint")
    names = [entry["name"] for entry in json.loads((store / META_NAME).read_text())["crowns"]["species"]]
    sprites = _sprites(names, _titan(3000.0, 6000.0, 5000.0))
    size = 2048
    step = (BOUNDS_M["x_max_m"] - BOUNDS_M["x_min_m"]) / size
    found = crown_occluder(crown_tops(store, sprites, True), tmp_path / "a", size)
    assert found is not None
    top, cover, titan = found
    row = int((10.0 - BOUNDS_M["y_min_m"]) / step)
    col = int((32.0 - BOUNDS_M["x_min_m"]) / step)
    assert np.isfinite(top[row, col]) and cover[row, col] > 0 and titan[row, col] == 0
    assert top[row, col] == pytest.approx(9.0, abs=0.01), "a crown's top over its pivot"
    t_row = int((60.0 - BOUNDS_M["y_min_m"]) / step)
    t_col = int((30.0 - BOUNDS_M["x_min_m"]) / step)
    assert titan[t_row, t_col] == 255 and top[t_row, t_col] == pytest.approx(50.0 + 36.0, abs=0.05)
    assert not cover[np.isnan(top)].any() and not titan[np.isnan(top)].any()
    without = crown_occluder(crown_tops(store, sprites, False), tmp_path / "b", size)
    assert without is not None and not without[2].any(), "no Titan canopy without the Titan trees"
    del found, top, cover, titan, without


def test_a_run_without_the_store_s_crowns_or_the_sprites_casts_none(tmp_path):
    from tests.support.paint_store import write_store

    assert crown_tops(tmp_path, _sprites(["a"]), True) is None, "no paint store"
    store = write_store(tmp_path / "paint")
    assert crown_tops(store, None, True) is None, "no crown sprites"
    meta = json.loads((store / META_NAME).read_text(encoding="utf-8"))
    del meta["files"]["crowns.rec.z"]
    (store / META_NAME).write_text(json.dumps(meta), encoding="utf-8")
    assert crown_tops(store, _sprites(["a"]), True) is None, "a store without crowns"
    assert crown_occluder(None, tmp_path, 64) is None

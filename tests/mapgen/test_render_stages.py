"""The renders command's stages: one level sweep, the stamped rasters, the run's caches, and
what a layer's sidecar says.

docs/spatial-and-map.md sections 20 and 39. Synthetic fixtures throughout: no install.
"""

from __future__ import annotations

import sys

import numpy as np
import pytest

from mapgen.cache import MESH_CACHE_DIR_NAME
from mapgen.common import Refusal
from mapgen.render import cached_rasters
from mapgen.render.cached_rasters import UNREADABLE_RASTER, LevelSweep, stamped_raster
from mapgen.render.extras import RUN_CACHE_DIRS, remove_run_caches
from mapgen.tiles.layer_meta import (
    LayerDraw,
    RenderFacts,
    RunRecord,
    layer_provenance,
    render_block,
)


def test_the_levels_are_swept_once_whatever_asks_first(monkeypatch):
    swept: list[object] = []
    monkeypatch.setattr(cached_rasters, "AssetIndex", lambda store: ("index", store))
    monkeypatch.setattr(cached_rasters, "ClassFacts", lambda store, index: ("classes", store))

    def sweep_world(store, scripts, index, classes, progress):
        swept.append(classes)
        return {"meshes": [], "seconds": 0.0}

    def read_cliff_geometry(store, scripts, index, classes, progress, sweep):
        return {"sweep": sweep, "meshes": 0, "tris": 0, "by_source": {},
                "seconds_sweep": 0.0, "seconds_decode": 0.0}  # fmt: skip

    monkeypatch.setattr(cached_rasters, "sweep_world", sweep_world)
    monkeypatch.setattr(cached_rasters, "read_cliff_geometry", read_cliff_geometry)
    level = LevelSweep("store", "scripts", False)
    assert level.geometry["sweep"] is level.sweep
    assert len(swept) == 1, "the geometry reads the sweep the run already made"


def test_a_raster_that_will_not_read_back_refuses_the_run(tmp_path):
    written = []
    with pytest.raises(Refusal, match="could not be read back") as refused:
        stamped_raster(tmp_path, ("direct", "cliff_geometry"),
                       lambda: written.append(1) or {}, lambda: None)  # fmt: skip
    assert refused.value.code == UNREADABLE_RASTER and written == [1]


def test_a_cached_raster_is_quoted_and_not_rasterised(tmp_path):
    (tmp_path / "meta.json").write_text('{"size": 64}', encoding="utf-8")
    planes = (np.zeros((64, 64), np.float32), np.zeros((64, 64), np.uint8))
    maps, source = stamped_raster(tmp_path, ("top", "top_overlay"), pytest.fail,
                                  lambda: planes)  # fmt: skip
    assert maps == planes and source == {"top_overlay": {"reused": {"size": 64}}}


def test_the_run_s_caches_are_removed_and_a_held_one_is_named(tmp_path):
    for name in RUN_CACHE_DIRS:
        (tmp_path / name).mkdir()
        (tmp_path / name / "meta.json").write_text("{}", encoding="utf-8")
    held = np.lib.format.open_memmap(
        tmp_path / MESH_CACHE_DIR_NAME / "meshes.npy", "w+", np.float32, (8, 8)
    )
    left = remove_run_caches(tmp_path)
    if sys.platform == "win32":
        assert left == [tmp_path / MESH_CACHE_DIR_NAME], "Windows will not delete a mapped file"
        del held
        assert remove_run_caches(tmp_path) == []
    else:
        del held
        assert left == []
    assert not any((tmp_path / name).exists() for name in RUN_CACHE_DIRS)


def _record(inputs: dict) -> RunRecord:
    facts = RenderFacts(
        size=2048, spacing_m=3.6621, subsamples=1, two_regime=True,
        composition={"lift_knee_m": 0.25}, water={"source": "s", "shore": {"rule": "r"}},
        cut_workers=2, parallel_check=None, pillow_version="12",
    )  # fmt: skip
    return RunRecord(
        render=facts, recipe=7, kernel_only=False, field_meta={}, build_raw={},
        inputs=inputs, sources={}, biome_source={}, paint_source={},
    )  # fmt: skip


def _draw(layer: str, biome: bool) -> LayerDraw:
    return LayerDraw(
        layer=layer, style_id="painted-v1" if layer == "painted" else "terrain-v1",
        style_digest="d", biome=biome, measured={"regimes": {}}, shore_optics={"k": 1},
        seconds_to_draw=1.25, draw_threads=4, seconds_to_cut=2.5,
    )  # fmt: skip


def test_each_layer_s_render_block_carries_its_own_shore_optics():
    record = _record({})
    block = render_block(record.render, _draw("terrain", False))
    assert block["water"] == {"source": "s", "shore": {"rule": "r", "optics": {"k": 1}}}
    assert record.render.water["shore"] == {"rule": "r"}, "the run's own record is untouched"
    assert block["two_regime"]["lift_knee_m"] == 0.25 and "regimes" in block["two_regime"]


def test_a_layer_names_only_the_inputs_it_was_drawn_from(monkeypatch):
    from mapgen.tiles import layer_meta

    monkeypatch.setattr(layer_meta, "STYLES", {
        "terrain-v1": {"version": 1, "label": "t", "tone": None},
        "painted-v1": {"version": 1, "label": "p", "tone": None},
    })  # fmt: skip
    monkeypatch.setattr(layer_meta, "provenance_block", lambda raw, inputs, r, s: sorted(inputs))
    record = _record({name: {} for name in ("heightfield", "biome_raster", "paint", "titan_trees")})
    assert layer_provenance(record, _draw("terrain", False)) == ["heightfield"]
    assert layer_provenance(record, _draw("painted", True)) == [
        "biome_raster", "heightfield", "paint", "titan_trees",
    ]  # fmt: skip

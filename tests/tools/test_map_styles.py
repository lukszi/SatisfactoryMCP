"""Map styles on the server: tones, the palette-only restyle preset, and the Vulkan gate.

docs/maps_contract.md §3.2, §4 and §4.1. No generator runs here.
"""

from __future__ import annotations

import pytest

from satisfactory_mcp import config
from satisfactory_mcp.core import gpu
from satisfactory_mcp.core.gameassets.versions import STYLES
from satisfactory_mcp.domain.maps import axes as ax
from satisfactory_mcp.domain.maps import presets, registry
from tests.support.map_jobs import keep_cache


@pytest.fixture
def maps_home(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "data_dir", lambda: tmp_path / "data")
    monkeypatch.setattr(config, "game_root", lambda: tmp_path / "game")
    (tmp_path / "data" / "local").mkdir(parents=True)
    return tmp_path


def test_every_style_declares_a_tone_and_the_axes_read_it():
    assert {row["tone"] for row in STYLES.values()} == {"light", "dark"}
    assert ax.style_tone({"style": {"id": "relief-night"}}) == "dark"
    assert ax.style_tone({"style": {"id": "relief-muted", "tone": "light"}}) == "light"
    assert ax.style_tone({"style": {"id": "something new"}}) == "light"
    assert ax.style_tone({}) == "light"
    assert ax.LAYER_STYLE["relief-dark"] == "relief-night"


def test_the_generate_form_can_ask_for_every_style(maps_home):
    for layer in presets.RENDER_LAYERS:
        options = presets.normalise("render", {"layers": [layer], "size": 1024})
        assert options["layers"] == [layer]
    plan = presets.plan(
        "render", {"layers": ["relief", "relief-dark"], "size": 1024}, "j1", 1, set()
    )
    assert sorted(plan["produces"]) == ["relief-dark-r7-1", "relief-r7-1"]
    assert plan["argv"].count("--layer") == 2


def test_a_restyle_is_refused_until_a_full_render_kept_the_cache(maps_home):
    options = {"layers": ["relief"], "size": 1024, "restyle": True}
    with pytest.raises(presets.PresetError, match="raster cache"):
        presets.plan("render", options, "j1", 1, set())
    with pytest.raises(presets.PresetError, match="raster cache"):
        presets.estimate("render", options)
    keep_cache(1024, presets.CACHE_PARTS[:2])
    assert not presets.cache_ready(1024) and presets.cached_sizes() == []
    keep_cache(1024)
    assert presets.cached_sizes() == [1024]
    argv = presets.plan("render", options, "j1", 1, set())["argv"]
    assert "--restyle" in argv and "--keep-direct" in argv
    assert argv[argv.index("--cache-dir") + 1] == str(presets.cache_dir(1024))
    with pytest.raises(presets.PresetError, match="kernel-only"):
        presets.normalise("render", {**options, "recipe": "kernel-only"})


def test_a_restyle_costs_only_the_draw_and_the_cut(maps_home):
    keep_cache(32768)
    full = presets.stage_plan("render", presets.normalise("render", {"layers": ["relief"]}))
    unlit = {"layers": ["relief"], "restyle": True, "light": False}
    fast = presets.stage_plan("render", presets.normalise("render", unlit))
    assert {"sweep", "direct", "top", "light"} <= set(full)
    assert set(fast) == {"prep", "draw", "cut:relief"}
    assert 6 * 60 < sum(fast.values()) < 10 * 60
    assert presets.estimate("render", unlit)["seconds"] < 600
    lit = presets.stage_plan(
        "render", presets.normalise("render", {"layers": ["relief"], "restyle": True})
    )
    assert set(lit) == {"prep", "draw", "light", "cut:relief"}
    registry.record_history(
        {"job": "j", "preset": "render", "seconds": 2000,
         "options": {"layers": ["relief"], "size": 32768, "recipe": "current"}}
    )  # fmt: skip
    assert not presets.estimate("render", unlit)["measured"]


def test_the_upscaler_is_offered_only_with_vulkan(maps_home, monkeypatch):
    monkeypatch.setattr(presets, "vulkan_available", lambda: False)
    with pytest.raises(presets.PresetError, match="Vulkan"):
        presets.normalise("artwork", {"enhance": True})
    assert presets.normalise("artwork", {"enhance": False})["enhance"] is False
    assert presets.can_generate()["vulkan"] is False
    monkeypatch.setattr(presets, "vulkan_available", lambda: True)
    assert presets.normalise("artwork", {"enhance": True})["enhance"] is True


def test_the_vulkan_probe_runs_once_and_survives_a_missing_loader(monkeypatch):
    calls = []
    monkeypatch.setattr(gpu, "_answer", None)
    monkeypatch.setattr(gpu, "probe_vulkan", lambda: calls.append(1) or True)
    assert gpu.vulkan_available() and gpu.vulkan_available()
    assert calls == [1]
    monkeypatch.setattr(gpu, "_load", lambda: None)
    monkeypatch.undo()
    monkeypatch.setattr(gpu, "_load", lambda: None)
    assert gpu.probe_vulkan() is False

"""A render bakes the live-sun light unless told not to, from the CLI and from a job alike.

``python -m mapgen renders`` reads ``--light`` (the default), ``--no-light``, and ``--unlit``
from before the default changed; the ``render`` preset passes the choice explicitly.
docs/spatial-and-map.md section 29, docs/maps_contract.md section 4.
"""

from __future__ import annotations

import argparse

import pytest

from mapgen.lighting.stage import Surface, allocate_work_arrays, occluder_planes
from mapgen.render.draw.kept_light import KEPT_LIGHT_DIR_NAME
from satisfactory_mcp.domain.maps import presets, registry
from tests.support.map_jobs import Passed, keep_cache, run_renders


def parsed(monkeypatch, *argv: str) -> argparse.Namespace:
    """What ``renders.main`` parsed from ``argv`` before it stopped at ``require_gen``."""
    seen: list[argparse.Namespace] = []
    real = argparse.ArgumentParser.parse_args
    monkeypatch.setattr(
        argparse.ArgumentParser,
        "parse_args",
        lambda self, *a, **k: seen.append(real(self, *a, **k)) or seen[-1],
    )
    with pytest.raises(Passed):
        run_renders(monkeypatch, "--renders-name", "renders-new", *argv)
    return seen[-1]


def test_the_cli_bakes_the_light_unless_told_not_to(in_use_local, monkeypatch):
    assert parsed(monkeypatch).light is True
    assert parsed(monkeypatch, "--no-light").light is False
    assert parsed(monkeypatch, "--light").light is True
    assert parsed(monkeypatch, "--unlit").light is True, "the old opt-in still parses"
    assert parsed(monkeypatch, "--restyle").light is True
    assert parsed(monkeypatch, "--kernel-only", "--no-light").light is False


@pytest.mark.parametrize("light", [True, False])
@pytest.mark.parametrize("mode", ["current", "kernel-only", "restyle"])
def test_a_job_hands_its_light_choice_to_the_generator(
    in_use_local, runner, monkeypatch, mode, light
):
    options: dict = {"layers": ["terrain"], "size": 1024}
    if light is False:
        options["light"] = False
    if mode == "kernel-only":
        options["recipe"] = "kernel-only"
    if mode == "restyle":
        options["restyle"] = True
        keep_cache(1024)
    job = runner.submit("render", options, None, None)
    assert job["options"]["light"] is light
    assert ("--light" in job["argv"]) is light and ("--no-light" in job["argv"]) is not light
    args = parsed(monkeypatch, *job["argv"])
    assert args.light is light
    assert args.kernel_only is (mode == "kernel-only") and args.restyle is (mode == "restyle")


def test_the_estimate_counts_the_light_cache_and_its_crowns_for_any_layers(in_use_local):
    area = (2048 / presets.FULL_PX) ** 2
    scratch = presets.LIGHT_SCRATCH_BYTES + presets.CROWN_SCRATCH_BYTES
    for layers in (["terrain"], ["painted"]):
        lit = presets.estimate("render", {"layers": layers, "size": 2048})
        dark = presets.estimate("render", {"layers": layers, "size": 2048, "light": False})
        assert lit["transient_bytes"] - dark["transient_bytes"] >= int(scratch * area)
        assert lit["keep_bytes"] > dark["keep_bytes"] and lit["seconds"] > dark["seconds"]


def test_a_lit_render_that_keeps_its_cache_counts_its_kept_terms_as_kept(in_use_local):
    """The terms move out of the scratch into ``light.kept/``: kept, and no more space at the
    peak."""
    terms = int(presets.KEPT_TERMS_BYTES * (2048 / presets.FULL_PX) ** 2)
    drop = presets.estimate("render", {"layers": ["terrain"], "size": 2048})
    keep = presets.estimate("render", {"layers": ["terrain"], "size": 2048, "keep_cache": True})
    assert keep["keep_bytes"] - drop["keep_bytes"] == terms
    assert keep["transient_bytes"] == drop["transient_bytes"] - terms
    assert keep["needs_bytes"] == drop["needs_bytes"]
    dark = {"layers": ["terrain"], "size": 2048, "light": False}
    kept, dropped = (presets.estimate("render", {**dark, "keep_cache": k}) for k in (True, False))
    assert kept["keep_bytes"] == dropped["keep_bytes"], "no light, no terms"


def test_a_restyle_that_finds_a_kept_light_is_budgeted_no_bake(in_use_local):
    assert presets.KEPT_LIGHT_PART == KEPT_LIGHT_DIR_NAME
    size, area = 2048, (2048 / presets.FULL_PX) ** 2
    keep_cache(size)
    restyle = {"layers": ["terrain"], "size": size, "restyle": True}
    baked = presets.estimate("render", restyle)["seconds"]
    full = presets.estimate("render", {"layers": ["terrain"], "size": size})["seconds"]
    keep_cache(size, parts=(presets.KEPT_LIGHT_PART,))
    assert presets.light_kept(size)
    kept = presets.estimate("render", restyle)["seconds"]
    saved = (presets.LIGHT_STAGE_S - presets.LIGHT_KEPT_S) * area
    assert baked - kept == pytest.approx(saved, abs=1)
    assert presets.estimate("render", {"layers": ["terrain"], "size": size})["seconds"] == full
    plan = presets.stage_plan("render", presets.normalise("render", restyle))
    assert plan["light"] == pytest.approx(presets.LIGHT_KEPT_S * area + 2.0)


def test_the_light_scratch_is_the_light_cache_the_stage_allocates(tmp_path):
    size = 512
    work = tmp_path / "light.cache"
    Surface(work, size).close()
    allocate_work_arrays(work, size)
    written = sum(path.stat().st_size for path in work.glob("*.npy"))
    expected = presets.LIGHT_SCRATCH_BYTES * (size / presets.FULL_PX) ** 2
    assert written == pytest.approx(expected, rel=0.01)


def test_the_kept_terms_are_the_terms_the_stage_allocates(tmp_path):
    size = 512
    allocate_work_arrays(tmp_path, size)
    expected = presets.KEPT_TERMS_BYTES * (size / presets.FULL_PX) ** 2
    assert (tmp_path / "terms.npy").stat().st_size == pytest.approx(expected, rel=0.01)


def test_a_lit_job_that_keeps_its_light_counts_the_kept_terms(in_use_local):
    terms = int(presets.KEPT_TERMS_BYTES * (2048 / presets.FULL_PX) ** 2)
    lit = {"layers": ["terrain"], "size": 2048}
    plain = presets.estimate("render", lit)
    for keeps in ({"keep_cache": True}, {"recipe": "kernel-only"}):
        kept = presets.estimate("render", {**lit, **keeps})
        assert kept["keep_bytes"] - plain["keep_bytes"] == terms, keeps
        assert kept["needs_bytes"] == plain["needs_bytes"], "moved out of the scratch, not copied"
    dark = {**lit, "light": False}
    dark_kept = presets.estimate("render", {**dark, "keep_cache": True})
    assert dark_kept["keep_bytes"] == presets.estimate("render", dark)["keep_bytes"]
    keep_cache(2048, parts=(presets.KEPT_LIGHT_PART,))
    replaced = presets.estimate("render", {**lit, "keep_cache": True})
    assert replaced["keep_bytes"] == plain["keep_bytes"], "a kept light is replaced in place"


def test_the_cache_counts_and_clears_the_light_kept_beside_the_rasters(in_use_local):
    keep_cache(2048)
    rasters = registry.cache_bytes()
    kept = presets.cache_dir(2048) / presets.KEPT_LIGHT_PART
    (kept / "tiles" / "0").mkdir(parents=True)
    (kept / "tiles" / "0" / "0.webp").write_bytes(b"t" * 10)
    (kept / "terms.npy").write_bytes(b"\0" * 300)
    (kept / "meta.json").write_text("{}", encoding="utf-8")
    assert presets.light_kept(2048)
    assert registry.cache_bytes() == rasters + 312
    assert registry.clear_cache() == rasters + 312
    assert registry.cache_bytes() == 0 and not presets.light_kept(2048)


def test_the_bands_are_cut_as_they_settle_so_more_layers_need_no_more_scratch(in_use_local):
    dark = {"size": 2048, "light": False}
    every = presets.estimate("render", {**dark, "layers": list(presets.RENDER_LAYERS)})
    one = presets.estimate("render", {**dark, "layers": ["terrain"]})
    assert every["transient_bytes"] == one["transient_bytes"]
    assert every["keep_bytes"] == len(presets.RENDER_LAYERS) * one["keep_bytes"]


def test_the_crown_scratch_is_the_one_occluder_the_bake_reads(tmp_path):
    size = 512
    planes = occluder_planes(tmp_path, size)
    del planes
    written = sum(path.stat().st_size for path in tmp_path.glob("*.npy"))
    expected = presets.CROWN_SCRATCH_BYTES * (size / presets.FULL_PX) ** 2
    assert written == pytest.approx(expected, rel=0.01)


def test_a_measured_run_predicts_only_a_run_with_the_same_light(in_use_local):
    registry.record_history(
        {"job": "j", "preset": "render", "seconds": 2000,
         "options": {"layers": ["relief-dark"], "size": 32768, "recipe": "current", "light": False}}
    )  # fmt: skip
    assert presets.estimate("render", {"layers": ["relief-dark"], "light": False})["measured"]
    assert not presets.estimate("render", {"layers": ["relief-dark"]})["measured"]

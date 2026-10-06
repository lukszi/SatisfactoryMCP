"""A render bakes the live-sun light unless told not to, from the CLI and from a job alike.

``python -m mapgen renders`` reads ``--light`` (the default), ``--no-light``, and ``--unlit``
from before the default changed; the ``render`` preset passes the choice explicitly.
docs/spatial-and-map.md section 29, docs/maps_contract.md section 4.
"""

from __future__ import annotations

import argparse

import pytest

from mapgen.lighting.stage import Surface, _alloc, occluder_planes
from satisfactory_mcp.domain.maps import presets, registry
from tests.support.map_jobs import Passed, keep_cache, run_renders


def parsed(monkeypatch, *argv: str) -> argparse.Namespace:
    """What ``pipeline.main`` parsed from ``argv`` before it stopped at ``require_gen``."""
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


def test_the_estimate_counts_the_light_cache_and_its_crowns(in_use_local):
    area = (2048 / presets.FULL_PX) ** 2
    for layers, scratch in (
        (["terrain"], presets.LIGHT_SCRATCH_BYTES),
        (["painted"], presets.LIGHT_SCRATCH_BYTES + presets.CROWN_SCRATCH_BYTES),
    ):
        lit = presets.estimate("render", {"layers": layers, "size": 2048})
        dark = presets.estimate("render", {"layers": layers, "size": 2048, "light": False})
        assert lit["transient_bytes"] - dark["transient_bytes"] >= int(scratch * area)
        assert lit["keep_bytes"] > dark["keep_bytes"] and lit["seconds"] > dark["seconds"]


def test_the_light_scratch_is_the_light_cache_the_stage_allocates(tmp_path):
    size = 512
    work = tmp_path / "light.cache"
    Surface(work, size).close()
    _alloc(work, size)
    written = sum(path.stat().st_size for path in work.glob("*.npy"))
    expected = presets.LIGHT_SCRATCH_BYTES * (size / presets.FULL_PX) ** 2
    assert written == pytest.approx(expected, rel=0.01)


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
         "options": {"layers": ["relief"], "size": 32768, "recipe": "current", "light": False}}
    )  # fmt: skip
    assert presets.estimate("render", {"layers": ["relief"], "light": False})["measured"]
    assert not presets.estimate("render", {"layers": ["relief"]})["measured"]

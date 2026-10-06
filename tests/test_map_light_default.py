"""A render bakes the live-sun light unless told not to, from the CLI and from a job alike.

``python -m mapgen renders`` reads ``--light`` (the default), ``--no-light``, and ``--unlit``
from before the default changed; the ``render`` preset passes the choice explicitly.
docs/spatial-and-map.md section 29, docs/maps_contract.md section 4.
"""

from __future__ import annotations

import argparse

import pytest
from test_map_render_in_use import Passed, local, run, runner  # noqa: F401  (the fixtures)
from test_map_styles import _keep_cache

from mapgen.lighting.stage import Surface, _alloc
from satisfactory_mcp.domain.maps import presets, registry


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
        run(monkeypatch, "--renders-name", "renders-new", *argv)
    return seen[-1]


def test_the_cli_bakes_the_light_unless_told_not_to(local, monkeypatch):  # noqa: F811
    assert parsed(monkeypatch).light is True
    assert parsed(monkeypatch, "--no-light").light is False
    assert parsed(monkeypatch, "--light").light is True
    assert parsed(monkeypatch, "--unlit").light is True, "the old opt-in still parses"
    assert parsed(monkeypatch, "--restyle").light is True
    assert parsed(monkeypatch, "--kernel-only", "--no-light").light is False


@pytest.mark.parametrize("light", [True, False])
@pytest.mark.parametrize("mode", ["current", "kernel-only", "restyle"])
def test_a_job_hands_its_light_choice_to_the_generator(local, runner, monkeypatch, mode, light):  # noqa: F811
    options: dict = {"layers": ["terrain"], "size": 1024}
    if light is False:
        options["light"] = False
    if mode == "kernel-only":
        options["recipe"] = "kernel-only"
    if mode == "restyle":
        options["restyle"] = True
        _keep_cache(1024)
    job = runner.submit("render", options, None, None)
    assert job["options"]["light"] is light
    assert ("--light" in job["argv"]) is light and ("--no-light" in job["argv"]) is not light
    args = parsed(monkeypatch, *job["argv"])
    assert args.light is light
    assert args.kernel_only is (mode == "kernel-only") and args.restyle is (mode == "restyle")


def test_the_estimate_counts_the_light_cache_and_its_crowns(local):  # noqa: F811
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


def test_a_measured_run_predicts_only_a_run_with_the_same_light(local):  # noqa: F811
    registry.record_history(
        {"job": "j", "preset": "render", "seconds": 2000,
         "options": {"layers": ["relief"], "size": 32768, "recipe": "current", "light": False}}
    )  # fmt: skip
    assert presets.estimate("render", {"layers": ["relief"], "light": False})["measured"]
    assert not presets.estimate("render", {"layers": ["relief"]})["measured"]

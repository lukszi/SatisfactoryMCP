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

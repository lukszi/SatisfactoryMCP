"""``python -m mapgen renders`` refuses a folder holding tiles of a type the registry lists.

The tree is a ``data/local`` under ``tmp_path``: the artwork at the root and ``renders-v4``
holding the default map, its manifest written by the server's own registry. A run that gets
past the guard stops at ``require_gen``, before anything reads the game or writes a tile.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

from mapgen import pipeline
from mapgen.tiles.inuse import IN_USE, MANIFEST, OVERRIDE, held_types, in_use_refusal
from satisfactory_mcp import config
from satisfactory_mcp.domain.maps import presets, registry
from satisfactory_mcp.interfaces.web.mapjobs import MapJobRunner

PNG = b"\x89PNG"
PIN = "buildVersion 502094 (engine branch ++FactoryGame+rel-main), the installed build"


class Passed(Exception):
    """The run got past the guard to where it would open the game."""


def pyramid(directory: Path, sidecar_name: str, sidecar: dict) -> None:
    (directory / "tiles" / "0").mkdir(parents=True, exist_ok=True)
    (directory / "tiles" / "0" / "0_0.png").write_bytes(PNG)
    (directory / sidecar_name).write_text(json.dumps(sidecar), encoding="utf-8")


def render_meta(layer: str) -> dict:
    return {
        "_meta": {
            "generator": "tools/gen_map_renders.py",
            "layer": layer,
            "recipe": 6,
            "sources": {"heightfield": {"generator_version": 5, "game_version_pinned": PIN}},
            "tiles": {"bytes": 1000, "max_z": 7},
        }
    }


def link(kind: str, target: Path, at: Path) -> None:
    if kind == "junction":
        if os.name != "nt":
            pytest.skip("directory junctions are a Windows feature")
        import _winapi

        _winapi.CreateJunction(str(target), str(at))
        return
    try:
        at.symlink_to(target, target_is_directory=True)
    except (OSError, NotImplementedError) as exc:
        pytest.skip(f"this account cannot make a directory symlink here: {exc}")


def ident_of(rel: str) -> str:
    return next(i for i, e in registry.read()["types"].items() if e["dir"] == rel)


def run(monkeypatch, *argv: str) -> int:
    def past_the_guard(*_names: str) -> dict:
        raise Passed

    monkeypatch.setattr(pipeline, "require_gen", past_the_guard)
    monkeypatch.setattr(sys, "argv", ["mapgen", *argv])
    return pipeline.main()


@pytest.fixture
def local(tmp_path, monkeypatch) -> Path:
    root = tmp_path / "local"
    monkeypatch.setattr(config, "data_dir", lambda: tmp_path)
    monkeypatch.setattr(registry, "game_cl", lambda: 502094)
    monkeypatch.setattr(pipeline, "LOCAL_DIR", root)
    root.mkdir()
    pyramid(root, "map.json", {"_meta": {"generator": "tools/gen_map_image.py"}})
    for layer in ("terrain", "painted"):
        pyramid(root / "renders-v4" / layer, "meta.json", render_meta(layer))
    assert registry.ensure()
    registry.set_default(ident_of("renders-v4/painted"))
    return root


def test_the_reader_finds_the_manifest_the_registry_writes(local):
    assert local / MANIFEST == registry.manifest_path()


def test_a_render_into_the_folder_of_a_registered_type_is_refused(local, monkeypatch, capsys):
    painted = ident_of("renders-v4/painted")
    assert set(held_types(local, local / "renders-v4")) == {painted, ident_of("renders-v4/terrain")}
    assert run(monkeypatch, "--renders-name", "renders-v4") == IN_USE
    out = capsys.readouterr().out
    assert f"{painted} (the default)" in out
    assert "--renders-name <new>" in out and OVERRIDE in out
    assert (local / "renders-v4" / "painted" / "tiles" / "0" / "0_0.png").read_bytes() == PNG


@pytest.mark.parametrize("kind", ["junction", "symlink"])
def test_the_refusal_follows_a_link_to_the_folder(local, monkeypatch, capsys, kind):
    link(kind, local / "renders-v4", local / "renders")
    assert run(monkeypatch) == IN_USE
    out = capsys.readouterr().out
    assert f"{ident_of('renders-v4/painted')} (the default)" in out
    assert "renders-v4" in out, "the message says where the link leads"


def test_force_is_not_the_override(local, monkeypatch):
    assert run(monkeypatch, "--renders-name", "renders-v4", "--force") == IN_USE


def test_the_override_writes_into_a_registered_folder_anyway(local, monkeypatch):
    assert in_use_refusal(local, local / "renders-v4", overwrite=True) is None
    with pytest.raises(Passed):
        run(monkeypatch, "--renders-name", "renders-v4", OVERRIDE)


def test_a_new_or_unregistered_folder_passes(local, monkeypatch):
    pyramid(local / "renders-scratch" / "terrain", "meta.json", render_meta("terrain"))
    for name in ("renders-v5", "renders-scratch"):
        with pytest.raises(Passed):
            run(monkeypatch, "--renders-name", name)


def test_without_a_manifest_the_guard_passes(local, monkeypatch, tmp_path):
    registry.manifest_path().unlink()
    assert held_types(local, local / "renders-v4") == []
    with pytest.raises(Passed):
        run(monkeypatch, "--renders-name", "renders-v4")
    fresh = tmp_path / "fresh-clone" / "data" / "local"
    assert held_types(fresh, fresh / "renders") == []


@pytest.fixture
def runner(local, monkeypatch) -> MapJobRunner:
    (local / "heightmap").mkdir()
    (local / "heightmap" / "meta.json").write_text('{"generator_version": 5}', encoding="utf-8")
    monkeypatch.setattr(config, "game_root", lambda: local.parent / "game")
    monkeypatch.setattr(
        presets,
        "can_generate",
        lambda: {"gen": True, "tools": True, "game": True, "heightfield": True, "vulkan": True,
                 "ok": True, "reason": None},
    )  # fmt: skip
    return MapJobRunner(watcher=None)


@pytest.mark.parametrize("flow", ["new", "re-render", "restyle"])
def test_every_runner_flow_passes_the_guard(local, monkeypatch, runner, flow):
    painted = ident_of("renders-v4/painted")
    options: dict = {"layers": ["painted"], "size": 1024}
    if flow == "restyle":
        options["restyle"] = True
        for part in presets.CACHE_PARTS:
            (presets.cache_dir(1024) / part).mkdir(parents=True)
            (presets.cache_dir(1024) / part / "meta.json").write_text("{}", encoding="utf-8")
    job = runner.submit("render", options, None, None if flow == "new" else painted)
    assert job["command"] == "renders" and OVERRIDE not in job["argv"]
    assert registry.read()["types"][job["produces"][0]]["status"] == "building"
    with pytest.raises(Passed):
        run(monkeypatch, *job["argv"])
    assert in_use_refusal(local, local / "renders-v4", overwrite=False), "still guarded"

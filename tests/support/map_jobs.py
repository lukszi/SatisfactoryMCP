"""Scratch ``data/local`` map trees, a fake generator, and ``mapgen renders`` stopped at its guard.

The fixtures here are registered for the whole suite by ``tests/conftest.py``, so ``mapgen``
and the web stack are imported inside the functions that need them. Nothing in this module
reads the game or a real map.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

from satisfactory_mcp import config
from satisfactory_mcp.domain.maps import presets, registry

PIN = "buildVersion 502094 (engine branch ++FactoryGame+rel-main), the installed build"
PNG_MAGIC = b"\x89PNG"


def write_pyramid(directory: Path, sidecar_name: str | None, sidecar: dict | None = None) -> None:
    """A one-tile pyramid in ``directory``, with its sidecar when ``sidecar_name`` is given."""
    (directory / "tiles" / "0").mkdir(parents=True, exist_ok=True)
    (directory / "tiles" / "0" / "0_0.png").write_bytes(PNG_MAGIC)
    if sidecar_name is not None:
        (directory / sidecar_name).write_text(json.dumps(sidecar or {}), encoding="utf-8")


def render_meta(layer: str, recipe: int, hf: int, nbytes: int = 1000) -> dict:
    return {
        "_meta": {
            "generator": "tools/gen_map_renders.py",
            "layer": layer,
            "recipe": recipe,
            "sources": {"heightfield": {"generator_version": hf, "game_version_pinned": PIN}},
            "tiles": {"bytes": nbytes, "max_z": 7},
        }
    }


def link_dir(target: Path, at: Path) -> None:
    """A directory junction on Windows, a symlink elsewhere: the shape ``renders`` has."""
    if os.name == "nt":
        import _winapi

        _winapi.CreateJunction(str(target), str(at))
    else:
        at.symlink_to(target, target_is_directory=True)


@pytest.fixture
def local(tmp_path, monkeypatch) -> Path:
    """A ``data/local`` as a long-used machine has it: artwork, three render sets, a link."""
    monkeypatch.setattr(config, "data_dir", lambda: tmp_path)
    monkeypatch.setattr(registry, "game_cl", lambda: 502094)
    root = tmp_path / "local"
    root.mkdir()
    write_pyramid(
        root,
        "map.json",
        {"_meta": {"generator": "tools/gen_map_image.py",
                   "sources": {"map_slices": {"game_version_raw": {"Changelist": 502094}}},
                   "tiles": {"enhanced": True, "enhancement": {"recipe": 2}, "bytes": 50}}},
    )  # fmt: skip
    for layer in ("terrain", "satellite"):
        write_pyramid(root / "renders-v1" / layer, "meta.json", render_meta(layer, 3, 3))
        write_pyramid(root / "renders-v2" / layer, "meta.json", render_meta(layer, 4, 5))
        write_pyramid(root / "renders-v3" / layer, "meta.json", render_meta(layer, 5, 5))
    link_dir(root / "renders-v3", root / "renders")
    (root / "heightmap").mkdir()
    (root / "heightmap" / "meta.json").write_text(
        json.dumps({"generator_version": 5, "sources": {"game": {"game_version_pinned": PIN}}}),
        encoding="utf-8",
    )
    write_pyramid(
        root / "renders-v2.incoming" / "terrain", "meta.json", render_meta("terrain", 4, 5)
    )
    return root


class Passed(Exception):
    """The run got past the guard to where it would open the game."""


def ident_of(rel: str) -> str:
    """The registry id of the map type stored at ``rel`` under ``data/local``."""
    return next(i for i, e in registry.read()["types"].items() if e["dir"] == rel)


def run_renders(monkeypatch, *argv: str) -> int:
    """``python -m mapgen renders`` with ``argv``; raises ``Passed`` once past the guard."""
    from mapgen import pipeline

    def past_the_guard(*_names: str) -> dict:
        raise Passed

    monkeypatch.setattr(pipeline, "require_gen", past_the_guard)
    monkeypatch.setattr(sys, "argv", ["mapgen", *argv])
    return pipeline.main()


@pytest.fixture
def in_use_local(tmp_path, monkeypatch) -> Path:
    """A ``data/local`` whose ``renders-v4`` holds the registered default map."""
    from mapgen import pipeline

    root = tmp_path / "local"
    monkeypatch.setattr(config, "data_dir", lambda: tmp_path)
    monkeypatch.setattr(registry, "game_cl", lambda: 502094)
    monkeypatch.setattr(pipeline, "LOCAL_DIR", root)
    root.mkdir()
    write_pyramid(root, "map.json", {"_meta": {"generator": "tools/gen_map_image.py"}})
    for layer in ("terrain", "painted"):
        write_pyramid(root / "renders-v4" / layer, "meta.json", render_meta(layer, 6, 5))
    assert registry.ensure()
    registry.set_default(ident_of("renders-v4/painted"))
    return root


@pytest.fixture
def runner(in_use_local, monkeypatch):
    """A map job runner over ``in_use_local`` that believes it can generate."""
    from satisfactory_mcp.interfaces.web.mapjobs import MapJobRunner

    (in_use_local / "heightmap").mkdir()
    (in_use_local / "heightmap" / "meta.json").write_text(
        '{"generator_version": 5}', encoding="utf-8"
    )
    monkeypatch.setattr(config, "game_root", lambda: in_use_local.parent / "game")
    monkeypatch.setattr(
        presets,
        "can_generate",
        lambda: {"gen": True, "tools": True, "game": True, "heightfield": True, "vulkan": True,
                 "ok": True, "reason": None},
    )  # fmt: skip
    return MapJobRunner(watcher=None)


def keep_cache(size: int, parts=presets.CACHE_PARTS) -> None:
    """Leave the raster cache a full render of ``size`` keeps, so a restyle may run."""
    for part in parts:
        folder = presets.cache_dir(size) / part
        folder.mkdir(parents=True, exist_ok=True)
        (folder / "meta.json").write_text("{}", encoding="utf-8")


#: A stand-in generator: the real one's progress lines and a one-tile pyramid per layer.
FAKE_GENERATOR = r"""
import argparse, json, os, sys, time
from pathlib import Path
p = argparse.ArgumentParser()
p.add_argument("command", nargs="?")
p.add_argument("--game"); p.add_argument("--field"); p.add_argument("--out-dir")
p.add_argument("--renders-name"); p.add_argument("--size", type=int)
p.add_argument("--layer", action="append"); p.add_argument("--kernel-only", action="store_true")
p.add_argument("--no-top", action="store_true"); p.add_argument("--cache-dir")
p.add_argument("--keep-direct", action="store_true")
p.add_argument("--light", action=argparse.BooleanOptionalAction, default=True)
a = p.parse_args()
pause = float(os.environ.get("FAKE_PAUSE", "0.05"))
print("field: 7500x7500 at 1 m, build buildVersion 502094 (x), the installed build", flush=True)
for i in range(0, 101, 25):
    print(f"  direct.cache: {i:4.1f}% of {a.size}x{a.size} at 0.2289 m, 1 M texels, 1s", flush=True)
    time.sleep(pause)
for layer in a.layer or ["terrain", "satellite"]:
    print(f"drawing {layer} at {a.size}x{a.size}", flush=True)
    for i in (13.3, 50.8, 88.3):
        print(f"  {layer}: {i:4.1f}% of {a.size}x{a.size} in 1.0s", flush=True)
        time.sleep(pause)
    if os.environ.get("FAKE_FAIL"):
        print("something broke in the cutter", flush=True)
        sys.exit(3)
    out = Path(a.out_dir) / a.renders_name / layer
    (out / "tiles" / "0").mkdir(parents=True, exist_ok=True)
    (out / "tiles" / "0" / "0_0.png").write_bytes(b"png")
    meta = {"_meta": {"generator": "tools/gen_map_renders.py", "layer": layer,
            "tiles": {"bytes": 3, "max_z": 2},
            "provenance": {"schema": 1, "game": {"cl": 502094},
                "inputs": {"heightfield": {"cl": 502094, "generator_version": 5, "planes": [],
                                           "digest": "sha256:hf"}},
                "renderer": {"family": "render", "recipe": 5, "version": 1, "label": "PCHIP",
                             "size_px": a.size},
                "style": {"id": "terrain-hypsometric", "version": 1, "label": layer}}}}
    (out / "meta.json").write_text(json.dumps(meta), encoding="utf-8")
    for z in range(3):
        print(f"  pyramid z{z}: 256x256, 1 tiles, 0.10 MB", flush=True)
    print(f"wrote {out}  1 tiles over z0..z2 (0.0 MB)", flush=True)
print("done in 1s", flush=True)
"""


def install_fake(tools: Path) -> None:
    """The fake as the shim path and as the ``mapgen`` package the runner starts."""
    (tools / "gen_map_renders.py").write_text(FAKE_GENERATOR, encoding="utf-8")
    package = tools / "mapgen" / "src" / "mapgen"
    package.mkdir(parents=True)
    (package / "__init__.py").write_text("", encoding="utf-8")
    (package / "__main__.py").write_text(FAKE_GENERATOR, encoding="utf-8")

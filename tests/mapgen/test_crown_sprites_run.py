"""A painted run reads the crown sprites of the installed build, building them first when the
cache is missing, for another build, or short of a paint store species.

docs/map/light-and-crowns.md section 36, "Crown sprites". The build is a stand-in: no install.
"""

from __future__ import annotations

import json

import numpy as np

from mapgen.gamedata.vegetation.crown_sprites import CROWN_RECORD
from mapgen.render.run import sprites as run_sprites
from mapgen.sprites.cache import BuiltSprites, paint_species
from tests.support.crown_sprites import random_sprite, sprite_atlas


def _paint(tmp_path, names):
    directory = tmp_path / "paint"
    directory.mkdir(exist_ok=True)
    species = [{"name": n, "mesh": f"/Trees/{n}", "instances": 3} for n in names]
    meta = {"crowns": {"species": species}}
    (directory / "meta.json").write_text(json.dumps(meta), encoding="utf-8")
    return directory


def _builder(calls, skipped=()):
    def build(game, sweep, paint_dir, only=None):
        names = [s.name for s in paint_species(paint_dir) if s.name not in skipped]
        rng = np.random.default_rng(len(calls))
        titan = np.zeros(2, CROWN_RECORD)
        atlas = sprite_atlas([random_sprite(rng, 6, 7) for _ in names], titan, names)
        calls.append(names)
        species = [{"name": n, "source": "mesh raster"} for n in names]
        return BuiltSprites(atlas, species, list(skipped))

    return build


def test_a_run_builds_the_sprites_once_and_again_only_when_they_fall_behind(tmp_path, monkeypatch):
    calls: list[list[str]] = []
    build = {"pin": "b1"}
    monkeypatch.setattr(run_sprites, "build_crown_sprites", _builder(calls))
    monkeypatch.setattr(run_sprites, "installed_build", lambda game: (build["pin"], {}))
    cache = tmp_path / "sprites"

    def run(names):
        paint = _paint(tmp_path, names)
        return run_sprites.crown_sprites(cache, paint, tmp_path, lambda: None, lambda: None)

    atlas, block = run(["A", "B"])
    assert calls == [["A", "B"]] and atlas.names == ["A", "B"] and len(atlas.titan) == 2
    assert block["sources"] == {"mesh raster": 2} and block["titan_placements"] == 2
    assert block["game_version_pinned"] == "b1" and block["skipped"] == []
    again, same = run(["A", "B"])
    assert len(calls) == 1 and same == block, "a current cache is read, and said the same way"
    assert again.colour.tobytes() == atlas.colour.tobytes()
    run(["A", "B", "C"])
    assert calls[-1] == ["A", "B", "C"], "a species the cache lacks builds it again"
    build["pin"] = "b2"
    run(["A", "B", "C"])
    assert len(calls) == 3, "another build"


def test_a_species_no_sprite_can_be_made_of_does_not_rebuild_every_run(tmp_path, monkeypatch):
    calls: list[list[str]] = []
    monkeypatch.setattr(run_sprites, "build_crown_sprites", _builder(calls, skipped=("B",)))
    monkeypatch.setattr(run_sprites, "installed_build", lambda game: ("b1", {}))
    paint = _paint(tmp_path, ["A", "B"])
    for _ in range(2):
        _atlas, block = run_sprites.crown_sprites(
            tmp_path / "sprites", paint, tmp_path, lambda: None, lambda: None
        )
    assert calls == [["A"]] and block["skipped"] == ["B"]

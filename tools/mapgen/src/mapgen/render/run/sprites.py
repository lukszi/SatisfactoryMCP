"""The crown sprites a painted run draws: the cache for the installed build, built when missing.

A cache whose stamp is the installed build's and which holds every species of the paint store
is read; any other is built again in the run, from the run's own game and paint store, as
``mapgen crown-sprites`` builds it. docs/map/light-and-crowns.md section 36, "Crown sprites".
"""

from __future__ import annotations

import argparse
from collections.abc import Callable, Container
from pathlib import Path

from mapgen.common import Refusal
from mapgen.gamedata.ground.paint_store import PAINT_DIR
from mapgen.gamedata.install import GameReader
from mapgen.gamedata.level.sweep import Sweep
from mapgen.sprites.cache import (
    BuiltSprites,
    SpeciesSource,
    build_sprites,
    paint_species,
    texture_reader,
)
from mapgen.sprites.store import SPRITES_DIR, SpriteAtlas, read_sprites, sprite_stamp, write_sprites
from mapgen.terrain.render_meshes import TITAN_LEAVES, titan_items
from satisfactory_mcp.core.arrays import F32Grid
from satisfactory_mcp.core.gameassets.provenance import InstallNotFound, installed_build
from satisfactory_mcp.core.jsontypes import JsonArray, JsonObject

__all__ = ["NO_SPRITES", "add_paint_flags", "build_crown_sprites", "crown_sprites", "titan_canopy"]

#: The renders command's exit code for a run whose crown sprites could not be built.
NO_SPRITES = 13


def add_paint_flags(parser: argparse.ArgumentParser) -> None:
    """``--paint-dir`` and ``--sprites-dir``: what the painted layer is coloured and drawn from."""
    parser.add_argument(
        "--paint-dir",
        type=Path,
        default=PAINT_DIR,
        help="the paint layers tools/gen_paint_layers.py wrote, for the painted layer",
    )
    parser.add_argument(
        "--sprites-dir",
        type=Path,
        default=SPRITES_DIR,
        help=(
            "the crown sprite cache (python -m mapgen crown-sprites); one that is missing or "
            "for another build is built there first"
        ),
    )


def titan_canopy(game: GameReader, sweep: Sweep) -> dict[str, F32Grid]:
    """Every Titan canopy mesh the sweep places, with its placements' 4 x 4 matrices."""
    prepared, _meta = titan_items(game.store, game.scripts, game.index, sweep)
    return {
        mesh: group.mats
        for mesh, group in prepared.items.items()
        if (group.codes == TITAN_LEAVES).all()
    }


def build_crown_sprites(
    game: GameReader,
    sweep: Callable[[], Sweep],
    paint_dir: Path,
    only: Container[str] | None = None,
) -> BuiltSprites:
    """Every paint store species and the Titan canopy, from the install."""
    import texture2ddecoder as decoder

    species: list[SpeciesSource] = paint_species(paint_dir)
    titan = titan_canopy(game, sweep())
    return build_sprites(game, species, titan, texture_reader(game, decoder), only)


def crown_sprites(
    sprites_dir: Path,
    paint_dir: Path,
    game_dir: Path,
    reader: Callable[[], GameReader],
    sweep: Callable[[], Sweep],
) -> tuple[SpriteAtlas, JsonObject]:
    """The installed build's crown sprites and what the sidecar records of them, built first
    when ``sprites_dir`` holds none that covers the paint store."""
    try:
        pin, _raw = installed_build(game_dir)
    except (InstallNotFound, OSError, ValueError):
        pin = None
    stamp = sprite_stamp(pin)
    names = {source.name for source in paint_species(paint_dir)}
    found = read_sprites(sprites_dir, stamp)
    if found is not None and names <= {*found[0].names, *_skipped(found[1])}:
        print(f"crown sprites: reusing {sprites_dir}")
        return found[0], _record(found[1])
    print(f"crown sprites: none in {sprites_dir} for this build and paint store, building them")
    built = build_crown_sprites(reader(), sweep, paint_dir)
    write_sprites(sprites_dir, stamp, built.atlas, built.species, built.skipped)
    found = read_sprites(sprites_dir, stamp)
    if found is None:
        raise Refusal(NO_SPRITES, f"the crown sprites just written to {sprites_dir} do not read")
    print(f"  {len(built.species)} crown sprites, skipped {built.skipped or 'none'}")
    return found[0], _record(found[1])


def _skipped(recorded: JsonObject) -> list[str]:
    skipped = recorded.get("skipped")
    return [str(name) for name in skipped] if isinstance(skipped, list) else []


def _record(recorded: JsonObject) -> JsonObject:
    """The sidecar's block, the same whether the run built the cache or read it: the stamp,
    the atlas, how many species each source gave, the skipped, the Titan placements."""
    species = recorded.get("species")
    sources: JsonObject = {}
    for entry in species if isinstance(species, list) else []:
        source = str(entry.get("source")) if isinstance(entry, dict) else "?"
        count = sources.get(source, 0)
        sources[source] = (count if isinstance(count, int) else 0) + 1
    keys = ("game_version_pinned", "reader_version", "format", "texel_cm", "atlas")
    block: JsonObject = {key: recorded.get(key) for key in keys}
    skipped: JsonArray = [*_skipped(recorded)]
    block.update(
        sources=sources, skipped=skipped, titan_placements=recorded.get("titan_placements")
    )
    return block

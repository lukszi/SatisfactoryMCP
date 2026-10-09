"""The crown sprite cache built from the install: the paint store's species and the Titan canopy.

``mapgen crown-sprites`` builds it, and so does a render that finds none for the installed
build (``render/run/sprites.py``). docs/map/light-and-crowns.md section 36, "Crown sprites".
"""

from __future__ import annotations

import json
from collections.abc import Callable, Container, Mapping
from dataclasses import dataclass
from pathlib import Path
from types import ModuleType
from typing import NamedTuple

from mapgen.gamedata.ground.landscape_albedo import decode_texture
from mapgen.gamedata.ground.paint_store import META_NAME
from mapgen.gamedata.install import GameReader
from mapgen.gamedata.vegetation.crown_sprites import crown_records, species_name
from mapgen.gamedata.vegetation.tree_surface import SizedTextureReader
from mapgen.sprites.build import species_sprite
from mapgen.sprites.raster import SpritePlanes
from mapgen.sprites.store import SpriteAtlas, encode_atlas
from satisfactory_mcp.core.arrays import F32Grid, F64Grid, U8Grid
from satisfactory_mcp.core.jsontypes import JsonObject, JsonValue

__all__ = ["BuiltSprites", "SpeciesSource", "build_sprites", "paint_species", "texture_reader"]


class SpeciesSource(NamedTuple):
    """A species the paint store's ``crowns`` block names: its name, mesh and instances."""

    name: str
    mesh: str
    instances: int


@dataclass(frozen=True)
class BuiltSprites:
    """The atlas, each species' sidecar entry, and the species no sprite could be made of."""

    atlas: SpriteAtlas
    species: list[JsonObject]
    skipped: list[str]


def paint_species(paint_dir: Path) -> list[SpeciesSource]:
    """Every species in the paint store's ``crowns`` block, in its order."""
    meta: JsonValue = json.loads((paint_dir / META_NAME).read_text(encoding="utf-8"))
    crowns = meta.get("crowns") if isinstance(meta, dict) else None
    species = crowns.get("species") if isinstance(crowns, dict) else None
    found: list[SpeciesSource] = []
    for entry in species if isinstance(species, list) else []:
        if not isinstance(entry, dict):
            continue
        count = entry.get("instances")
        name, mesh = str(entry["name"]), str(entry["mesh"])
        found.append(SpeciesSource(name, mesh, count if isinstance(count, int) else 0))
    return found


def texture_reader(game: GameReader, decoder: ModuleType) -> SizedTextureReader:
    """A texture by asset path, RGBA, at the largest mip no longer than the size asked."""

    def read(path: str, side: int) -> U8Grid:
        asset = path.split(".")[0].removeprefix("/Game/FactoryGame/")
        return decode_texture(game, decoder, asset, side, channels=4)

    return read


def build_sprites(
    game: GameReader,
    species: list[SpeciesSource],
    titan: Mapping[str, F32Grid | F64Grid],
    texture_rgba: SizedTextureReader,
    only: Container[str] | None = None,
    report: Callable[[str], None] = print,
) -> BuiltSprites:
    """A sprite for every species, then for every Titan canopy mesh of ``titan`` (mesh to its
    placements' 4 x 4 matrices), and the Titan placements as records; ``only`` names the
    species to build, all when None."""
    sprites: list[tuple[str, SpritePlanes]] = []
    entries: list[JsonObject] = []
    skipped: list[str] = []
    canopy = [
        SpeciesSource(species_name(mesh.split(".")[0]), mesh.split(".")[0], len(mats))
        for mesh, mats in sorted(titan.items())
    ]
    for source in [*species, *canopy]:
        if only is not None and source.name not in only:
            continue
        sprite = species_sprite(game, source.name, source.mesh, texture_rgba)
        if sprite is None:
            skipped.append(source.name)
            continue
        sprites.append((source.name, sprite.planes))
        entry: JsonObject = {**source._asdict(), "source": sprite.source}
        if source in canopy:
            entry["titan"] = True
        entries.append(entry | sprite.record)
        report(f"  {source.name}: {sprite.source}")
    names = [name for name, _planes in sprites]
    placed = {mesh.split(".")[0]: mats for mesh, mats in titan.items()}
    records, _stats = crown_records(placed, names)
    return BuiltSprites(encode_atlas(sprites, records), entries, skipped)

"""Build the crown sprite cache: every tree species the painted layer draws, from above.

    uv run --extra gen python -m mapgen crown-sprites [--gpu]

The species are the paint store's (``python -m mapgen paint``); each one's mesh, textures and
billboard are read from the install, and the sprites go to ``data/local/crown-sprites/``. A
cache whose stamp matches the build is kept unless ``--force``. docs/map/light-and-crowns.md
section 36, "Crown sprites".
"""

from __future__ import annotations

import json
import time
from pathlib import Path

from mapgen.common import base_parser, require_gen
from mapgen.gamedata.ground.landscape_albedo import decode_texture
from mapgen.gamedata.ground.paint_store import META_NAME, PAINT_DIR
from mapgen.gamedata.install import open_game
from mapgen.jit import add_gpu_flag, gpu_on
from mapgen.sprites.build import ATLAS, species_sprite
from mapgen.sprites.raster import SpritePlanes
from mapgen.sprites.store import (
    SPRITES_DIR,
    encode_atlas,
    read_sprites,
    sprite_stamp,
    write_sprites,
)
from satisfactory_mcp.core.arrays import U8Grid
from satisfactory_mcp.core.gameassets.provenance import InstallNotFound, installed_build
from satisfactory_mcp.core.jsontypes import JsonObject, JsonValue

__all__ = ["main"]


def _parse_args() -> tuple[Path, Path, Path, list[str], bool]:
    parser = base_parser((__doc__ or "").splitlines()[0])
    parser.add_argument("--paint-dir", type=Path, default=PAINT_DIR, help="the paint store")
    parser.add_argument(
        "-o", "--out-dir", type=Path, default=SPRITES_DIR, help="destination (gitignored)"
    )
    parser.add_argument(
        "--species", action="append", default=[], help="only these species (repeatable)"
    )
    parser.add_argument("--force", action="store_true", help="rebuild a cache that matches")
    add_gpu_flag(parser)
    args = parser.parse_args()
    return args.game, args.paint_dir, args.out_dir, args.species, args.force


def _species(paint_dir: Path) -> list[tuple[str, str, int]]:
    """``(name, mesh, instances)`` of every species in the paint store's crowns block."""
    meta: JsonValue = json.loads((paint_dir / META_NAME).read_text(encoding="utf-8"))
    crowns = meta.get("crowns") if isinstance(meta, dict) else None
    species = crowns.get("species") if isinstance(crowns, dict) else None
    found: list[tuple[str, str, int]] = []
    for entry in species if isinstance(species, list) else []:
        if not isinstance(entry, dict):
            continue
        count = entry.get("instances")
        found.append(
            (str(entry["name"]), str(entry["mesh"]), count if isinstance(count, int) else 0)
        )
    return found


def main() -> int:
    game_dir, paint_dir, out_dir, only, force = _parse_args()
    require_gen("ooz", "texture2ddecoder", "PIL.Image")
    import texture2ddecoder as decoder

    try:
        pin, _raw = installed_build(game_dir)
    except (InstallNotFound, OSError, ValueError) as exc:
        print(f"not a game install: {exc}")
        return 1
    stamp = sprite_stamp(pin)
    if not force and not only and read_sprites(out_dir, stamp) is not None:
        print(f"{out_dir} is current for build {pin}; --force rebuilds it")
        return 0
    if not (paint_dir / META_NAME).is_file():
        print(f"no paint store at {paint_dir}: run `python -m mapgen paint` first")
        return 1
    game = open_game(game_dir)

    def texture_rgba(path: str, side: int) -> U8Grid:
        asset = path.split(".")[0].removeprefix("/Game/FactoryGame/")
        return decode_texture(game, decoder, asset, side, channels=4)

    started = time.time()
    sprites: list[tuple[str, SpritePlanes]] = []
    entries: list[JsonObject] = []
    skipped: list[str] = []
    for name, mesh, instances in _species(paint_dir):
        if only and name not in only:
            continue
        sprite = species_sprite(game, name, mesh, texture_rgba)
        if sprite is None:
            skipped.append(name)
            continue
        sprites.append((name, sprite.planes))
        entries.append(
            {"name": name, "mesh": mesh, "source": sprite.source, "instances": instances}
            | sprite.record
        )
        print(f"  {name}: {sprite.source}", flush=True)
    atlas = encode_atlas(sprites)
    written = write_sprites(out_dir, stamp, atlas, entries)
    from_atlas = sum(1 for e in entries if e["source"] == ATLAS)
    print(
        f"wrote {written}: {len(entries)} species ({from_atlas} from the game's top view), "
        f"atlas {atlas.colour.shape[1]}x{atlas.colour.shape[0]}, skipped {skipped or 'none'}, "
        f"{'GPU' if gpu_on() else 'CPU'} fill, {time.time() - started:.0f}s"
    )
    return 0

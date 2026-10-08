"""Build the crown sprite cache: every tree species the painted layer draws, from above.

    uv run --extra gen python -m mapgen crown-sprites [--gpu]

The species are the paint store's (``python -m mapgen paint``) and the Titan canopy's meshes,
whose placements a sweep of the levels finds; each one's mesh, textures and billboard are
read from the install, and the sprites go to ``data/local/crown-sprites/``. A cache whose
stamp matches the build is kept unless ``--force``; a render builds a missing one itself.
docs/map/light-and-crowns.md section 36, "Crown sprites".
"""

from __future__ import annotations

import time
from pathlib import Path

from mapgen.common import base_parser, require_gen
from mapgen.gamedata.ground.paint_store import META_NAME, PAINT_DIR
from mapgen.gamedata.install import open_game
from mapgen.jit import add_gpu_flag, gpu_on
from mapgen.render.run.cached_rasters import LevelSweep
from mapgen.render.run.sprites import build_crown_sprites
from mapgen.sprites.build import ATLAS
from mapgen.sprites.store import SPRITES_DIR, read_sprites, sprite_stamp, write_sprites
from satisfactory_mcp.core.gameassets.provenance import InstallNotFound, installed_build

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


def main() -> int:
    game_dir, paint_dir, out_dir, only, force = _parse_args()
    require_gen("ooz", "texture2ddecoder", "PIL.Image")
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
    started = time.time()
    level = LevelSweep(game.store, game.scripts, progress=True)
    built = build_crown_sprites(game, lambda: level.sweep, paint_dir, set(only) or None)
    atlas = built.atlas
    written = write_sprites(out_dir, stamp, atlas, built.species, built.skipped)
    from_atlas = sum(1 for e in built.species if e["source"] == ATLAS)
    print(
        f"wrote {written}: {len(built.species)} species ({from_atlas} from the game's top view), "
        f"{len(atlas.titan)} Titan canopy placements, atlas {atlas.colour.shape[1]}x"
        f"{atlas.colour.shape[0]}, skipped {built.skipped or 'none'}, "
        f"{'GPU' if gpu_on() else 'CPU'} fill, {time.time() - started:.0f}s"
    )
    return 0

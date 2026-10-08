"""Derive the game-painted style's display targets from the game's own data.

    python -m mapgen calibrate [--paint-dir data/local/paint] [--field data/local/heightmap]
                               [--check]

Reads the paint store, the heightfield's water and the install's area map; writes only
``targets.derived.json`` beside the store. ``--check`` prints the table and writes nothing.
docs/map/calibration.md section 31.
"""

from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

from mapgen.common import LOCAL_DIR, base_parser, require_gen
from mapgen.gamedata.ground.biome import area_names, read_biome
from mapgen.gamedata.ground.paint_store import PAINT_DIR
from mapgen.gamedata.install import missing_container, open_game
from mapgen.palette.painted.albedo import load_paint_meta
from mapgen.palette.painted.derive.gate import gate_hex
from mapgen.palette.painted.derive.scene import area_grid, scene_from_store
from mapgen.palette.painted.derive.targets import (
    TARGETS_NAME,
    Derived,
    NoDaylight,
    derive,
    gate_declines,
    screenshot_target,
    stamp_of,
    targets_json,
)
from mapgen.palette.painted.shapes import CalibrationStyle
from mapgen.palette.styles import PAINTED_PALETTE
from satisfactory_mcp.domain.spatial import heightfield as hf

__all__ = ["main"]

#: Exit codes: no paint store, a store without daylight, and a game install it cannot use
#: (no container there, or the output inside it).
NO_STORE, NO_DAYLIGHT, BAD_GAME = 1, 2, 1


def _parse_args() -> argparse.Namespace:
    parser = base_parser((__doc__ or "").splitlines()[0])
    parser.add_argument("--paint-dir", type=Path, default=PAINT_DIR, help="the paint store")
    parser.add_argument(
        "--field",
        type=Path,
        default=LOCAL_DIR / hf.DIR_NAME,
        help="the heightfield, whose water rehomes the areas offshore as the painter does",
    )
    parser.add_argument(
        "--out", type=Path, default=None, help=f"default <paint-dir>/{TARGETS_NAME}"
    )
    parser.add_argument("--check", action="store_true", help="print the table, write nothing")
    return parser.parse_args()


def report(derived: Derived, cal: CalibrationStyle) -> list[str]:
    """The table: key, derived colour, the palette's screenshot colour, their distance and its
    chroma-and-hue part against the gate's allowance, light. ``*`` marks the keys a render takes
    derived, ``x`` those the derive gate declines."""
    wanted = set(cal.get("derived_keys", []))
    declined = gate_declines(cal, derived.hexes())
    lines = [f"E {derived.exposure:.4f} over {derived.texels} bake texels", *derived.notes]
    for t in derived.targets:
        now = screenshot_target(cal, t.key)
        gap = " " * 16
        if t.hex and now:
            verdict = gate_hex(t.hex, now)
            gap = f"{verdict.delta_e:5.1f} {verdict.chromatic:4.1f}/{verdict.allowance:4.1f}"
        mark = "x" if t.key in declined else "*" if t.key in wanted else " "
        why = f"  ({t.rule.error})" if t.rule.error else ""
        why += f"  (gate: {declined[t.key]})" if t.key in declined else ""
        lines.append(f"{mark} {t.key:70s} {t.hex or '-':8s} {now or '-':8s} {gap}  {t.light}{why}")
    return lines


def main() -> int:
    args = _parse_args()
    paint_dir: Path = args.paint_dir
    out: Path = args.out or paint_dir / TARGETS_NAME
    meta = load_paint_meta(paint_dir)
    if meta is None:
        print(f"no paint store at {paint_dir}: run `python -m mapgen paint` first")
        return NO_STORE
    if not meta.get("lighting"):
        print(f"the paint store at {paint_dir} keeps no daylight: re-run `python -m mapgen paint`")
        return NO_DAYLIGHT
    if out.resolve().is_relative_to(Path(args.game).resolve()):
        print(f"{out} is inside the game install, which this command never writes")
        return BAD_GAME
    if why := missing_container(args.game):
        print(f"not a game install: {why}")
        return BAD_GAME
    require_gen("ooz")
    started = time.time()
    game = open_game(args.game)
    biome = read_biome(game.store, game.scripts)
    field = hf.load_field(args.field, cache=False)
    if field is None:
        print(f"  no heightfield at {args.field}: the areas are not rehomed offshore")
    grid = meta["grid"]
    areas = area_grid(biome, area_names(biome), field, (grid["height"], grid["width"]))
    cal = PAINTED_PALETTE["calibration"]
    try:
        derived = derive(scene_from_store(paint_dir, meta, areas), cal)
    except NoDaylight as exc:
        print(str(exc))
        return NO_DAYLIGHT
    print("\n".join(report(derived, cal)))
    if args.check:
        return 0
    stamp = stamp_of(meta.get("digest"), areas.digest(), cal)
    body = targets_json(derived, stamp, meta.get("cl"))
    out.write_text(json.dumps(body, indent=1), encoding="utf-8")
    print(f"wrote {out} in {time.time() - started:.0f}s")
    return 0

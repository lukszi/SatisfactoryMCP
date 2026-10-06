"""Generate data/region_names.json -- the ADVISORY region-name layer, from the game's areas.

    uv run --extra gen python tools/gen_region_names.py

docs/spatial-and-map.md §7.2 is the design. ``core.gameassets.maparea`` reads the area raster,
the same asset ``mapgen.gamedata.biome`` pins to the map square. Two grids come out, each with
a confidence grid of its own shape: 256 m for ``/api/regions`` and 64 m for
``domain.spatial.regions``. The emitted ``_meta`` carries the naming rules and the measurements.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import UTC, datetime
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
for _path in (ROOT / "src", ROOT / "tools" / "mapgen" / "src"):
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))

# The corners and the edge-ratio statistic come from the generators that measured them, so
# three artifacts cannot drift into three opinions about where the world is.
from mapgen.common import base_parser, require_gen
from mapgen.gamedata.biome import calibrate_biome
from mapgen.gamedata.frame import BOUNDS_M
from satisfactory_mcp.core.gameassets.container import (
    CONTAINER,
    open_container,
    paks_dir,
    read_artwork_sheet,
)
from satisfactory_mcp.core.gameassets.iostore import oodle_decompress
from satisfactory_mcp.core.gameassets.maparea import (
    MAP_AREA_CLASS,
    MAP_AREA_DIR,
    MAP_AREA_PATH,
    MapAreaError,
    MapAreas,
    read_map_areas,
)
from satisfactory_mcp.core.gameassets.packages import ScriptObjects
from satisfactory_mcp.core.gameassets.provenance import (
    InstallNotFound,
    installed_build,
    installed_build_from_exe,
    read_path,
)
from satisfactory_mcp.domain.spatial import geo

DEST = ROOT / "data" / "region_names.json"

VOID = "."

#: The grid ``/api/regions`` serves and the client's arithmetic is written against.
GRID_X0, GRID_Y0 = -336_000.0, -380_000.0
GRID_CELL = 25_600.0
GRID_NX = GRID_NY = 30

#: 64 m divides the 256 m cell exactly, which keeps the coarse grid a majority of this one
#: rather than a second independent downsample. ``_meta.grids.why_64_m`` is the measurement.
FINE_CELL = 6_400.0

#: The game's own name for each place, keyed by the localisation key its ``Area_*`` asset
#: states. A key that is not in here stops the run rather than being labelled by guesswork.
DISPLAY_NAMES = {
    "Locations/AbyssCliffs": "Abyss Cliffs",
    "Locations/BlueCrater": "Blue Crater",
    "Locations/CraterLakes": "Crater Lakes",
    "Locations/DesertCanyon": "Desert Canyons",
    "Locations/DuneDesert": "Dune Desert",
    "Locations/EasternDuneForest": "Eastern Dune Forest",
    "Locations/GrassFields": "Grass Fields",
    "Locations/JungleSpires": "Jungle Spires",
    "Locations/LakeForest": "Lake Forest",
    "Locations/MazeCanyon": "Maze Canyons",
    "Locations/NoMansLand": "No Man's Land",
    "Locations/NorthernForest": "Northern Forest",
    "Locations/RedBambooFields": "Red Bamboo Fields",
    "Locations/RedJungle": "Red Jungle",
    "Locations/RockyDesert": "Rocky Desert",
    "Locations/SouthernForest": "Southern Forest",
    "Locations/SpireCoast": "Spire Coast",
    "Locations/Swamp": "Swamp",
    "Locations/TitanForest": "Titan Forest",
    "Locations/WesternDuneForest": "Western Dune Forest",
}

#: The two labels that are not their key verbatim, stated as data so ``_meta`` can carry the
#: deviation rather than a reader having to diff two lists to find it.
PLURALISED = {"Locations/DesertCanyon": "Desert Canyons", "Locations/MazeCanyon": "Maze Canyons"}

#: What the game calls ground it does not otherwise name. Labelled rather than blanked.
UNNAMED_LABEL = DISPLAY_NAMES["Locations/NoMansLand"]

#: Collectible categories that stand on land and so mark it. They decide only whether an
#: unnamed cell is coast or ocean, never what anything is called.
LAND_MASK_CATEGORIES = (
    "crashed_drop_pod",
    "mercer_shrine",
    "mercer_sphere",
    "power_slug_blue",
    "power_slug_purple",
    "power_slug_yellow",
    "somersloop",
)

#: A cell the game leaves unnamed with no known static object within this far is ocean or
#: off-map. Cell-to-nearest distances are strongly bimodal -- median 123 m on land against
#: p90 1212 m -- so the cut sits in the gap rather than on a slope.
VOID_DISTANCE_M = 1000.0

#: The confidence letters, and what each one measures about its own cell.
INTERIOR, BOUNDARY, UNNAMED = "l", "b", "u"
CONFIDENCE_LEGEND = {
    INTERIOR: "interior cell: one area covers the whole 256 m cell",
    BOUNDARY: "boundary cell: an exact area boundary runs through it",
    UNNAMED: "unnamed cell: the game names no region here, so the label is its No Man's Land",
    VOID: "void: the game names no region and no known object is within 1 km -- ocean or off-map",
}

#: Measured once, on 2026-07-30, against the committed wiki trace at the commit before it was
#: retired. It cannot be measured again -- the source it compares against is deleted -- so
#: these figures are transcribed and no run gates on them.
RETIRED_TRACE_COMPARISON = {
    "what": (
        "data/satisfactory_regions.json, a hand trace of satisfactory.wiki.gg's Biome Map "
        "image (CC BY-SA 4.0), rasterised at 256 m. Retired in the same commit as this "
        "measurement; the geometry it supplied is now read from the game."
    ),
    "non_void_cells": 768,
    "cells_landing_on_a_named_game_area": 481,
    "cells_landing_on_a_named_game_area_pct": 62.6,
    "cells_comparable_by_name": 401,
    "cells_agreeing": 273,
    "agreement_pct": 68.1,
    "spire_coast": (
        "62 of the 128 misses. The wiki draws one coastal ring; the game draws a smaller "
        "Spire Coast and gives the rest of that ring to four other areas -- Rocky Desert "
        "(40 cells), Desert Canyons (11), Dune Desert (10) and Swamp (1). The game's "
        "granularity is kept: those cells now carry the name the game gives them, which is "
        "the whole point of re-deriving the geometry."
    ),
    "names_the_wiki_had_and_the_game_does_not": ["Western Beaches", "Snaketree Forest"],
    "names_the_game_has_and_the_wiki_did_not": ["Blue Crater is a game name, not a wiki one"],
}

KNOWN_LIMITATIONS = [
    (
        "The published grid is 256 m and the lookup grid is 64 m, so a name near a "
        "boundary can be one region out. The SOURCE boundaries are exact -- this is "
        "the cost of publishing a small table, not of not knowing."
    ),
    (
        "No Man's Land is a label, not a region with edges anybody drew: it is "
        "everything the game does not name, from the outer coast to open ocean, and "
        "the land mask is what separates the two here."
    ),
    (
        "The corners are MEASURED, not stated in the asset. Nothing in the texture "
        "says where its 4096 texels go; calibration above re-measures it every run "
        "and this file refuses to write if the pin stops holding."
    ),
]


def load_positioned_rows(name: str, key: str) -> list[dict]:
    """The ``key`` rows of a committed ``data/`` table, each of which must carry x and y."""
    path = ROOT / "data" / name
    if not path.exists():
        raise SystemExit(f"{path.relative_to(ROOT)} missing -- the land mask needs it")
    rows = json.loads(path.read_text(encoding="utf-8"))[key]
    missing = [r for r in rows if "x" not in r or "y" not in r]
    if missing:
        raise SystemExit(f"{name}: {len(missing)} of {len(rows)} {key} carry no x/y")
    return rows


def reference_points() -> tuple[np.ndarray, dict[str, int]]:
    """Static world objects, used purely to tell coast from ocean.

    Positions only: the raster names every cell, so these decide one thing, which is whether
    an unnamed cell is ground a player can stand on or open water.
    """
    pts: list[tuple[float, float]] = []
    counts: dict[str, int] = {}
    nodes = load_positioned_rows("world_resource_nodes.json", "nodes")
    pts.extend((node["x"], node["y"]) for node in nodes)
    counts["resource_nodes"] = len(nodes)
    for row in load_positioned_rows("world_collectibles.json", "collectibles"):
        category = row.get("category")
        if category in LAND_MASK_CATEGORIES:
            pts.append((row["x"], row["y"]))
            counts[category] = counts.get(category, 0) + 1
    for category in LAND_MASK_CATEGORIES:
        if category not in counts:
            raise SystemExit(f"world_collectibles.json: no {category} rows -- schema changed?")
    return np.array(pts, np.float64), dict(sorted(counts.items()))


def texel_bounds(lo: float, hi: float, span: tuple[float, float], width: int) -> tuple[int, int]:
    """The half-open texel range a world-space interval covers, clamped to the raster."""
    start, end = span
    first = max(0, min(width, round((lo - start) / (end - start) * width)))
    last = max(0, min(width, round((hi - start) / (end - start) * width)))
    return first, last


def rasterise(
    area: np.ndarray, names: list[str | None], cell: float, nx: int, ny: int
) -> tuple[list[list[str | None]], list[list[bool]]]:
    """Majority display name per grid cell, and whether that cell is one area throughout.

    The name is what most of the cell is, and the purity flag is whether "most" was "all".
    Cells whose box falls outside the raster come back as ``None``: the grid is 7,680 m
    square and the map is 7,500 m, so a margin of it is off the texture entirely.
    """
    width = area.shape[0]
    x_span = (BOUNDS_M["x_min_m"] * 100, BOUNDS_M["x_max_m"] * 100)
    y_span = (BOUNDS_M["y_min_m"] * 100, BOUNDS_M["y_max_m"] * 100)
    grid: list[list[str | None]] = []
    pure: list[list[bool]] = []
    for j in range(ny):
        v0, v1 = texel_bounds(GRID_Y0 + j * cell, GRID_Y0 + (j + 1) * cell, y_span, width)
        row: list[str | None] = []
        row_pure: list[bool] = []
        band = area[v0:v1] if v1 > v0 else None
        for i in range(nx):
            u0, u1 = texel_bounds(GRID_X0 + i * cell, GRID_X0 + (i + 1) * cell, x_span, width)
            if band is None or u1 <= u0:
                row.append(None)
                row_pure.append(False)
                continue
            values, counts = np.unique(band[:, u0:u1], return_counts=True)
            labels = {names[int(v)] for v in values}
            row.append(names[int(values[counts.argmax()])])
            row_pure.append(len(labels) == 1)
        grid.append(row)
        pure.append(row_pure)
    return grid, pure


def nearest_distance_m(points: np.ndarray, x: float, y: float) -> float:
    return float(np.sqrt(((points[:, 0] - x) ** 2 + (points[:, 1] - y) ** 2).min())) / 100.0


def classify_cells(
    grid: list[list[str | None]],
    pure: list[list[bool]],
    points: np.ndarray,
    cell: float,
    inherited_void: tuple[set[tuple[int, int]], int] | None,
) -> tuple[set[tuple[int, int]], list[str], dict[str, int]]:
    """Void set, confidence letters and cell counts for one grid.

    Mutates ``grid``: an unnamed cell the mask calls land is written back as the No Man's
    Land label, so every ``None`` left in it is void.

    ``inherited_void`` is ``(coarse void cells, cells per coarse cell)``: a fine cell is void
    exactly when the coarse cell over it is, so the two grids share one coastline. Without it
    the mask is asked at this grid's own centres.
    """
    void: set[tuple[int, int]] = set()
    rows: list[str] = []
    counts = {"interior": 0, "boundary": 0, "unnamed": 0, "void": 0}
    for j, line in enumerate(grid):
        row = ""
        for i, name in enumerate(line):
            if name is not None and name != UNNAMED_LABEL:
                key = "interior" if pure[j][i] else "boundary"
                counts[key] += 1
                row += INTERIOR if pure[j][i] else BOUNDARY
                continue
            if inherited_void is not None:
                coarse_void, ratio = inherited_void
                is_void = (i // ratio, j // ratio) in coarse_void
            else:
                cx, cy = GRID_X0 + (i + 0.5) * cell, GRID_Y0 + (j + 0.5) * cell
                is_void = nearest_distance_m(points, cx, cy) > VOID_DISTANCE_M
            if is_void:
                void.add((i, j))
                counts["void"] += 1
                row += VOID
            else:
                grid[j][i] = UNNAMED_LABEL
                counts["unnamed"] += 1
                row += UNNAMED
        rows.append(row)
    return void, rows, counts


def letters_for(names: list[str]) -> dict[str, str]:
    """One character per region, assigned in name order.

    Alphabetical: the letters key the frontend's colour table, so the rule has to be one a
    person can check by eye. 26 letters against 19 regions, and the run stops rather than
    wrapping.
    """
    if len(names) > 26:
        raise SystemExit(
            f"{len(names)} regions and 26 letters. The grid is a character raster and the "
            "legend has run out of alphabet; the encoding has to change before this can."
        )
    return {name: chr(ord("A") + i) for i, name in enumerate(sorted(names))}


def encode_grid_rows(
    grid: list[list[str | None]], letters: dict[str, str], void: set[tuple[int, int]]
) -> list[str]:
    """One string per grid row: a region letter per cell, ``VOID`` where nothing is named."""
    return [
        "".join(
            VOID if (i, j) in void or name is None else letters[name] for i, name in enumerate(row)
        )
        for j, row in enumerate(grid)
    ]


def _parse_args() -> argparse.Namespace:
    parser = base_parser(__doc__.splitlines()[0])
    parser.add_argument(
        "-o", "--out", type=Path, default=DEST, help=f"destination (default {DEST})"
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="rewrite a table this run cannot show was cut from the installed build",
    )
    return parser.parse_args()


def _grid_meta(fine_nx: int, fine_ny: int) -> dict:
    return {
        "x0": GRID_X0,
        "y0": GRID_Y0,
        "cell": GRID_CELL,
        "nx": GRID_NX,
        "ny": GRID_NY,
        "void": VOID,
        "fine_cell": FINE_CELL,
        "fine_nx": fine_nx,
        "fine_ny": fine_ny,
    }


def main() -> int:
    args = _parse_args()

    versions = require_gen("ooz", "texture2ddecoder", "PIL.Image")
    import texture2ddecoder as decoder
    from PIL import Image

    Image.MAX_IMAGE_PIXELS = None

    try:
        build_pin, _raw = installed_build(args.game)
    except InstallNotFound as exc:
        print(f"{exc}\nPass --game if the install is somewhere else.")
        return 1
    exe_build = installed_build_from_exe(args.game)
    print(f"installed build: {build_pin}")

    dest: Path = args.out
    if dest.is_file() and not args.force and not check_existing_pin(dest, build_pin):
        return 3

    paks = paks_dir(args.game)
    if not (paks / f"{CONTAINER}.utoc").exists():
        print(f"no {CONTAINER}.utoc under {paks}")
        return 1
    print(f"reading the game's own assets from {paks} with pyooz {versions['pyooz']}")
    store = open_container(args.game)
    scripts = ScriptObjects(paks, oodle_decompress)
    try:
        areas = read_map_areas(store, scripts)
    except MapAreaError as exc:
        print(f"{exc}\nThere is no other source for where a region is, so nothing was written.")
        return 4

    raster = np.frombuffer(areas.texels, np.uint8).reshape(areas.width, areas.width)
    names = area_display_names(areas)
    if names is None:
        return 5
    print(
        f"  {areas.width}x{areas.width} palette indices, {len(areas.areas)} entries, "
        f"{len(areas.assets)} area assets, {len({n for n in names if n})} named regions"
    )

    calibration = calibrate_pin(store, areas, raster, decoder, Image)
    if calibration is None:
        return 6

    # ---- the two grids, each with a confidence of its own shape ----------------------
    coarse, coarse_pure = rasterise(raster, names, GRID_CELL, GRID_NX, GRID_NY)
    fine_nx = int(GRID_NX * GRID_CELL / FINE_CELL)
    fine_ny = int(GRID_NY * GRID_CELL / FINE_CELL)
    fine, fine_pure = rasterise(raster, names, FINE_CELL, fine_nx, fine_ny)

    points, mask_counts = reference_points()
    print(f"land mask from {len(points)} static world objects (coast or ocean, nothing else)")

    # The mask is asked once, at 256 m, and the fine grid inherits the answer: asking it at
    # both resolutions gives the two grids two coastlines.
    void_coarse, coarse_conf, coarse_counts = classify_cells(
        coarse, coarse_pure, points, GRID_CELL, None
    )
    per_fine = int(GRID_CELL / FINE_CELL)
    void_fine, fine_conf, fine_counts = classify_cells(
        fine, fine_pure, points, FINE_CELL, (void_coarse, per_fine)
    )
    print(cell_count_line(GRID_CELL, coarse_counts))
    print(cell_count_line(FINE_CELL, fine_counts))

    present = sorted(
        {name for row in coarse for name in row if name} | {n for r in fine for n in r if n}
    )
    letters = letters_for(present)
    region_rows = encode_grid_rows(coarse, letters, void_coarse)
    fine_rows = encode_grid_rows(fine, letters, void_fine)
    regions = region_extents(region_rows, letters, areas, names)

    out = {
        "_meta": build_meta(
            build_pin=build_pin,
            exe_build=exe_build,
            areas=areas,
            calibration=calibration,
            coarse_counts=coarse_counts,
            fine_counts=fine_counts,
            mask_counts=mask_counts,
            mask_points=len(points),
            name_map=area_name_map(areas),
            unreferenced=sorted(_unreferenced_assets(store, areas)),
            fine_shape=(fine_nx, fine_ny),
            versions=versions,
        ),
        "grid_meta": _grid_meta(fine_nx, fine_ny),
        "legend": {letter: name for name, letter in sorted(letters.items(), key=lambda kv: kv[1])},
        # Four grids in two pairs: each confidence is indexed exactly like the grid it is
        # named after, and the two pairs are different shapes.
        "region_grid": region_rows,
        "confidence_grid": coarse_conf,
        "fine_grid": fine_rows,
        "fine_confidence": fine_conf,
        "regions": dict(sorted(regions.items())),
    }

    # The trailing newline is load-bearing: the committed blob carries one, and without it
    # every regeneration reports a diff and so says nothing on any run.
    dest.write_text(json.dumps(out, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"wrote {_shown(dest)}  {dest.stat().st_size} B  {len(regions)} regions")
    return 0


def _shown(path: Path) -> Path:
    """``path`` relative to the repository when it is inside it, as given otherwise."""
    return path.relative_to(ROOT) if path.is_relative_to(ROOT) else path


def check_existing_pin(dest: Path, build_pin: str) -> bool:
    """Whether ``dest`` may be replaced: True unless it states another build than this one.

    A table with no readable pin may be replaced; one cut from another build describes
    another world's coastline, so the refusal is printed and False returned.
    """
    try:
        existing = json.loads(dest.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        existing = {}
    pinned = read_path(existing, ("_meta", "game_version_pinned"))
    if pinned is None or pinned == build_pin:
        return True
    print(
        f"{_shown(dest)} was cut from another build, and a region table from "
        "another build describes another world's coastline.\n"
        f"  installed:   {build_pin}\n"
        f"  that table:  {pinned}\n"
        "Drift is announced rather than overwritten. Pass --force to replace it."
    )
    return False


def area_display_names(areas: MapAreas) -> list[str | None] | None:
    """The display name of each palette entry, or None (printed) for a key with no label."""
    unknown = sorted(
        {a.key or f"<{a.asset} states no display name>" for a in areas.areas if a is not None}
        - set(DISPLAY_NAMES)
    )
    if unknown:
        print(
            "the game names a region this file has no label for: "
            + ", ".join(unknown)
            + "\nA label cannot be invented -- add it to DISPLAY_NAMES with the name the game "
            "shows for it. Nothing was written."
        )
        return None
    return [None if area is None else DISPLAY_NAMES[area.key] for area in areas.areas]


def calibrate_pin(store, areas: MapAreas, raster: np.ndarray, decoder, image_mod) -> dict | None:
    """The raster's pin to the map square, re-measured rather than inherited; None, with the
    reason printed, when it no longer holds by the required margin."""
    biome = {"width": areas.width, "area": raster}
    artwork = read_artwork_sheet(store, decoder, image_mod)
    calibration = calibrate_biome(biome, artwork, image_mod)
    print(
        f"  calibration: edge ratio {calibration['edge_ratio_at_the_pin']} at the pin against "
        f"{calibration['edge_ratio_at_the_best_rival_shift']} for the best rival shift "
        f"-- margin {calibration['margin_over_the_best_rival']}x"
    )
    if not calibration["pin_holds"]:
        print(
            "  the raster no longer sits on the map square by the required margin, so every "
            "cell below would be named from a picture pinned to the wrong place. Nothing "
            "was written; look at the asset."
        )
        return None
    return calibration


def cell_count_line(cell: float, counts: dict[str, int]) -> str:
    return (
        f"cells at {cell / 100:.0f} m: {counts['interior']} interior, "
        f"{counts['boundary']} boundary, {counts['unnamed']} unnamed, {counts['void']} void"
    )


def region_extents(
    region_rows: list[str],
    letters: dict[str, str],
    areas: MapAreas,
    names: list[str | None],
) -> dict[str, dict]:
    """Per region: bbox, centroid and cells, read off the published grid so containment holds."""
    regions: dict[str, dict] = {}
    for name, letter in letters.items():
        cells = [
            (i, j) for j in range(GRID_NY) for i in range(GRID_NX) if region_rows[j][i] == letter
        ]
        if not cells:
            continue
        xs = [GRID_X0 + i * GRID_CELL for i, _ in cells] + [
            GRID_X0 + (i + 1) * GRID_CELL for i, _ in cells
        ]
        ys = [GRID_Y0 + j * GRID_CELL for _, j in cells] + [
            GRID_Y0 + (j + 1) * GRID_CELL for _, j in cells
        ]
        cx = sum(GRID_X0 + (i + 0.5) * GRID_CELL for i, _ in cells) / len(cells)
        cy = sum(GRID_Y0 + (j + 0.5) * GRID_CELL for _, j in cells) / len(cells)
        regions[name] = {
            "letter": letter,
            "bbox": [int(min(xs)), int(min(ys)), int(max(xs)), int(max(ys))],
            "centroid": [int(cx), int(cy)],
            "cells": len(cells),
            "area_km2": round(len(cells) * (GRID_CELL / 100_000) ** 2, 2),
            "grid_cells": sorted(
                {
                    geo.grid_cell(GRID_X0 + (i + 0.5) * GRID_CELL, GRID_Y0 + (j + 0.5) * GRID_CELL)
                    for i, j in cells
                }
            ),
            "areas": sorted(
                {
                    area.asset
                    for index, area in enumerate(areas.areas)
                    if area is not None and names[index] == name
                }
            ),
        }
    return regions


def area_name_map(areas: MapAreas) -> dict[str, dict]:
    """Area asset -> its package, localisation key, display name, palette indices and texels."""
    return {
        area.asset: {
            "package": area.stem,
            "display_name_key": area.key,
            "string_table": area.string_table,
            "display_name": DISPLAY_NAMES[area.key],
            "palette_indices": [i for i, a in enumerate(areas.areas) if a == area],
            "texels": sum(areas.texels.count(i) for i, a in enumerate(areas.areas) if a == area),
        }
        for area in sorted({a for a in areas.areas if a is not None}, key=lambda a: a.asset)
    }


def _unreferenced_assets(store, areas: MapAreas) -> set[str]:
    """Area assets beside the texture that no palette index reaches: the game has the name and
    puts no ground under it (``Area_EasternDuneForest_1``)."""
    on_disk = {
        path.rsplit("/", 1)[-1].removesuffix(".uasset")
        for path in store.by_path
        if path.startswith(MAP_AREA_DIR) and "/Area_" in path
    }
    return on_disk - {a.asset for a in areas.areas if a is not None}


def build_meta(
    *,
    build_pin: str,
    exe_build: str | None,
    areas: MapAreas,
    calibration: dict,
    coarse_counts: dict[str, int],
    fine_counts: dict[str, int],
    mask_counts: dict[str, int],
    mask_points: int,
    name_map: dict[str, dict],
    unreferenced: list[str],
    fine_shape: tuple[int, int],
    versions: dict[str, str],
) -> dict:
    """The table's ``_meta``: provenance, the naming rules, the grids and the land mask."""
    return {
        "purpose": (
            "ADVISORY region names only. Layer 1 (grid cells, cones, radii, clustering) is "
            "exact and is what every calculation uses. A name from this file must never feed "
            "a computation."
        ),
        "generated": datetime.now(UTC).strftime("%Y-%m-%d"),
        "generator": "tools/gen_region_names.py",
        "game_version_pinned": build_pin,
        "game_build": exe_build,
        "units": "centimetres; north is -Y, east is +X, up is +Z",
        "accuracy_m": int(FINE_CELL / 100),
        "source": {
            "asset": "/Game/" + MAP_AREA_PATH.split("/FactoryGame/Content/")[1].rsplit(".", 1)[0],
            "class": MAP_AREA_CLASS,
            "derivation": (
                f"mAreaData, {areas.width}x{areas.width} palette indices at "
                f"{round((BOUNDS_M['x_max_m'] - BOUNDS_M['x_min_m']) / areas.width, 4)} m to "
                "the texel, row 0 north; mColorToArea resolves each index to one UFGMapArea "
                "asset by PublicExportHash, and that asset's mDisplayName states the game's "
                "own name for the place"
            ),
            "read_by": "core.gameassets.maparea, shared with tools/gen_map_renders.py",
            "frame_m": dict(BOUNDS_M),
            "shipped_palette_rgba": [list(entry) for entry in areas.palette],
            "shipped_palette_role": (
                "the game's own minimap legend -- flat primaries, cyan, magenta, white. "
                "Decoded for the record and never drawn or shipped; this file emits no "
                "colour at all."
            ),
            "pyooz_version": versions["pyooz"],
        },
        "licence": (
            "First-party. Every value here is derived from Coffee Stain's cooked assets, read "
            "out of the reader's own installed copy of the game: palette indices, area "
            "identifiers and localisation keys. The same posture as every other derived table "
            "in data/ -- facts and coordinates, no artwork. No copyleft and no share-alike "
            "obligation reaches this file; the CC BY-SA wiki trace it used to rasterise is "
            "retired, and data/satisfactory_regions.json is deleted."
        ),
        "calibration": calibration,
        "area_display_names": {asset: entry["display_name"] for asset, entry in name_map.items()},
        "name_map": name_map,
        "naming_rules": _naming_rules(unreferenced),
        "grids": _grids(fine_shape),
        "confidence_legend": CONFIDENCE_LEGEND,
        "cell_counts": {
            f"{GRID_CELL / 100:.0f}m": coarse_counts,
            f"{FINE_CELL / 100:.0f}m": fine_counts,
        },
        "void_distance_m": VOID_DISTANCE_M,
        "land_mask": {
            "role": (
                "positions only, and one decision only: whether a cell the GAME leaves "
                "unnamed is coast or open water. It supplies no name, and no calculation "
                "reads it."
            ),
            "sources": {
                "data/world_resource_nodes.json": (
                    "first-party: the game's own Persistent_Level.umap node actors, read "
                    "from the installed game by tools/gen_world_resource_nodes.py"
                ),
                "data/world_collectibles.json": (
                    "the game's own cooked map packages, read from the installed game"
                ),
            },
            "reference_points": mask_points,
            "categories": mask_counts,
        },
        "node_region_overrides": (
            "gone, key and all. It held 48 oil nodes whose region had been read off a wiki "
            "image by eye and was trusted over the raster; the raster is the game's own "
            "geometry now, so there is nothing for a hand correction to correct. Keying one "
            "by instance name would be the wrong repair in any case -- a map update renames "
            "instances, which is what the node skew gate exists for. A node is labelled by "
            "where it stands, like every other coordinate."
        ),
        "retired_wiki_trace": RETIRED_TRACE_COMPARISON,
        "known_limitations": KNOWN_LIMITATIONS,
    }


def _naming_rules(unreferenced: list[str]) -> dict:
    return {
        "rule": (
            "the display name is the game's own, taken from each Area_* asset's "
            "mDisplayName localisation key. Geometry is game-granular: where two assets "
            "carry one key they are one region, and where two assets with the same "
            "package name carry different keys they are two."
        ),
        "one_key_two_assets": (
            "Area_Savanna_1 and Area_Savanna_2 both state Locations/RockyDesert, so the "
            "game has a Savanna asset and no Savanna region. Same for the pairs behind "
            "Dune Desert, Grass Fields, Lake Forest, Maze Canyons, Northern Forest, "
            "Rocky Desert, Southern Forest, Swamp, Titan Forest and Western Dune Forest."
        ),
        "one_asset_name_two_keys": (
            "Area_crater_1 is Blue Crater and Area_crater_2 is Crater Lakes; "
            "Area_RedJungle_1 is Red Jungle and Area_RedJungle_2 is Jungle Spires. "
            "Resolving by package name would answer one of each pair at random."
        ),
        "no_mans_land": (
            "Area_NoMansLand is 43% of the raster and is the ocean and the outer coast. "
            "It is labelled No Man's Land -- the game's own display name for it -- rather "
            "than blanked, because a player standing on the outer coast is standing "
            "somewhere and the game has a name for it. A cell is void instead only when "
            f"the game names no region AND no known static object is within "
            f"{VOID_DISTANCE_M:.0f} m, which is ocean and off-map."
        ),
        "spellings": {
            key: {"key": key, "emitted": label, "why": "the plural every player uses"}
            for key, label in PLURALISED.items()
        },
        "names_with_no_ground": (
            "Area assets that exist beside the texture and that no palette index reaches. "
            "Eastern Dune Forest is a name the game has and puts nowhere."
        ),
        "unreferenced_area_assets": unreferenced,
    }


def _grids(fine_shape: tuple[int, int]) -> dict:
    fine_nx, fine_ny = fine_shape
    return {
        "published": (
            f"region_grid and confidence_grid are {GRID_NX}x{GRID_NY} at "
            f"{GRID_CELL / 100:.0f} m -- what /api/regions serves and what the map paints."
        ),
        "fine": (
            f"fine_grid and fine_confidence are {fine_nx}x{fine_ny} at "
            f"{FINE_CELL / 100:.0f} m and are what domain.spatial.regions looks names up "
            "in. Not served: the payload contract is the coarse pair and nothing else."
        ),
        "pairing": (
            "each confidence grid is indexed exactly like the grid it is named after. "
            "confidence_grid goes with region_grid at 256 m; fine_confidence goes with "
            "fine_grid at 64 m. They are different shapes and swapping them reads fine "
            "and answers wrong."
        ),
        "why_64_m": (
            "measured on 5,054 known static world objects, looked up in a majority "
            "downsample against the raster itself: 256 m mislabels 714 (14.13%), 128 m "
            "421 (8.33%), 64 m 268 (5.30%), 32 m 132 (2.61%). 64 m costs 14,400 "
            "characters; 32 m costs 57,600 for twice the accuracy and was refused."
        ),
        "not_exact": (
            "a majority downsample of an exact boundary is still a downsample. The "
            "boundaries in the source are exact; these two grids are not, and the "
            "confidence letter says which cells that bites in."
        ),
    }


if __name__ == "__main__":
    raise SystemExit(main())

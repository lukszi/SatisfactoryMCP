"""Generate data/world_resource_nodes.json from the installed game's own map package.

    uv run --extra gen python tools/gen_world_resource_nodes.py

Every resource-node actor the world level places -- plain nodes, well satellites, geysers
and fracking cores -- with its class, resource, purity, world position, and for a
satellite the core it draws from. This is the authoritative node SET the rest of the tree
projects from: ``tools/gen_resource_nodes.py`` cuts the served table out of it, the region
layer's land mask and the map sheet's calibration project its positions, and the
heightmap's validation gate reads its ``z``.

Only ``Persistent_Level.umap`` places these four classes in this build -- a fact about the
build, not about the format -- so every run sweeps the whole ``GameLevel01`` package list
and refuses to write if a placement turns up in a streamed cell. The emitted ``_meta``
carries the rest: how each field is read, the deposit exclusion, the licence, and the
record of the retired third-party table under ``retired_mit_table``.
"""

from __future__ import annotations

import collections
import json
import sys
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import NotRequired, TypedDict

ROOT = Path(__file__).resolve().parents[1]
for _path in (ROOT / "src", ROOT / "tools" / "mapgen" / "src"):
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))

from mapgen.common import base_parser, require_gen
from satisfactory_mcp.core.gameassets.container import CONTAINER, paks_dir
from satisfactory_mcp.core.gameassets.iostore import IoStore, oodle_decompress
from satisfactory_mcp.core.gameassets.levels import (
    LEVEL_SUFFIX,
    WORLD_LEVEL_DIR,
    level_paths,
    walk_levels,
)
from satisfactory_mcp.core.gameassets.packages import (
    AssetIndex,
    ClassFacts,
    PackageView,
    ScriptObjects,
    class_name_of,
    root_component,
    world_transform,
)
from satisfactory_mcp.core.gameassets.provenance import InstallNotFound, installed_build
from satisfactory_mcp.core.jsontypes import JsonObject

PERSISTENT_LEAF = "Persistent_Level.umap"

RESOURCE_NODE_CLASS = "BP_ResourceNode_C"
FRACKING_SATELLITE_CLASS = "BP_FrackingSatellite_C"
GEYSER_CLASS = "BP_ResourceNodeGeyser_C"
FRACKING_CORE_CLASS = "BP_FrackingCore_C"

#: The classes that become rows.
NODE_CLASSES = (RESOURCE_NODE_CLASS, FRACKING_SATELLITE_CLASS, GEYSER_CLASS, FRACKING_CORE_CLASS)

#: A deposit is counted every run but never emitted, so its exclusion stays a decision
#: rather than a blind spot.
DEPOSIT_CLASS = "BP_ResourceDeposit_C"
COUNTED_CLASSES = {*NODE_CLASSES, DEPOSIT_CLASS}

#: ``mPurity`` FName -> the vocabulary every consumer speaks. UE omits a property equal to
#: its class default and the default is ``RP_Normal``, so absence decodes to ``normal``
#: and ``RP_Normal`` itself never appears on a placed instance.
PURITY = {None: "normal", "RP_Normal": "normal", "RP_Inpure": "impure", "RP_Pure": "pure"}

#: Decimal places of a centimetre. The composed float32 transforms are not meaningful past
#: this, and a fixed rounding keeps regeneration diffs readable.
POSITION_DECIMALS = 4

#: One row of the table. The functional form, because "class" is a keyword; ``core`` is on
#: the satellites only.
WorldNode = TypedDict(
    "WorldNode",
    {
        "id": str,
        "class": str,
        "resource": str | None,
        "purity": str,
        "x": float,
        "y": float,
        "z": float,
        "core": NotRequired[str],
    },
)

#: The retirement record for ``data/world_resource_nodes.mit.json``, deleted in the same
#: commit that first generated this file. Transcribed, not recomputed -- the file it was
#: measured against is gone -- so no run gates on it. ``tests/data/test_nodes_provenance.py``
#: pins these figures.
RETIRED_MIT_TABLE: JsonObject = {
    "what": (
        "data/world_resource_nodes.mit.json: 626 resource-node rows vendored from "
        "rockfactory/satisfactory-logistics (MIT, Copyright (c) 2024 Leonardo Ascione), "
        "itself an FModel dump of this same Persistent_Level.umap, cut from a 2024 build. "
        "Deleted in the same commit as this file's first generation; every consumer reads "
        "this first-party extraction instead, so no third-party data and no attribution "
        "obligation remains."
    ),
    "parity_measured": "2026-07-30, this extraction against the MIT rows, on the build below",
    "rows": {"mit": 626, "this_extraction_plus_the_deposit": 626, "shared_ids": 625},
    "composition": (
        "identical on both sides: 459 BP_ResourceNode_C, 118 BP_FrackingSatellite_C, "
        "31 BP_ResourceNodeGeyser_C, 17 BP_FrackingCore_C, and the persistent level's one "
        "BP_ResourceDeposit_C (a row there, out of scope here)"
    ),
    "purity": "equal on all 625 shared ids",
    "resource": (
        "equal on all 594 comparable ids; the 31 geysers are not comparable because the "
        "asset carries no mResourceClass and the MIT rows labelled them with the synthetic "
        "Desc_GeothermalEnergy_C"
    ),
    "positions": {
        "within_the_mit_files_whole_centimetre_rounding": 600,
        "max_delta_inside_that_floor_cm": 0.83,
        "moved_since_the_mit_extraction": (
            "25 rows, 9.54-80.38 cm apart, every one vertical -- the horizontal component "
            "is at most 0.64 cm, i.e. rounding -- because the game moved these nodes in a "
            "map update after the MIT set was cut. The same 25 rows and the same 80.38 cm "
            "maximum had already been measured against saveVersion 60 save actors while "
            "the MIT table was current, so the disagreement is dated game movement, and "
            "this extraction is authoritative."
        ),
        "moved_rows_dz_cm": {
            "BP_ResourceNode143_1543": -80.38,
            "BP_ResourceNode124_5785": -50.31,
            "BP_ResourceNode137_2248": 40.25,
            "BP_ResourceNode586": 40.14,
            "BP_ResourceNode566": -40.0,
            "BP_ResourceNode553": 30.43,
            "BP_ResourceNode556": 30.32,
            "BP_ResourceNode40": -30.25,
            "BP_ResourceNode469": -30.23,
            "BP_ResourceNode12_91": -30.11,
            "BP_ResourceNode466": 30.06,
            "BP_ResourceNode229": 30.02,
            "BP_ResourceNode545": 29.88,
            "BP_ResourceNode550": 29.82,
            "BP_ResourceNode464_UAID_40B076DF2F7914E201_2026233335": 29.68,
            "BP_ResourceNode486": 29.52,
            "BP_ResourceNode53_510": -20.32,
            "BP_ResourceNode442": 20.14,
            "BP_ResourceNode85": 20.07,
            "BP_ResourceNode144_1644": 19.98,
            "BP_ResourceNode620": -19.57,
            "BP_ResourceNode573_UAID_40B076DF2F7983E001_1840982787": -19.54,
            "BP_ResourceNode464_UAID_40B076DF2F790EE201_1850696287": -10.12,
            "BP_ResourceNode441": 10.03,
            "BP_ResourceNode554": 9.54,
        },
    },
    "renamed": {
        "mit": "BP_ResourceNode11",
        "now": "BP_ResourceNode20_UAID_04D9F5D42711A7C902_1245462149",
        "apart_cm": 150.12,
        "what": (
            "a pure Limestone node the game renamed and moved between the MIT set's build "
            "and this one -- the same rename every saveVersion 52 vs 60 save pair on this "
            "machine shows"
        ),
    },
}


@dataclass
class WorldSweep:
    """One walk over every world package: the census, and the persistent level it found."""

    #: Package leaf -> counts, for the packages placing any counted class.
    per_package: dict[str, collections.Counter[str]]
    #: Counted class -> how many the whole world places.
    census: dict[str, int]
    persistent_view: PackageView
    persistent_path: str
    packages_swept: int

    def deposits(self) -> tuple[int, int]:
        """Deposits placed by the persistent level and by the streamed cells; none is emitted."""
        persistent = self.per_package.get(PERSISTENT_LEAF, collections.Counter[str]())
        in_persistent = persistent.get(DEPOSIT_CLASS, 0)
        return in_persistent, self.census.get(DEPOSIT_CLASS, 0) - in_persistent


def sweep_world_levels(store: IoStore, scripts: ScriptObjects) -> WorldSweep:
    """Count the counted classes over every world package; hand back the persistent level.

    The view comes out of the same walk that proves it is the only package placing a node,
    so the proof and the read cannot diverge. Dies if any package is unreadable or a node
    turns up in a streamed cell.
    """
    per_package: dict[str, collections.Counter[str]] = {}
    census: collections.Counter[str] = collections.Counter()
    persistent: PackageView | None = None
    persistent_path: str | None = None
    failures: collections.Counter[str] = collections.Counter()

    def unreadable(_path: str, exc: Exception) -> None:
        failures[type(exc).__name__] += 1

    paths = level_paths(store, contains=WORLD_LEVEL_DIR)
    started = time.time()
    for number, total, path, view in walk_levels(
        store, scripts, paths=paths, on_unreadable=unreadable
    ):
        here: collections.Counter[str] = collections.Counter()
        for export in view.exports:
            slot = export["slot"]
            if view.outer_of.get(slot) not in view.level_slots:
                continue
            cls = class_name_of(view.class_of.get(slot))
            if cls in COUNTED_CLASSES:
                here[cls] += 1
        if here:
            per_package[path.rsplit("/", 1)[-1]] = here
            census.update(here)
        if path.endswith("/" + PERSISTENT_LEAF):
            persistent = view
            persistent_path = path
        if number and number % 1000 == 0:
            print(f"  {number:>5}/{total} packages  {time.time() - started:>5.1f}s", flush=True)
    if failures:
        raise SystemExit(f"unreadable world packages, so the census is not a proof: {failures}")
    if persistent is None or persistent_path is None:
        raise SystemExit(f"no {PERSISTENT_LEAF} under {WORLD_LEVEL_DIR} -- container layout moved")

    outside = {
        leaf: {cls: n for cls, n in here.items() if cls in NODE_CLASSES}
        for leaf, here in per_package.items()
        if leaf != PERSISTENT_LEAF and any(cls in NODE_CLASSES for cls in here)
    }
    if outside:
        raise SystemExit(
            "emitted classes are placed outside the persistent level, so a one-package "
            f"read is incomplete from this build on: {outside}"
        )
    return WorldSweep(
        per_package=per_package,
        census=dict(census),
        persistent_view=persistent,
        persistent_path=persistent_path,
        packages_swept=len(paths),
    )


def sweep_other_levels(store: IoStore, scripts: ScriptObjects) -> list[JsonObject]:
    """The container's non-world levels, swept with the identical rule.

    The developer test map places resource nodes too, so a count from outside the world
    level is recorded rather than treated as a contradiction.
    """
    out: list[JsonObject] = []
    for path in sorted(
        p for p in store.by_path if p.endswith(LEVEL_SUFFIX) and WORLD_LEVEL_DIR not in p
    ):
        entry: JsonObject = {"package": path.rsplit("/", 1)[-1]}
        try:
            view = PackageView(store.read_path(path), scripts)
            counts = collections.Counter(
                class_name_of(view.class_of.get(export["slot"]))
                for export in view.exports
                if view.outer_of.get(export["slot"]) in view.level_slots
            )
        except Exception as exc:
            entry["read"] = f"failed: {type(exc).__name__}"
            out.append(entry)
            continue
        entry["actors"] = sum(counts.values())
        entry["of_a_counted_class"] = {
            cls: n for cls, n in sorted(counts.items()) if cls in COUNTED_CLASSES
        }
        out.append(entry)
    return out


def read_rows(view: PackageView, classes: ClassFacts) -> list[WorldNode]:
    """Every node actor of the persistent level as a finished row, or die saying why."""
    problems: list[str] = []
    actors = [
        (export, class_name_of(view.class_of.get(export["slot"])))
        for export in view.exports
        if view.outer_of.get(export["slot"]) in view.level_slots
    ]
    core_names = {
        export["slot"]: export["name"] for export, cls in actors if cls == FRACKING_CORE_CLASS
    }
    rows: list[WorldNode] = []
    for export, cls in actors:
        if cls not in NODE_CLASSES:
            continue
        row = _read_node_row(view, classes, export, cls, core_names, problems)
        if row is not None:
            rows.append(row)
    problems.extend(_check_well_links(rows))
    if problems:
        for p in problems:
            print("  PROBLEM:", p)
        raise SystemExit(f"{len(problems)} problem(s); refusing to write a partial table")
    rows.sort(key=lambda r: r["id"])
    return rows


def _read_node_row(
    view: PackageView,
    classes: ClassFacts,
    export: dict,
    cls: str,
    core_names: dict[int, str],
    problems: list[str],
) -> WorldNode | None:
    """One node's row, appending what is wrong with it to ``problems``.

    None where the row cannot be written at all: an unknown purity or no transform.
    """
    slot = export["slot"]
    name = export["name"]
    props = view.props(slot)

    raw = props.get("mPurity")
    purity_name = view.read_fname(raw) if raw is not None else None
    if purity_name not in PURITY:
        problems.append(f"{name}: unknown mPurity value {purity_name!r}")
        return None

    resource_path = view.import_path(props["mResourceClass"]) if "mResourceClass" in props else None
    resource = class_name_of(resource_path) if resource_path else None
    if cls == GEYSER_CLASS:
        if resource is not None:
            problems.append(f"{name}: a geyser carrying mResourceClass ({resource}) is new")
    elif resource is None:
        problems.append(f"{name}: no readable mResourceClass")

    core = None
    if cls == FRACKING_SATELLITE_CLASS:
        ref = view.export_ref(props.get("mCore", b""))
        core = core_names.get(ref) if ref is not None else None
        if core is None:
            problems.append(f"{name}: mCore resolves to no {FRACKING_CORE_CLASS} export")

    root = root_component(view, slot)
    transform = world_transform(view, root, classes)[0] if root is not None else None
    if transform is None:
        problems.append(f"{name}: no composable root-component transform")
        return None

    row: WorldNode = {
        "id": name,
        "class": cls,
        "resource": resource,
        "purity": PURITY[purity_name],
        "x": round(transform[0][0], POSITION_DECIMALS),
        "y": round(transform[0][1], POSITION_DECIMALS),
        "z": round(transform[0][2], POSITION_DECIMALS),
    }
    if core is not None:
        row["core"] = core
    return row


def _check_well_links(rows: list[WorldNode]) -> list[str]:
    """A core and its satellites tap one deposit, so a resource disagreement across the link
    is a misread rather than a curiosity; so is a core no satellite references."""
    problems: list[str] = []
    resource_of_core = {
        row["id"]: row["resource"] for row in rows if row["class"] == FRACKING_CORE_CLASS
    }
    referenced = {row["core"] for row in rows if "core" in row}
    for row in rows:
        if row["class"] == FRACKING_CORE_CLASS and row["id"] not in referenced:
            problems.append(f"{row['id']}: a fracking core no satellite references")
        if "core" in row and row["resource"] != resource_of_core.get(row["core"]):
            problems.append(
                f"{row['id']}: satellite says {row['resource']}, its core "
                f"{row['core']} says {resource_of_core.get(row['core'])}"
            )
    return problems


def main() -> int:
    parser = base_parser("Generate data/world_resource_nodes.json from the installed game.")
    parser.add_argument(
        "--out",
        type=Path,
        default=ROOT / "data" / "world_resource_nodes.json",
        help="where to write the table",
    )
    args = parser.parse_args()
    versions = require_gen("ooz")

    try:
        build_pin, _raw = installed_build(args.game)
    except InstallNotFound as exc:
        print(f"{exc}\nPass --game if the install is somewhere else.")
        return 1
    paks = paks_dir(args.game)
    if not (paks / f"{CONTAINER}.utoc").exists():
        print(f"no {CONTAINER}.utoc under {paks}")
        return 1
    print(f"installed build: {build_pin}")
    print(f"reading the game's own assets from {paks} with pyooz {versions['pyooz']}")

    store = IoStore(paks, CONTAINER, oodle_decompress)
    scripts = ScriptObjects(paks, oodle_decompress)
    classes = ClassFacts(store, AssetIndex(store))

    world = sweep_world_levels(store, scripts)
    rows = read_rows(world.persistent_view, classes)
    meta = build_meta(
        rows=rows,
        world=world,
        side_levels=sweep_other_levels(store, scripts),
        build_pin=build_pin,
        versions=versions,
    )
    args.out.write_text(json.dumps({"_meta": meta, "nodes": rows}, indent=1) + "\n", "utf-8")
    print(f"wrote {_shown(args.out)}  {len(rows)} rows  {args.out.stat().st_size} B")
    print("by class:", meta["by_class"])
    print("by purity:", meta["by_purity"])
    in_persistent, in_streamed = world.deposits()
    print(
        f"deposits: {in_persistent} in the persistent level, {in_streamed} in the "
        "streamed cells, 0 emitted"
    )
    return 0


def _shown(path: Path) -> Path:
    """``path`` relative to the repository when it is inside it, as given otherwise."""
    return path.relative_to(ROOT) if path.is_relative_to(ROOT) else path


def build_meta(
    *,
    rows: list[WorldNode],
    world: WorldSweep,
    side_levels: list[JsonObject],
    build_pin: str,
    versions: dict[str, str],
) -> JsonObject:
    """The table's ``_meta``: provenance, counts, coverage, and the retired table's record."""
    by_class = collections.Counter(r["class"] for r in rows)
    by_purity = collections.Counter(r["purity"] for r in rows)
    satellites = [r for r in rows if r["class"] == FRACKING_SATELLITE_CLASS]
    cores = {r["id"] for r in rows if r["class"] == FRACKING_CORE_CLASS}
    deposits_in_persistent, deposits_streamed = world.deposits()
    return {
        "description": (
            "Every resource-node actor the game's world level places: class, resource, "
            "purity, world position, and the satellite -> fracking-core link. The "
            "authoritative first-party node set that data/resource_nodes.json, the "
            "region layer's land mask, the map sheet's calibration and the heightmap's "
            "validation gate all project from."
        ),
        "licence": (
            "First-party. Identifiers, classes, purities and coordinates are facts "
            "about Coffee Stain's map, read from the reader's own installed copy of "
            "the game by tools/gen_world_resource_nodes.py. No third-party table "
            "contributed to this file, in any form; no external licence and no "
            "attribution obligation attaches. The MIT-licensed table this replaced is "
            "retired and deleted -- see retired_mit_table."
        ),
        "source": {
            "container": "FactoryGame-Windows.utoc/.ucas",
            "package": world.persistent_path.lstrip("./"),
            "read_by": "satisfactory_mcp.core.gameassets (iostore, packages, levels)",
            "method": (
                "exports whose Outer is the package's /Script/Engine.Level export; "
                "resource from the mResourceClass ObjectProperty via the import map; "
                "purity from the mPurity ByteProperty FName (absent means the class "
                "default, normal); the well link from the satellite's mCore "
                "ObjectProperty, an export reference in the same package; positions "
                "from the composed root-component world transform"
            ),
            "decoder": dict(versions),
        },
        "game_version_pinned": build_pin,
        "generated": datetime.now(UTC).date().isoformat(),
        "count": len(rows),
        "by_class": dict(sorted(by_class.items())),
        "by_purity": dict(sorted(by_purity.items())),
        "coverage": {
            "world_level": WORLD_LEVEL_DIR,
            "packages_swept": world.packages_swept,
            "emitted_classes_outside_the_persistent_level": 0,
            "note": (
                "all four emitted classes are placed exclusively by the persistent "
                "level; the streamed cell packages place none -- measured this run "
                "over every world package, and the run refuses to write when that "
                "stops holding, so the one-package read cannot go quietly incomplete"
            ),
            "other_levels_in_the_container": list(side_levels),
        },
        "deposits": {
            "why_no_rows": (
                "BP_ResourceDeposit_C is hand-mineable only -- no extractor can be "
                "placed on one -- so a deposit row would advertise capacity that "
                "cannot be built"
            ),
            "placed_by_the_persistent_level": deposits_in_persistent,
            "placed_by_the_streamed_cells": deposits_streamed,
        },
        "geysers": (
            "carry no mResourceClass -- a geyser is a placement target for the "
            "Geothermal Generator, not an item -- so resource is null here and "
            "consumers label it synthetically"
        ),
        "well_links": {
            "satellites": len(satellites),
            "cores": len(cores),
            "statement": (
                "total on both sides and resource-consistent: every satellite carries "
                "mCore, every core is referenced, and every satellite names its "
                "core's resource -- re-checked every run, the run dies otherwise"
            ),
        },
        "not_carried": (
            "the retired MIT rows also carried a yaw rotation and a display name; no "
            "consumer ever read either, so neither is a field here"
        ),
        "join_key": (
            "id; a save actor's instanceName is 'Persistent_Level:PersistentLevel.' "
            "plus this, byte for byte"
        ),
        "units": "centimetres; north is -Y, east is +X, up is +Z",
        "retired_mit_table": RETIRED_MIT_TABLE,
    }


if __name__ == "__main__":
    raise SystemExit(main())

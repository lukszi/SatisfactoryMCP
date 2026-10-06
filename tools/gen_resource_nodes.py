"""Generate data/resource_nodes.json from the first-party world node table.

    uv run python tools/gen_resource_nodes.py

Node resource type and purity are NOT serialized in save files -- node actors carry only
``mResourcesLeft`` and a transform -- so this static table is the only source. It is a
projection of ``data/world_resource_nodes.json``: filter the classes, rename the fields,
attach the satellite -> fracking-core link the game ships as ``mCore``. ``main``
re-measures both the grouping and the projection on every run and dies rather than
writing, so the two committed artifacts cannot drift apart.
"""

from __future__ import annotations

import json
import math
from pathlib import Path
from typing import TYPE_CHECKING, Literal, NotRequired, TypedDict, cast

if TYPE_CHECKING:
    from satisfactory_mcp.core.jsontypes import JsonObject

ROOT = Path(__file__).resolve().parents[1]

#: Save actors are keyed by this prefix; the world table stores bare ids.
INSTANCE_PREFIX = "Persistent_Level:PersistentLevel."

FRACKING_SATELLITE_CLASS = "BP_FrackingSatellite_C"
FRACKING_CORE_CLASS = "BP_FrackingCore_C"

NodeKind = Literal["node", "well_sat", "geyser"]

# The functional form, because "class" is a keyword. ``core`` is on the satellites only.
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


class WorldTableMeta(TypedDict, total=False):
    """The fields of the world table's ``_meta`` that this table carries forward."""

    generated: str
    game_version_pinned: str


class ResourceNodeRow(TypedDict):
    """One row of ``data/resource_nodes.json``."""

    instance: str
    resource: str | None
    purity: str
    kind: NodeKind
    x: float
    y: float
    z: float
    well_core: str | None


#: A well satellite yields half a plain node's rate AND needs a Pressurizer on its parent
#: core, so conflating the two with a plain node overstates a field by 2x.
_KINDS: dict[str, NodeKind] = {
    "BP_ResourceNode_C": "node",
    FRACKING_SATELLITE_CLASS: "well_sat",
    "BP_ResourceNodeGeyser_C": "geyser",
}

#: A fracking core produces nothing itself and reaches the table as ``well_core`` on its
#: satellites.
_EXCLUDED = {FRACKING_CORE_CLASS}

#: Synthetic: a geyser is not an item in Docs.json, it is a placement target for the
#: Geothermal Generator.
_GEYSER_RESOURCE = "Desc_Geyser_C"

_PURITIES = {"impure", "normal", "pure"}

#: This file emits whole-hundredth centimetres, so a comparison against the source's
#: finer floats can never read below half a hundredth per axis. Any delta at or under
#: this is that rounding and nothing else.
ROUNDING_FLOOR_CM = math.sqrt(3) / 2 * 0.01


def load_world_node_table() -> tuple[list[WorldNode], WorldTableMeta]:
    """The rows and ``_meta`` of ``data/world_resource_nodes.json``, or exit naming its generator."""
    path = ROOT / "data" / "world_resource_nodes.json"
    if not path.is_file():
        raise SystemExit(
            f"{path} missing -- run: uv run --extra gen python tools/gen_world_resource_nodes.py"
        )
    payload: JsonObject = json.loads(path.read_text(encoding="utf-8"))
    nodes, meta = payload["nodes"], payload["_meta"]
    if not isinstance(nodes, list) or not isinstance(meta, dict):
        raise SystemExit(f"{path} holds no node list and _meta object -- regenerate it")
    return cast("list[WorldNode]", nodes), cast(WorldTableMeta, meta)


def project_nodes(world: list[WorldNode]) -> tuple[list[ResourceNodeRow], int]:
    """The served rows, in world-table order, and how many fracking cores the table holds."""
    nodes: list[ResourceNodeRow] = []
    core_ids: set[str] = set()
    unknown_purity: list[str] = []
    for entry in world:
        if entry["class"] == FRACKING_CORE_CLASS:
            core_ids.add(entry["id"])
        kind = _KINDS.get(entry["class"])
        if kind is None:
            if entry["class"] not in _EXCLUDED:
                raise SystemExit(f"{entry['id']}: unexpected class {entry['class']}")
            continue

        resource = _GEYSER_RESOURCE if kind == "geyser" else entry["resource"]
        purity = entry["purity"]
        if purity not in _PURITIES:
            unknown_purity.append(f"{entry['id']}: {purity!r}")

        nodes.append(
            {
                "instance": INSTANCE_PREFIX + entry["id"],
                "resource": resource,
                "purity": purity,
                "kind": kind,
                "x": round(float(entry["x"]), 2),
                "y": round(float(entry["y"]), 2),
                "z": round(float(entry["z"]), 2),
                "well_core": INSTANCE_PREFIX + entry["core"]
                if kind == "well_sat" and "core" in entry
                else None,
            }
        )

    if unknown_purity:
        for u in unknown_purity:
            print("  UNKNOWN PURITY:", u)
        raise SystemExit(f"{len(unknown_purity)} row(s) carry a purity this table cannot speak")
    return nodes, len(core_ids)


def check_well_links_by_position(world: list[WorldNode]) -> JsonObject:
    """Re-derive the well grouping from positions and compare with the ``mCore`` link.

    Returns the distance distribution and the ambiguity margin: satellites sit in a tight
    ring around their core, median 34 m, while the next-nearest core is at least 3.8x
    further, so the nearest core is the recorded one or something moved.
    """
    pos = {e["id"]: (e["x"], e["y"], e["z"]) for e in world}
    cores = [e["id"] for e in world if e["class"] == FRACKING_CORE_CLASS]
    sat_core = {e["id"]: e.get("core", "") for e in world if e["class"] == FRACKING_SATELLITE_CLASS}
    missing = sorted(s for s, c in sat_core.items() if not c)
    if missing:
        raise SystemExit(f"{len(missing)} satellite(s) carry no core link: {missing}")
    disagree: list[str] = []
    own: list[float] = []
    margins: list[float] = []

    for sat, core in sat_core.items():
        ranked = sorted(cores, key=lambda c: math.dist(pos[sat], pos[c]))
        d_own = math.dist(pos[sat], pos[core])
        own.append(d_own)
        rival = ranked[1] if ranked[0] == core else ranked[0]
        margins.append(math.dist(pos[sat], pos[rival]) / d_own)
        if ranked[0] != core:
            disagree.append(f"{sat}: mCore says {core}, nearest is {ranked[0]}")

    if disagree:
        for d in disagree:
            print("  GEOMETRY DISAGREES:", d)
        raise SystemExit(f"{len(disagree)} satellite(s) contradict the shipped mCore link")

    own.sort()
    return {
        "method": "nearest fracking core by position, vs the mCore link the game ships",
        # Equal by construction -- any disagreement raised above.
        "satellites_checked": len(own),
        "nearest_core_agrees": len(own),
        "distance_to_own_core_cm": {
            "min": round(own[0], 1),
            "median": round(own[len(own) // 2], 1),
            "max": round(own[-1], 1),
        },
        "runner_up_core_distance_ratio_min": round(min(margins), 2),
    }


def check_projection(
    nodes: list[ResourceNodeRow], world: list[WorldNode], world_meta: WorldTableMeta
) -> JsonObject:
    """Every emitted row against the source file: the projection cannot drift silently.

    The returned block is shaped the way ``domain/spatial/nodes/`` reads a positions
    comparison, so renaming a key here silences that skew gate.
    """
    by_id = {e["id"]: e for e in world if e["class"] not in _EXCLUDED}
    emitted = {n["instance"].removeprefix(INSTANCE_PREFIX): n for n in nodes}
    only_here = sorted(set(emitted) - set(by_id))
    only_source = sorted(set(by_id) - set(emitted))
    if only_here or only_source:
        raise SystemExit(
            f"projection and source disagree on the row set: {only_here} vs {only_source}"
        )
    deltas: list[float] = []
    for name, row in emitted.items():
        src = by_id[name]
        if row["purity"] != src["purity"]:
            raise SystemExit(f"{name}: purity changed in projection")
        if row["resource"] != (src["resource"] or _GEYSER_RESOURCE):
            raise SystemExit(f"{name}: resource changed in projection")
        deltas.append(math.dist((row["x"], row["y"], row["z"]), (src["x"], src["y"], src["z"])))
    worst = max(deltas)
    if worst > ROUNDING_FLOOR_CM:
        raise SystemExit(f"projection moved a position by {worst} cm, past its own rounding")
    return {
        "measured": world_meta.get("generated"),
        "build": world_meta.get("game_version_pinned"),
        "method": (
            "every emitted row compared back to data/world_resource_nodes.json -- the "
            "installed build's own Persistent_Level.umap, read out of the IoStore "
            "container -- on identity, resource, purity and position, recomputed on "
            "every run of this generator"
        ),
        "rows_compared": len(deltas),
        "rows_only_in_this_table": [],
        "rows_only_in_the_installed_build": [],
        "max_position_delta_cm": round(worst, 4),
        "rounding_floor_cm": round(ROUNDING_FLOOR_CM, 4),
        "rows_past_the_rounding_floor": [],
    }


def main() -> int:
    world, world_meta = load_world_node_table()
    nodes, core_count = project_nodes(world)
    geometry = check_well_links_by_position(world)
    projection = check_projection(nodes, world, world_meta)

    nodes.sort(key=lambda n: n["instance"])
    by_kind: dict[str, int] = {}
    for n in nodes:
        by_kind[n["kind"]] = by_kind.get(n["kind"], 0) + 1

    meta = build_meta(
        nodes=nodes,
        by_kind=by_kind,
        core_count=core_count,
        geometry=geometry,
        projection=projection,
        world_meta=world_meta,
    )
    dest = ROOT / "data" / "resource_nodes.json"
    dest.write_text(json.dumps({"_meta": meta, "nodes": nodes}, indent=1), encoding="utf-8")
    print_summary(dest, nodes, by_kind, core_count, geometry, projection)
    return 0


def build_meta(
    *,
    nodes: list[ResourceNodeRow],
    by_kind: dict[str, int],
    core_count: int,
    geometry: JsonObject,
    projection: JsonObject,
    world_meta: WorldTableMeta,
) -> JsonObject:
    """The table's ``_meta``: its source, the cross-checks this run made, and the exclusions."""
    return {
        "description": "Static resource node table: type, purity, world position.",
        "why": (
            "Save files serialize only mResourcesLeft for node actors; resource "
            "type and purity are level data and must come from a static table."
        ),
        "sources": {
            "primary": {
                "name": "data/world_resource_nodes.json",
                "licence": (
                    "first-party; read from the installed game's own packaged map "
                    "data by tools/gen_world_resource_nodes.py -- no third-party "
                    "table, no external licence, no attribution obligation"
                ),
                "derivation": (
                    "node exports of the installed build's Persistent_Level.umap, "
                    "read out of the IoStore container: mResourceClass, mPurity, "
                    "mCore and the composed root-component transform"
                ),
                "role": (
                    "authoritative node set, resource, purity, position, and the "
                    "satellite -> fracking-core link"
                ),
                "game_version_pinned": world_meta.get("game_version_pinned"),
                "generated": world_meta.get("generated"),
            },
            "retired": (
                "the MIT-licensed node table this file used to merge from, and the "
                "GPL SCIM grouping before it, are both gone; the retirement record "
                "and the parity that justified it are _meta.retired_mit_table in "
                "data/world_resource_nodes.json"
            ),
        },
        "cross_validation": {
            "satellites": by_kind.get("well_sat", 0),
            "fracking_cores_referenced": core_count,
            "geometry": geometry,
            # A re-measure against a newer build adds a second block beside this one;
            # the skew gate speaks when any block's deltas pass its floor.
            "positions": {"against_the_installed_build": projection},
        },
        "excluded": {
            "BP_ResourceDeposit_C": (
                "hand-mineable only, no extractor can be placed; the world table "
                "emits no deposit rows in the first place"
            ),
            FRACKING_CORE_CLASS: "produces nothing itself; referenced as well_core on satellites",
        },
        "geyser_note": (
            "Desc_Geyser_C is a synthetic label. A geyser is not an item in "
            "Docs.json -- it is a placement target for the Geothermal Generator."
        ),
        "join_key": "instance (matches save actor instanceName exactly)",
        "count": len(nodes),
        "by_kind": dict(by_kind),
        "fracking_cores": core_count,
        "purity_multiplier": {"impure": 0.5, "normal": 1.0, "pure": 2.0},
        "units": "centimetres; north is -Y, east is +X, up is +Z",
    }


def print_summary(
    dest: Path,
    nodes: list[ResourceNodeRow],
    by_kind: dict[str, int],
    core_count: int,
    geometry: JsonObject,
    projection: JsonObject,
) -> None:
    print(f"wrote {dest.relative_to(ROOT)}  {len(nodes)} nodes  {dest.stat().st_size} B")
    print("by kind:", by_kind)
    d = geometry["distance_to_own_core_cm"]
    assert isinstance(d, dict)
    print(
        f"well links: {geometry['satellites_checked']} satellites over {core_count} cores, "
        f"nearest-core agrees {geometry['nearest_core_agrees']}/{geometry['satellites_checked']}, "
        f"distance {d['min']:.0f}-{d['max']:.0f} cm (median {d['median']:.0f}), "
        f"runner-up at least {geometry['runner_up_core_distance_ratio_min']:.2f}x further"
    )
    print(
        f"projection: {projection['rows_compared']} rows match the installed build "
        f"({projection['build']}), max delta {projection['max_position_delta_cm']} cm, "
        f"floor {projection['rounding_floor_cm']} cm"
    )

    oil = [n for n in nodes if n["resource"] == "Desc_LiquidOil_C"]
    pump = [n for n in oil if n["kind"] == "node"]
    print(
        f"crude oil: {len(oil)} total, {len(pump)} pumpable nodes, "
        f"{len(oil) - len(pump)} well satellites"
    )


if __name__ == "__main__":
    raise SystemExit(main())

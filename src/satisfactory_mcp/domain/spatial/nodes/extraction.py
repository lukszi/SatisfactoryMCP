"""What a node yields and who taps it: rates, occupancy, reachability and capacity."""

from __future__ import annotations

from ....core.gamedata.model import GameData
from .. import geo
from . import skew as node_skew
from . import table as node_table
from .skew import _short
from .table import EXTRA_FOR_KIND, EXTRACTOR_FOR_KIND, GEYSER_CONSUMER

__all__ = [
    "annotate",
    "blocking_buildings",
    "capacity",
    "node_rate",
    "occupancy",
    "reachable",
    "unresolved_extractors",
]


def node_rate(node: dict, game: GameData, extractor_cls: str | None = None) -> float:
    """Extraction rate for one node at 100% clock, in items/min or m3/min.

    Uses the best extractor the player could place unless one is named. Geysers have
    no extractor -- they are consumed by a Geothermal Generator instead.
    """
    kind = node["kind"]
    if kind == "geyser" or game is None:
        return 0.0
    candidates = (extractor_cls,) if extractor_cls else EXTRACTOR_FOR_KIND.get(kind, ())
    best = 0.0
    for cls in candidates:
        b = game.buildings.get(cls)
        if b is None or not b.base_extract_rate:
            continue
        # FORM first, and never mAllowedResources alone: that field is only populated when
        # mOnlyAllowCertainResources is True, which is False on every miner, so filtering on
        # it leaves miners unrestricted and a Miner Mk.3 (base 240) out-bids the Oil
        # Extractor (base 120) on crude -- every oil node at double its real rate.
        # mAllowedResourceForms is what encodes it: RF_SOLID on miners, RF_LIQUID on pumps.
        item = game.items.get(node["resource"])
        if b.allowed_forms and item is not None and item.form not in b.allowed_forms:
            continue
        if b.allowed_resources and node["resource"] not in b.allowed_resources:
            continue
        best = max(best, b.extract_rate(node["purity"]))
    return best


def occupancy(projection: dict) -> dict[str, dict]:
    """node instanceName -> the extractor sitting on it.

    Resolution is PARTIAL: water pumps point at FGWaterVolume objects, which are not node
    keys, and some miners carry no mExtractableResource property at all -- only 40 of the
    reference save's 66 extractors resolve. An absent link is UNKNOWN, never free.
    """
    out: dict[str, dict] = {}
    for e in projection.get("extractors", ()):
        node = e.get("node")
        if not node:
            continue
        out[node] = {
            "extractor": e["cls"],
            "instance": e.get("instance"),
            "clock": e.get("clock", 1.0),
            "paused": e.get("paused", False),
            "pos": e.get("pos"),
        }
    return out


def unresolved_extractors(projection: dict) -> list[dict]:
    """Extractors whose node could not be resolved, so capacity is uncertain."""
    table = node_table.load_nodes().by_instance()
    # A node the newer build renamed lands here too, and "target not a node" is the wrong
    # diagnosis: the target IS a node, it is this table that is behind.
    skew = node_skew.skew_for_save(projection.get("header"))
    was = {_short(new): old for old, new in (skew.renamed_to.items() if skew else ())}
    out = []
    for e in projection.get("extractors", ()):
        node = e.get("node")
        if node is None:
            out.append({**e, "reason": "no mExtractableResource property"})
        elif node not in table:
            leaf = _short(node)
            old = was.get(leaf)
            reason = (
                f"node renamed by a game update after this table was cut "
                f"(this table calls it {_short(old)})"
                if old
                else f"target not a node ({leaf})"
            )
            out.append({**e, "reason": reason})
    return out


def reachable(node: dict, unlocked_buildings: set[str] | None) -> bool:
    """Whether the player can actually exploit this node yet.

    Without it a node table counts resource-well satellites needing a Pressurizer the player
    has not unlocked, which overstates crude on the reference save by 1,080 of 5,040 m3/min.
    """
    if unlocked_buildings is None:
        return True
    kind = node["kind"]
    if kind == "geyser":
        return GEYSER_CONSUMER in unlocked_buildings
    if not set(EXTRA_FOR_KIND.get(kind, ())) <= unlocked_buildings:
        return False
    return any(cls in unlocked_buildings for cls in EXTRACTOR_FOR_KIND.get(kind, ()))


def _can_tap(building, resource: str, game: GameData) -> bool:
    """Whether this extractor could tap this resource, unlocks aside.

    Mirrors the rule build_scenario applies when it turns nodes into extractor
    columns, and must keep mirroring it: a building with no `mAllowedResourceForms`
    of its own is a solid miner, so a fluid node is not its to take.
    """
    if building is None or not building.base_extract_rate:
        return False
    if building.allowed_resources:
        return resource in building.allowed_resources
    item = game.items.get(resource)
    return item is not None and not item.is_fluid


def blocking_buildings(
    node: dict, game: GameData, unlocked_buildings: set[str] | None
) -> tuple[str, ...]:
    """Building classes that must be unlocked before this node yields anything.

    Empty when nothing is in the way. Sharper than `reachable`, which asks only whether an
    extractor of the right KIND is unlocked: an unlocked Miner Mk2 makes a crude oil node
    read as reachable while nothing on the map can pump it.
    """
    if unlocked_buildings is None:
        return ()
    kind = node["kind"]
    if kind == "geyser":
        return () if GEYSER_CONSUMER in unlocked_buildings else (GEYSER_CONSUMER,)
    options = tuple(
        cls
        for cls in EXTRACTOR_FOR_KIND.get(kind, ())
        if _can_tap(game.buildings.get(cls), node["resource"], game)
    )
    missing = () if any(cls in unlocked_buildings for cls in options) else options
    extra = tuple(c for c in EXTRA_FOR_KIND.get(kind, ()) if c not in unlocked_buildings)
    return missing + extra


def annotate(
    nodes: list[dict],
    game: GameData,
    projection: dict | None = None,
    unlocked_buildings: set[str] | None = None,
) -> list[dict]:
    """Attach rate, grid cell, occupancy and reachability to node rows.

    The whole occupancy record travels, not a boolean: which extractor stands there, at
    what clock, whether it is switched off and where it is are what "is this node worth
    reclaiming" is answered from, and ``occupancy`` computes all of it anyway.
    """
    occ = occupancy(projection) if projection else {}
    out = []
    for n in nodes:
        taken = occ.get(n["instance"]) or {}
        out.append(
            {
                **n,
                "rate": node_rate(n, game),
                "grid": geo.grid_cell(n["x"], n["y"]),
                "tapped": bool(taken),
                "tapped_by": taken.get("extractor"),
                "tapped_clock": taken.get("clock"),
                "tapped_paused": taken.get("paused"),
                "tapped_instance": taken.get("instance"),
                "tapped_pos": taken.get("pos"),
                "reachable": reachable(n, unlocked_buildings),
            }
        )
    return out


def capacity(rows: list[dict], only_free: bool = False, only_reachable: bool = True) -> float:
    """Total rate across rows.

    Defaults to reachable nodes only, because unreachable capacity is not a plan.
    """
    total = 0.0
    for r in rows:
        if only_free and r.get("tapped"):
            continue
        if only_reachable and not r.get("reachable", True):
            continue
        total += r["rate"]
    return total

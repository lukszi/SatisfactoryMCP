"""``/api/power/circuits``: the ``power_report`` ledger, world-wide and per wired circuit.

A circuit is a connected component of the save's power edges; each one's figures are the same
``PowerLedger`` run over only the records standing on it. The limits of that reading are in
docs/spatial-and-map.md §21; wire rules in docs/web-wire.md.

WARNING: the function name is the operation_id -- renaming it churns the committed schema.
"""

from __future__ import annotations

from collections import Counter
from typing import Any, TypedDict

from fastapi import APIRouter, Request

from ....domain.factories import identity as fidentity
from ....domain.factories.health import assess
from ....domain.power.report import PowerLedger, starved_cause
from ....domain.spatial import geo
from ....domain.world.state import WorldState
from ..serial import (
    Biomass,
    Region,
    bbox_m,
    cm_to_m,
    instance_leaf,
    point_m,
    region_json,
    regions_or_none,
    require_world,
)

__all__ = ["router"]

router = APIRouter(prefix="/api")

RECORD_LISTS = ("generators", "machines", "extractors")


class Ledger(TypedDict):
    """``biomass_mw`` and ``biomass_generators`` are the wired burners left out of every
    figure here under ``?biomass=exclude``; both are 0 under ``include``."""

    generation_mw: float
    starved_generation_mw: float
    draw_mw: float
    measured_draw_mw: float
    headroom_mw: float
    measured_headroom_mw: float
    utilisation: float
    monitored: int
    unmonitored: int
    paused: int
    biomass_mw: float
    biomass_generators: int


class GeneratorGroup(TypedDict):
    name: str
    count: int
    mw: float


class StarvedGenerator(TypedDict):
    """``cause`` is ``missing`` as one phrase: "out of Coal, Water" or "no fuel loaded"."""

    instance: str
    name: str
    mw: float
    missing: list[str]
    cause: str
    x_m: float | None
    y_m: float | None


class MachineRef(TypedDict):
    """``circuit`` is the ``index`` of the circuit it stands on, null when on none;
    ``factory`` the named factory it is an anchor of, null when none; ``region`` where it
    stands, null when unplaced or off the region map."""

    instance: str
    name: str
    circuit: int | None
    factory: str | None
    region: Region | None
    x_m: float | None
    y_m: float | None


class Unwired(TypedDict):
    """What stands on no wire, left out of every ledger above. ``consumers`` counts the
    paused ones too, as ``unwired`` lists them; ``paused`` says how many of them are."""

    consumers: int
    paused: int
    draw_mw: float
    generators: int
    generation_mw: float


class CircuitRow(TypedDict):
    """``bbox_m`` is null when no record on the circuit has a position. ``factories`` is
    every named factory on it, most machines first; ``unmodellable`` the generator classes
    on it that game data cannot rate, whose output the ledger leaves out."""

    index: int
    ledger: Ledger
    generators: list[GeneratorGroup]
    starved: list[StarvedGenerator]
    unmodellable: list[str]
    consumers: int
    poles: int
    factories: list[str]
    factory_count: int
    centroid_m: tuple[float, float] | None
    bbox_m: tuple[float, float, float, float] | None


class CircuitsResponse(TypedDict):
    """``world`` is the sum of ``circuits``; ``unwired`` lists machines on no wire and
    ``unwired_generators`` generators on none."""

    world: Ledger
    paused: int
    generators: list[GeneratorGroup]
    starved: list[StarvedGenerator]
    unmodellable: list[str]
    circuits: list[CircuitRow]
    off_grid: Unwired
    unwired: list[MachineRef]
    unwired_generators: list[MachineRef]
    no_generator: list[MachineRef]


def _ledger(report: dict) -> dict:
    return {
        "generation_mw": round(report["generation_mw"], 1),
        "starved_generation_mw": round(report["starved_generation_mw"], 1),
        "draw_mw": round(report["draw_mw"], 1),
        "measured_draw_mw": round(report["measured_draw_mw"], 1),
        "headroom_mw": report["headroom_mw"],
        "measured_headroom_mw": report["measured_headroom_mw"],
        "utilisation": round(report["utilisation"], 3),
        "monitored": report["monitored"],
        "unmonitored": report["unmonitored"],
        "paused": report["paused_consumers"],
        "biomass_mw": round(report["biomass_mw"], 1),
        "biomass_generators": report["biomass_generators"],
    }


def _position_m(placed: dict, leaf: str) -> tuple[float | None, float | None]:
    pos = placed.get(leaf)
    return (cm_to_m(pos[0]), cm_to_m(pos[1])) if pos else (None, None)


def _groups(report: dict) -> list[dict]:
    return [
        {"name": g["name"], "count": g["count"], "mw": round(g["mw"], 1)}
        for g in sorted(report["by_generator"].values(), key=lambda g: -g["mw"])
    ]


def _starved(report: dict, placed: dict) -> list[dict]:
    rows = []
    for starved in report["starved_generators"]:
        x, y = _position_m(placed, starved["instance"])
        rows.append(
            {
                "instance": starved["instance"],
                "name": starved["name"],
                "mw": round(starved["mw"], 1),
                "missing": list(starved["missing"]),
                "cause": starved_cause(starved["missing"]),
                "x_m": x,
                "y_m": y,
            }
        )
    return rows


def _circuit_rows(
    st: WorldState, placed: dict, label_of: dict[str, str], counted: bool
) -> list[tuple[dict, set[str]]]:
    """Every circuit with a record on it, biggest ledger first, each with its member leaves."""
    graph = st.graph
    records = {
        key: {instance_leaf(r["instance"]): r for r in st.projection.get(key) or ()}
        for key in RECORD_LISTS
    }
    pairs = []
    for component in graph.components("power"):
        members = set(component)
        sub = {
            key: [r for leaf, r in records[key].items() if leaf in members] for key in RECORD_LISTS
        }
        if not any(sub.values()):
            continue
        report = PowerLedger(projection=sub, game=st.game).power_report(biomass=counted)
        standing = [leaf for leaf in component if leaf in placed]
        points = [placed[leaf][:2] for leaf in standing]
        centre = geo.centroid(points)
        named = Counter(label_of[leaf] for leaf in standing if leaf in label_of)
        row = {
            "index": 0,
            "ledger": _ledger(report),
            "generators": _groups(report),
            "starved": _starved(report, placed),
            "unmodellable": list(report["unmodellable"]),
            "consumers": len(sub["machines"]) + len(sub["extractors"]),
            "poles": sum(1 for leaf in component if graph.kind(leaf) in ("pole", "tower")),
            "factories": [name for name, _count in named.most_common()],
            "factory_count": len(named),
            "centroid_m": None if centre is None else point_m(centre),
            "bbox_m": bbox_m(placed, standing),
        }
        pairs.append((row, members))
    pairs.sort(
        key=lambda pair: -(pair[0]["ledger"]["generation_mw"] + pair[0]["ledger"]["draw_mw"])
    )
    for index, (row, _members) in enumerate(pairs):
        row["index"] = index
    return pairs


def _circuit_index(pairs: list[tuple[dict, set[str]]]) -> dict[str, int]:
    """Member leaf to the ``index`` of the circuit it stands on."""
    return {leaf: row["index"] for row, members in pairs for leaf in members}


def _machine_refs(
    st: WorldState,
    leaves: list[str],
    placed: dict,
    circuit_of: dict[str, int],
    label_of: dict[str, str],
    region_map,
) -> list[dict]:
    """Machines by leaf, sorted, with the circuit, factory and region each stands in."""
    refs = []
    for leaf in sorted(leaves):
        x, y = _position_m(placed, leaf)
        cls = st.graph.cls.get(leaf, "")
        pos = placed.get(leaf)
        refs.append(
            {
                "instance": leaf,
                "name": st.game.building_name(cls) or cls,
                "circuit": circuit_of.get(leaf),
                "factory": label_of.get(leaf),
                "region": (
                    None
                    if region_map is None or pos is None
                    else region_json(region_map.label_for(pos[0], pos[1]))
                ),
                "x_m": x,
                "y_m": y,
            }
        )
    return refs


@router.get("/power/circuits", response_model=CircuitsResponse)
def power_circuits(
    request: Request,
    save: str | None = None,
    world: str | None = None,
    biomass: Biomass = "exclude",
) -> Any:
    """Generation against draw, nameplate and measured, for the world and for each circuit.

    ``unwired`` and ``no_generator`` are ``assess``'s two lists over every machine in the
    world: no power edge at all, and a wire to a circuit no generator stands on. The world
    ledger counts only what stands on a wire, so it is the sum of the circuits.
    """
    st = require_world(request, save, world)

    counted = biomass == "include"
    placed = fidentity.positions(st.projection)
    label_of = {anchor: label.name for label in st.labels.labels for anchor in label.anchors}
    pairs = _circuit_rows(st, placed, label_of, counted)
    circuit_of = _circuit_index(pairs)

    wiring = assess("world", st.graph.machines(), st.game, st.projection, st.graph)
    region_map = regions_or_none()
    world_report = st.power_report(biomass=counted)
    return {
        "world": _ledger(world_report),
        "paused": world_report["paused_count"],
        "generators": _groups(world_report),
        "starved": _starved(world_report, placed),
        "unmodellable": list(world_report["unmodellable"]),
        "circuits": [row for row, _members in pairs],
        "off_grid": {
            "consumers": world_report["unwired_consumers"],
            "paused": world_report["unwired_paused"],
            "draw_mw": round(world_report["unwired_draw_mw"], 1),
            "generators": world_report["unwired_generators"],
            "generation_mw": round(world_report["unwired_generation_mw"], 1),
        },
        "unwired": _machine_refs(st, wiring.unwired, placed, circuit_of, label_of, region_map),
        "unwired_generators": _machine_refs(
            st, wiring.unwired_generators, placed, circuit_of, label_of, region_map
        ),
        "no_generator": _machine_refs(
            st, wiring.no_generator, placed, circuit_of, label_of, region_map
        ),
    }

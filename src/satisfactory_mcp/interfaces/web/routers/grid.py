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
from ....domain.spatial import regions as spatial_regions
from ..serial import Biomass, Region, _fail, _label_json, _m, _state

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


def _at(placed: dict, short: str) -> tuple[float | None, float | None]:
    pos = placed.get(short)
    return (_m(pos[0]), _m(pos[1])) if pos else (None, None)


def _groups(report: dict) -> list[dict]:
    return [
        {"name": g["name"], "count": g["count"], "mw": round(g["mw"], 1)}
        for g in sorted(report["by_generator"].values(), key=lambda g: -g["mw"])
    ]


def _starved(report: dict, placed: dict) -> list[dict]:
    out = []
    for s in report["starved_generators"]:
        x, y = _at(placed, s["instance"])
        out.append(
            {
                "instance": s["instance"],
                "name": s["name"],
                "mw": round(s["mw"], 1),
                "missing": list(s["missing"]),
                "cause": starved_cause(s["missing"]),
                "x_m": x,
                "y_m": y,
            }
        )
    return out


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
    try:
        st = _state(request, save, world)
    except Exception as exc:
        return _fail(f"could not read save: {exc}", 404)

    counted = biomass == "include"
    graph = st.graph
    placed = fidentity.positions(st.projection)
    records = {
        key: {r["instance"].rsplit(".", 1)[-1]: r for r in st.projection.get(key) or ()}
        for key in RECORD_LISTS
    }
    label_of = {a: label.name for label in st.labels.labels for a in label.anchors}

    circuits = []
    circuit_of: dict[str, int] = {}
    for component in graph.components("power"):
        members = set(component)
        sub = {key: [r for s, r in records[key].items() if s in members] for key in RECORD_LISTS}
        if not any(sub.values()):
            continue
        report = PowerLedger(projection=sub, game=st.game).power_report(biomass=counted)
        standing = [s for s in component if s in placed]
        pts = [placed[s][:2] for s in standing]
        box = geo.bbox(pts)
        centre = geo.centroid(pts)
        named = Counter(label_of[s] for s in standing if s in label_of)
        circuits.append(
            {
                "index": 0,
                "members": members,
                "ledger": _ledger(report),
                "generators": _groups(report),
                "starved": _starved(report, placed),
                "unmodellable": list(report["unmodellable"]),
                "consumers": len(sub["machines"]) + len(sub["extractors"]),
                "poles": sum(1 for s in component if graph.kind(s) in ("pole", "tower")),
                "factories": [n for n, _ in named.most_common()],
                "factory_count": len(named),
                "centroid_m": None if centre is None else [_m(centre[0]), _m(centre[1])],
                "bbox_m": None if box is None else [_m(v) for v in box],
            }
        )
    circuits.sort(key=lambda c: -(c["ledger"]["generation_mw"] + c["ledger"]["draw_mw"]))
    for index, row in enumerate(circuits):
        row["index"] = index
        for short in row.pop("members"):
            circuit_of[short] = index

    dark = assess("world", graph.machines(), st.game, st.projection, graph)
    try:
        rmap = spatial_regions.load_regions()
    except FileNotFoundError:
        rmap = None

    def region(short: str) -> dict | None:
        pos = placed.get(short)
        if rmap is None or pos is None:
            return None
        return _label_json(rmap.label_for(pos[0], pos[1]))

    def refs(shorts: list[str]) -> list[dict]:
        out = []
        for short in sorted(shorts):
            x, y = _at(placed, short)
            cls = graph.cls.get(short, "")
            out.append(
                {
                    "instance": short,
                    "name": st.game.building_name(cls) or cls,
                    "circuit": circuit_of.get(short),
                    "factory": label_of.get(short),
                    "region": region(short),
                    "x_m": x,
                    "y_m": y,
                }
            )
        return out

    world_report = st.power_report(biomass=counted)
    return {
        "world": _ledger(world_report),
        "paused": world_report["paused_count"],
        "generators": _groups(world_report),
        "starved": _starved(world_report, placed),
        "unmodellable": list(world_report["unmodellable"]),
        "circuits": circuits,
        "off_grid": {
            "consumers": world_report["unwired_consumers"],
            "paused": world_report["unwired_paused"],
            "draw_mw": round(world_report["unwired_draw_mw"], 1),
            "generators": world_report["unwired_generators"],
            "generation_mw": round(world_report["unwired_generation_mw"], 1),
        },
        "unwired": refs(dark.unwired),
        "unwired_generators": refs(dark.unwired_generators),
        "no_generator": refs(dark.no_generator),
    }

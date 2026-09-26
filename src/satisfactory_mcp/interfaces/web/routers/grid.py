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
from ....domain.power.report import PowerLedger
from ....domain.spatial import geo
from ..serial import _fail, _m, _state

__all__ = ["router"]

router = APIRouter(prefix="/api")

RECORD_LISTS = ("generators", "machines", "extractors")


class Ledger(TypedDict):
    generation_mw: float
    starved_generation_mw: float
    draw_mw: float
    measured_draw_mw: float
    headroom_mw: float
    measured_headroom_mw: float
    utilisation: float
    monitored: int
    unmonitored: int


class GeneratorGroup(TypedDict):
    name: str
    count: int
    mw: float


class StarvedGenerator(TypedDict):
    instance: str
    name: str
    mw: float
    missing: list[str]
    x_m: float | None
    y_m: float | None


class MachineRef(TypedDict):
    instance: str
    name: str
    x_m: float | None
    y_m: float | None


class CircuitRow(TypedDict):
    """``bbox_m`` is null when no record on the circuit has a position."""

    index: int
    ledger: Ledger
    generators: list[GeneratorGroup]
    starved: list[StarvedGenerator]
    consumers: int
    poles: int
    factories: list[str]
    centroid_m: tuple[float, float] | None
    bbox_m: tuple[float, float, float, float] | None


class CircuitsResponse(TypedDict):
    world: Ledger
    paused: int
    generators: list[GeneratorGroup]
    starved: list[StarvedGenerator]
    unmodellable: list[str]
    circuits: list[CircuitRow]
    unwired: list[MachineRef]
    no_generator: list[MachineRef]


def _ledger(report: dict) -> dict:
    return {
        "generation_mw": round(report["generation_mw"], 1),
        "starved_generation_mw": round(report["starved_generation_mw"], 1),
        "draw_mw": round(report["draw_mw"], 1),
        "measured_draw_mw": round(report["measured_draw_mw"], 1),
        "headroom_mw": round(report["headroom_mw"], 1),
        "measured_headroom_mw": round(report["measured_headroom_mw"], 1),
        "utilisation": round(report["utilisation"], 3),
        "monitored": report["monitored"],
        "unmonitored": report["unmonitored"],
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
                "x_m": x,
                "y_m": y,
            }
        )
    return out


@router.get("/power/circuits", response_model=CircuitsResponse)
def power_circuits(request: Request, save: str | None = None, world: str | None = None) -> Any:
    """Generation against draw, nameplate and measured, for the world and for each circuit.

    ``unwired`` and ``no_generator`` are ``assess``'s two lists over every machine in the
    world: no power edge at all, and a wire to a circuit no generator stands on.
    """
    try:
        st = _state(request, save, world)
    except Exception as exc:
        return _fail(f"could not read save: {exc}", 404)

    graph = st.graph
    placed = fidentity.positions(st.projection)
    records = {
        key: {r["instance"].rsplit(".", 1)[-1]: r for r in st.projection.get(key) or ()}
        for key in RECORD_LISTS
    }
    label_of = {a: label.name for label in st.labels.labels for a in label.anchors}

    circuits = []
    for component in graph.components("power"):
        members = set(component)
        sub = {key: [r for s, r in records[key].items() if s in members] for key in RECORD_LISTS}
        if not any(sub.values()):
            continue
        report = PowerLedger(projection=sub, game=st.game).power_report()
        standing = [s for s in component if s in placed]
        pts = [placed[s][:2] for s in standing]
        box = geo.bbox(pts)
        centre = geo.centroid(pts)
        named = Counter(label_of[s] for s in standing if s in label_of)
        circuits.append(
            {
                "index": 0,
                "ledger": _ledger(report),
                "generators": _groups(report),
                "starved": _starved(report, placed),
                "consumers": len(sub["machines"]) + len(sub["extractors"]),
                "poles": sum(1 for s in component if graph.kind(s) in ("pole", "tower")),
                "factories": [n for n, _ in named.most_common(3)],
                "centroid_m": None if centre is None else [_m(centre[0]), _m(centre[1])],
                "bbox_m": None if box is None else [_m(v) for v in box],
            }
        )
    circuits.sort(key=lambda c: -(c["ledger"]["generation_mw"] + c["ledger"]["draw_mw"]))
    for index, row in enumerate(circuits):
        row["index"] = index

    dark = assess("world", graph.machines(), st.game, st.projection, graph)

    def refs(shorts: list[str]) -> list[dict]:
        out = []
        for short in sorted(shorts):
            x, y = _at(placed, short)
            cls = graph.cls.get(short, "")
            out.append(
                {"instance": short, "name": st.game.building_name(cls) or cls, "x_m": x, "y_m": y}
            )
        return out

    world_report = st.power_report()
    return {
        "world": _ledger(world_report),
        "paused": world_report["paused_count"],
        "generators": _groups(world_report),
        "starved": _starved(world_report, placed),
        "unmodellable": list(world_report["unmodellable"]),
        "circuits": circuits,
        "unwired": refs(dark.unwired),
        "no_generator": refs(dark.no_generator),
    }

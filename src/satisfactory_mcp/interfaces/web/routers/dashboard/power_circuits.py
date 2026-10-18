"""``/api/power/circuits``: the ``power_report`` ledger, world-wide and per wired circuit.

A circuit is a grid as the game runs it: a connected component of the save's power edges, cut
at every switch standing off and joined through every one standing on (save-projection.md
§6.12a). Each one's figures are the same ``PowerLedger`` run over only the records on it. The limits of that reading are in
docs/spatial-and-map.md §21; wire rules in docs/web-wire.md.

Handler names are operation_ids (wire rule 1).
"""

from __future__ import annotations

from collections import Counter, deque
from collections.abc import Hashable
from dataclasses import dataclass

from fastapi import APIRouter, Request
from typing_extensions import TypedDict

from .....core.gamedata.model import GameData
from .....core.saveio.records import instance_leaf
from .....core.saveio.schema import BuildableRecord
from .....core.singleflight import Singleflight
from .....domain.factories import candidates
from .....domain.factories.health import assess
from .....domain.factories.model import FactoryGraph
from .....domain.power.report import (
    BatteryBank,
    MachineGroups,
    PowerLedger,
    battery_bank,
    generator_building,
    starved_cause,
)
from .....domain.power.views import PowerReport
from .....domain.progression.generators import GeneratorCandidate, generator_candidates
from .....domain.spatial import geo
from .....domain.spatial.regions import RegionMap
from .....domain.world.state import WorldState
from ...serial import (
    Biomass,
    Placed,
    Region,
    bbox_m,
    cm_to_m,
    point_m,
    region_json,
    regions_or_none,
    require_world,
)

__all__ = ["router"]

router = APIRouter(prefix="/api")


class Ledger(TypedDict):
    """``capacity_mw`` is production capacity, ``generation_mw`` less what starved generators
    cannot run; both headrooms are taken from it. ``biomass_mw`` and ``biomass_generators``
    are the wired burners left out of every figure here under ``?biomass=exclude``; both are
    0 under ``include``. ``batteries`` counts the Power Storage on the grid, ``storage_mwh`` the
    charge they hold and ``storage_capacity_mwh`` the most they can; all three are 0 on a grid
    with none, and none of them is in either headroom. ``switched_off`` counts the consumers
    behind a power switch standing off and ``switched_off_draw_mw`` their maximum consumption,
    which ``draw_mw`` includes and neither measured figure does."""

    generation_mw: float
    starved_generation_mw: float
    capacity_mw: float
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
    batteries: int
    storage_mwh: float
    storage_capacity_mwh: float
    switched_off: int
    switched_off_draw_mw: float


class GeneratorGroup(TypedDict):
    """One generator type, counted and summed; ``starved_mw`` is the part of ``mw`` standing on
    starved generators, ``icon`` the descriptor ``/api/icons/`` files its picture under."""

    name: str
    count: int
    mw: float
    starved_mw: float
    icon: str | None


class StarvedGenerator(TypedDict):
    """``cause`` is ``missing`` as one phrase: "out of Coal, Water" or "no fuel loaded".
    ``circuit``, ``factory`` and ``region`` read as on ``MachineRef``."""

    instance: str
    name: str
    mw: float
    missing: list[str]
    cause: str
    circuit: int | None
    factory: str | None
    region: Region | None
    icon: str | None
    x_m: float | None
    y_m: float | None


class MachineRef(TypedDict):
    """``circuit`` is the ``index`` of the circuit it stands on, null when on none;
    ``factory`` the named factory it is an anchor of, null when none; ``region`` where it
    stands, null when unplaced or off the region map; ``icon`` the building's descriptor for
    ``/api/icons/``, null when game data has none."""

    instance: str
    name: str
    circuit: int | None
    factory: str | None
    region: Region | None
    icon: str | None
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


class FactoryDraw(TypedDict):
    """A named factory's draw on one circuit, counting only its machines standing on it:
    ``draw_mw`` is maximum consumption, ``measured_draw_mw`` power consumption."""

    name: str
    draw_mw: float
    measured_draw_mw: float


class CircuitRow(TypedDict):
    """``bbox_m`` is null when no record on the circuit has a position. ``factories`` is
    every named factory on it, most machines first, with its draw on this circuit alone;
    ``unmodellable`` the generator classes on it that game data cannot rate, whose output
    the ledger leaves out."""

    index: int
    ledger: Ledger
    generators: list[GeneratorGroup]
    starved: list[StarvedGenerator]
    unmodellable: list[str]
    consumers: int
    poles: int
    factories: list[FactoryDraw]
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


def _ledger(report: PowerReport, bank: BatteryBank) -> Ledger:
    return {
        "generation_mw": round(report["generation_mw"], 1),
        "starved_generation_mw": round(report["starved_generation_mw"], 1),
        "capacity_mw": round(report["capacity_mw"], 1),
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
        "batteries": bank.batteries,
        "storage_mwh": round(bank.stored_mwh, 1),
        "storage_capacity_mwh": round(bank.capacity_mwh, 1),
        "switched_off": report["switched_off_consumers"],
        "switched_off_draw_mw": round(report["switched_off_draw_mw"], 1),
    }


def _position_m(placed: Placed, leaf: str) -> tuple[float | None, float | None]:
    pos = placed.get(leaf)
    return (cm_to_m(pos[0]), cm_to_m(pos[1])) if pos else (None, None)


def _icon(game: GameData, cls: str) -> str | None:
    building = generator_building(game, cls)
    return building.descriptor if building is not None else None


@dataclass(frozen=True)
class _Places:
    """Where a record stands: its circuit, named factory and region, and its building."""

    st: WorldState
    placed: Placed
    label_of: dict[str, str]
    region_map: RegionMap | None
    circuit_of: dict[str, int]

    def region(self, leaf: str) -> Region | None:
        pos = self.placed.get(leaf)
        if self.region_map is None or pos is None:
            return None
        return region_json(self.region_map.label_for(pos[0], pos[1]))

    def icon(self, leaf: str) -> str | None:
        return _icon(self.st.game, self.st.graph.cls.get(leaf, ""))


def _groups(report: PowerReport, game: GameData) -> list[GeneratorGroup]:
    starved: dict[str, float] = {}
    for entry in report["starved_generators"]:
        starved[entry["name"]] = starved.get(entry["name"], 0.0) + entry["mw"]
    ranked = sorted(report["by_generator"].items(), key=lambda pair: -pair[1]["mw"])
    return [
        {
            "name": g["name"],
            "count": g["count"],
            "mw": round(g["mw"], 1),
            "starved_mw": round(starved.get(g["name"], 0.0), 1),
            "icon": _icon(game, cls),
        }
        for cls, g in ranked
    ]


def _starved(report: PowerReport, places: _Places) -> list[StarvedGenerator]:
    rows: list[StarvedGenerator] = []
    for starved in report["starved_generators"]:
        leaf = starved["instance"]
        x, y = _position_m(places.placed, leaf)
        rows.append(
            {
                "instance": leaf,
                "name": starved["name"],
                "mw": round(starved["mw"], 1),
                "missing": list(starved["missing"]),
                "cause": starved_cause(starved["missing"]),
                "circuit": places.circuit_of.get(leaf),
                "factory": places.label_of.get(leaf),
                "region": places.region(leaf),
                "icon": places.icon(leaf),
                "x_m": x,
                "y_m": y,
            }
        )
    return rows


def _factory_draws(
    sub: MachineGroups,
    names: list[str],
    label_of: dict[str, str],
    game: GameData,
) -> list[FactoryDraw]:
    """Each named factory's draw from this circuit's live consumers only, so a factory spanning
    two circuits is split between them rather than counted on both, and its machines behind an
    off switch are left to the circuit's own figure."""
    own: dict[str, MachineGroups] = {
        name: {"machines": [], "extractors": [], "generators": []} for name in names
    }
    for machine in sub["machines"]:
        if (mine := own.get(label_of.get(instance_leaf(machine["instance"]), ""))) is not None:
            mine["machines"].append(machine)
    for extractor in sub["extractors"]:
        if (mine := own.get(label_of.get(instance_leaf(extractor["instance"]), ""))) is not None:
            mine["extractors"].append(extractor)
    rows: list[FactoryDraw] = []
    for name in names:
        report = PowerLedger(projection=own[name], game=game).power_report()
        rows.append(
            {
                "name": name,
                "draw_mw": round(report["draw_mw"], 1),
                "measured_draw_mw": round(report["measured_draw_mw"], 1),
            }
        )
    return rows


def _is_battery(record: BuildableRecord, game: GameData) -> bool:
    building = game.buildings.get(record["cls"])
    return building is not None and building.power_store_capacity_mwh > 0


def _fold_switched(
    graph: FactoryGraph, grids: list[list[str]], live: set[int], dead: set[int]
) -> list[tuple[list[str], set[str]]]:
    """``grids`` with each ``dead`` one folded into the ``live`` grid nearest it across off
    switches: what flipping them would join. Each entry is the members and the folded ones."""
    links = graph.switch_links(grids)
    owner = {i: i for i in sorted(live)}
    queue = deque(owner)
    while queue:
        at = queue.popleft()
        for near in sorted(links.get(at, ())):
            if near in dead and near not in owner:
                owner[near] = owner[at]
                queue.append(near)
    folded: dict[int, list[str]] = {}
    for i, j in owner.items():
        if i != j:
            folded.setdefault(j, []).extend(grids[i])
    return [
        (grid + folded.get(i, []), set(folded.get(i, ())))
        for i, grid in enumerate(grids)
        if i not in owner or owner[i] == i
    ]


def _circuit_rows(
    st: WorldState, placed: Placed, label_of: dict[str, str], counted: bool
) -> list[tuple[CircuitRow, set[str], PowerReport, BatteryBank]]:
    """Every circuit with a record on it, biggest ledger first, with its members and report.

    ``starved`` is filled by the caller: it names the circuit, whose index is known only once
    every circuit is ranked.
    """
    graph = st.graph
    machines = {instance_leaf(r["instance"]): r for r in st.projection.get("machines") or ()}
    extractors = {instance_leaf(r["instance"]): r for r in st.projection.get("extractors") or ()}
    generators = {instance_leaf(r["instance"]): r for r in st.projection.get("generators") or ()}
    facilities: dict[str, BuildableRecord] = {
        instance_leaf(r["instance"]): r for r in st.projection.get("facilities") or ()
    }
    rows: list[tuple[CircuitRow, set[str], PowerReport, BatteryBank]] = []
    grids = graph.grids()
    banked = {leaf for leaf, r in facilities.items() if _is_battery(r, st.game)}
    live = {i for i, grid in enumerate(grids) if any(n in generators for n in grid)}
    dead = {i for i, grid in enumerate(grids) if i not in live and not banked.intersection(grid)}
    for component, off in _fold_switched(graph, grids, live, dead):
        members = set(component)
        sub: MachineGroups = {
            "machines": [r for leaf, r in machines.items() if leaf in members],
            "extractors": [r for leaf, r in extractors.items() if leaf in members],
            "generators": [r for leaf, r in generators.items() if leaf in members],
        }
        bank = battery_bank((r for leaf, r in facilities.items() if leaf in members), st.game)
        if not any(sub.values()) and not bank.batteries:
            continue
        ledger = PowerLedger(projection=sub, game=st.game, switched_off=frozenset(off))
        report = ledger.power_report(biomass=counted)
        lit: MachineGroups = {
            "machines": [r for r in sub["machines"] if instance_leaf(r["instance"]) not in off],
            "extractors": [r for r in sub["extractors"] if instance_leaf(r["instance"]) not in off],
            "generators": sub["generators"],
        }
        standing = [leaf for leaf in component if leaf in placed]
        points = [placed[leaf][:2] for leaf in standing]
        centre = geo.centroid(points)
        named = Counter(label_of[leaf] for leaf in standing if leaf in label_of)
        row: CircuitRow = {
            "index": 0,
            "ledger": _ledger(report, bank),
            "generators": _groups(report, st.game),
            "starved": [],
            "unmodellable": list(report["unmodellable"]),
            "consumers": len(sub["machines"]) + len(sub["extractors"]),
            "poles": sum(1 for leaf in component if graph.kind(leaf) in ("pole", "tower")),
            "factories": _factory_draws(
                lit, [name for name, _count in named.most_common()], label_of, st.game
            ),
            "factory_count": len(named),
            "centroid_m": None if centre is None else point_m(centre),
            "bbox_m": bbox_m(placed, standing),
        }
        rows.append((row, members, report, bank))
    rows.sort(key=lambda r: -(r[0]["ledger"]["generation_mw"] + r[0]["ledger"]["draw_mw"]))
    for index, (row, *_rest) in enumerate(rows):
        row["index"] = index
    return rows


def _machine_refs(leaves: list[str], places: _Places) -> list[MachineRef]:
    """Machines by leaf, sorted, with the circuit, factory and region each stands in."""
    st = places.st
    refs: list[MachineRef] = []
    for leaf in sorted(leaves):
        x, y = _position_m(places.placed, leaf)
        cls = st.graph.cls.get(leaf, "")
        refs.append(
            {
                "instance": leaf,
                "name": st.game.building_name(cls) or cls,
                "circuit": places.circuit_of.get(leaf),
                "factory": places.label_of.get(leaf),
                "region": places.region(leaf),
                "icon": places.icon(leaf),
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
) -> CircuitsResponse:
    """Generation against draw, nameplate and measured, for the world and for each circuit.

    ``unwired`` and ``no_generator`` are ``assess``'s two lists over every machine in the
    world: no power edge at all, and a wire to a circuit no generator stands on. The world
    ledger counts only what stands on a wire, so it is the sum of the circuits.
    """
    st = require_world(request, save, world)

    counted = biomass == "include"
    placed = candidates.positions(st.projection)
    label_of = {anchor: label.name for label in st.labels.labels for anchor in label.anchors}
    rows = _circuit_rows(st, placed, label_of, counted)
    circuit_of = {leaf: row["index"] for row, members, *_rest in rows for leaf in members}
    places = _Places(st, placed, label_of, regions_or_none(), circuit_of)
    for row, _members, report, _bank in rows:
        row["starved"] = _starved(report, places)
    banks = [bank for *_rest, bank in rows]
    wired_bank = BatteryBank(
        sum(b.batteries for b in banks),
        sum(b.stored_mwh for b in banks),
        sum(b.capacity_mwh for b in banks),
    )

    wiring = assess("world", st.graph.machines(), st.game, st.projection, st.graph)
    world_report = st.power_report(biomass=counted)
    return {
        "world": _ledger(world_report, wired_bank),
        "paused": world_report["paused_count"],
        "generators": _groups(world_report, st.game),
        "starved": _starved(world_report, places),
        "unmodellable": list(world_report["unmodellable"]),
        "circuits": [row for row, *_rest in rows],
        "off_grid": {
            "consumers": world_report["unwired_consumers"],
            "paused": world_report["unwired_paused"],
            "draw_mw": round(world_report["unwired_draw_mw"], 1),
            "generators": world_report["unwired_generators"],
            "generation_mw": round(world_report["unwired_generation_mw"], 1),
        },
        "unwired": _machine_refs(wiring.unwired, places),
        "unwired_generators": _machine_refs(wiring.unwired_generators, places),
        "no_generator": _machine_refs(wiring.no_generator, places),
    }


#: (projection, world) -> the projection and the candidates: planner solves, kept per save
#: like the progress reads. The entry holds the projection, so its id is not reused.
_CANDIDATES: Singleflight[Hashable, tuple[object, list[GeneratorCandidate]]] = Singleflight(
    maxsize=8
)


@router.get("/power/generators", response_model=list[GeneratorCandidate])
def power_generators(
    request: Request, save: str | None = None, world: str | None = None
) -> list[GeneratorCandidate]:
    """Every fuelled generator type, biggest first, with whether the world can run it: the
    first runnable one is what a circuit short of power builds out with."""
    st = require_world(request, save, world)
    key = (id(st.projection), st.world_id)
    return _CANDIDATES.get(key, lambda: (st.projection, generator_candidates(st)))[1]

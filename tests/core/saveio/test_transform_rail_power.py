"""Schema 26: rail power and other hidden connections join grids with no wire (§6.12b)."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any, cast

import pytest

from satisfactory_mcp.core.saveio.extract.interning import Interner
from satisfactory_mcp.core.saveio.extract.power import hidden_power
from satisfactory_mcp.core.saveio.extract.walk import _ProjectionWalk, extract_projection
from satisfactory_mcp.core.saveio.records import instance_leaf
from satisfactory_mcp.domain.factories.build import build_graph
from tests.support.paths import REPO_ROOT

LEVEL = "Persistent_Level:PersistentLevel."
RAIL_A = LEVEL + "RailroadSubsystem.FGPowerConnectionComponent_1"
RAIL_B = LEVEL + "RailroadSubsystem.FGPowerConnectionComponent_2"


def _groups(links: list[tuple[str, str]]) -> list[list[str]]:
    actors = Interner()
    rows = hidden_power(links, actors)
    names = actors.names()
    return [[names[i] for i in row] for row in rows]


def _both_ways(member: str, rail: str) -> list[tuple[str, str]]:
    """The save writes every hidden connection on both of its ends."""
    return [(LEVEL + member, rail), (rail, LEVEL + member)]


def test_stations_and_platforms_on_one_third_rail_are_one_group():
    links = [
        *_both_ways("Build_TrainStation_C_1.PowerConnection", RAIL_A),
        *_both_ways("Build_TrainDockingStation_C_2.FGPowerConnection", RAIL_A),
        *_both_ways("Build_TrainStation_C_3.PowerConnection", RAIL_A),
    ]
    assert _groups(links) == [
        ["Build_TrainDockingStation_C_2", "Build_TrainStation_C_1", "Build_TrainStation_C_3"]
    ]


def test_two_rail_networks_stay_two_groups():
    """The subsystem owns every third rail, so it must not merge them."""
    links = [
        *_both_ways("Build_TrainStation_C_1.PowerConnection", RAIL_A),
        *_both_ways("Build_TrainDockingStation_C_2.FGPowerConnection", RAIL_A),
        *_both_ways("Build_TrainStation_C_3.PowerConnection", RAIL_B),
        *_both_ways("Build_TrainDockingStation_C_4.FGPowerConnection", RAIL_B),
    ]
    assert _groups(links) == [
        ["Build_TrainDockingStation_C_2", "Build_TrainStation_C_1"],
        ["Build_TrainDockingStation_C_4", "Build_TrainStation_C_3"],
    ]


def test_a_locomotive_or_a_hover_pack_joins_nothing():
    links = [
        *_both_ways("Build_TrainStation_C_1.PowerConnection", RAIL_A),
        *_both_ways("BP_Locomotive_C_5.SlidingShoe", RAIL_A),
        (LEVEL + "Equip_HoverPack_C_6.Power Connection", LEVEL + "Build_PowerPoleMk1_C_7.Conn"),
    ]
    assert _groups(links) == []


def test_an_elevator_joins_its_floor_stop():
    links = [
        (
            LEVEL + "Build_Elevator_C_1.PowerConnection ",
            LEVEL + "Build_ElevatorFloorStop_C_2.PowerInput",
        )
    ]
    assert _groups(links) == [["Build_ElevatorFloorStop_C_2", "Build_Elevator_C_1"]]


def test_the_walk_reads_hidden_connections_off_a_connection_component():
    walk = _ProjectionWalk(out=cast("Any", {}))
    component = LEVEL + "Build_TrainDockingStation_C_2.FGPowerConnection"
    walk._on_connections(component, {"mHiddenConnections": [SimpleNamespace(pathName=RAIL_A)]})
    assert walk.hidden_links == [(component, RAIL_A)]


def test_a_public_rail_save_powers_every_station_through_the_track():
    """Dunarr-076, a shared save kept out of git; skipped where it is not present. Wires alone
    reach 7 of its 62 stations and platforms; the game puts all 62 on the generators'
    circuit, and so does the rail."""
    path = REPO_ROOT / "data" / "local" / "external-saves" / "etothepii4-sfp" / "Dunarr-076.sav"
    if not path.exists():
        pytest.skip("the external saves are not on this machine")
    projection = extract_projection(str(path))
    graph = build_graph(projection)
    sources = {instance_leaf(r["instance"]) for r in projection.get("generators") or ()}
    reached = graph.power_reach(sources)
    stations = [n for n in graph.cls if n.startswith(("Build_TrainStation", "Build_TrainDocking"))]
    assert len(stations) == 62
    assert all(s in reached for s in stations)

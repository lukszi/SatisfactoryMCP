"""The two plumbing faults that need no walk of the pipes.

Both thresholds are derived from game data rather than written down, so the first test
here is the one that matters: it asserts the derivation reproduces the two figures the
FICSIT Plumbing Manual states, which is the only cross-check either number has.
"""

from __future__ import annotations

import pytest

from satisfactory_mcp.domain.factories.build import build_graph
from satisfactory_mcp.domain.world import headlift
from satisfactory_mcp.domain.world.plumbing import (
    PUMP_CLASSES,
    balance_level_m3,
    dark_pumps,
    throttled_buffers,
)
from satisfactory_mcp.domain.world.state import WorldState
from satisfactory_mcp.interfaces.mcp.tools import factories as tool

pytestmark = pytest.mark.integration

SMALL = "Build_PipeStorageTank_C"
INDUSTRIAL = "Build_IndustrialTank_C"


def _buffer(name, cls, level, fluid="Desc_Water_C"):
    return {
        "cls": cls,
        "instance": f"Persistent_Level:PersistentLevel.{name}",
        "pos": [0, 0, 0],
        "yaw": 0,
        "fluid": fluid,
        "stored_m3": level,
    }


def test_the_balance_level_reproduces_the_manuals_two_figures(game):
    """400 m3 over 8 m and 2,400 over 12 m both put 1.5 m of head at the manual's number.

    Capacity and height are read off Docs.json -- ``mStorageCapacity`` and the clearance
    box -- so this is the check that the clearance height IS the fluid column's height.
    """
    assert balance_level_m3(game, SMALL) == pytest.approx(75.0)
    assert balance_level_m3(game, INDUSTRIAL) == pytest.approx(300.0)


def test_a_class_that_stores_no_fluid_has_no_balance_level(game):
    assert balance_level_m3(game, "Build_ConstructorMk1_C") is None
    assert balance_level_m3(game, "Build_NotAThing_C") is None


def test_only_buffers_under_their_level_are_reported(game):
    projection = {
        "storage": [
            _buffer("a", SMALL, 74.9),
            _buffer("b", SMALL, 75.0),
            _buffer("c", INDUSTRIAL, 299.0),
            _buffer("d", INDUSTRIAL, 2000.0),
            # A solid container: no ``stored_m3``, and never a plumbing finding.
            {"cls": "Build_StorageContainerMk1_C", "instance": "P.e", "items": [], "slots": 24},
        ]
    }
    found = [b.instance for b in throttled_buffers(projection, game)]
    assert found == ["c", "a"], "emptiest share first, and exactly at the level is fine"


def test_an_empty_buffer_is_the_same_fault_at_its_limit(game):
    projection = {"storage": [_buffer("dry", INDUSTRIAL, 0.0)]}
    (found,) = throttled_buffers(projection, game)
    assert found.stored_m3 == 0.0
    assert found.balance_m3 == pytest.approx(300.0)
    assert found.share == 0.0


class _Graph:
    def __init__(self, cls, wired):
        self.cls = cls
        self._wired = wired

    def neighbours(self, node, layer):
        assert layer == "power"
        return ["a pole"] if node in self._wired else []


def test_a_pump_with_no_power_edge_is_dark_and_a_valve_is_not():
    """A Valve is the same native class as a pump and lifts nothing, so leaving it
    unpowered costs no head lift and it must not appear here."""
    graph = _Graph(
        {
            "Build_PipelinePump_C_1": "Build_PipelinePump_C",
            "Build_PipelinePumpMk2_C_2": "Build_PipelinePumpMk2_C",
            "Build_Valve_C_3": "Build_Valve_C",
        },
        wired={"Build_PipelinePump_C_1"},
    )
    counts = {"Build_PipelinePump_C": 1, "Build_PipelinePumpMk2_C": 1, "Build_Valve_C": 1}
    dark, unseen = dark_pumps({"building_counts": counts}, graph)
    assert dark == ["Build_PipelinePumpMk2_C_2"]
    assert unseen == 0


def test_a_pump_the_graph_never_saw_is_counted_rather_than_called_wired():
    """The actor list is cut from edges, so a pump coupled to nothing is absent from it."""
    graph = _Graph(
        {"Build_PipelinePump_C_1": "Build_PipelinePump_C"}, wired={"Build_PipelinePump_C_1"}
    )
    dark, unseen = dark_pumps({"building_counts": {"Build_PipelinePump_C": 3}}, graph)
    assert dark == []
    assert unseen == 2


def test_the_reference_world_has_three_throttled_buffers_and_seven_dark_pumps(game, projection):
    """Both counts, measured. Neither is zero, which is what made the checks worth writing:
    two of five buffers hold enough, and every dark pump is a Mk2."""
    throttled = throttled_buffers(projection, game)
    assert [(b.cls, round(b.stored_m3)) for b in throttled] == [
        ("Build_IndustrialTank_C", 54),
        ("Build_IndustrialTank_C", 74),
        ("Build_PipeStorageTank_C", 42),
    ], "worst SHARE first, so a 42 m3 small buffer is less starved than a 54 m3 industrial"
    dark, unseen = dark_pumps(projection, build_graph(projection))
    assert len(dark) == 7
    assert {c for c in PUMP_CLASSES if any(n.startswith(c) for n in dark)} == {
        "Build_PipelinePumpMk2_C"
    }
    assert unseen == 0, "every pump in the census is coupled to a pipe and so was checked"


def test_the_sweep_over_every_factory_reports_both_and_calls_them_world_wide(monkeypatch, labelled):
    """The sweep is the only view either finding can honestly appear in: a buffer and a
    pump belong to no machine set, so nothing scopes them to one factory."""
    monkeypatch.setattr(tool, "_state", lambda save=None, world=None, as_of=None: labelled)
    out = tool.factory_health(factory="all")
    assert "## fluid buffers below the level they need" in out
    assert "3 fluid buffer(s) world-wide are under the level they need" in out
    assert "7 pipeline pump(s) have no electrical connection" in out
    assert "Build_IndustrialTank_C_2147229749\tFuel\t54\t300" in out
    assert "coupled to no pipe at all" not in out, "nothing went unchecked here"


# ------------------------------------- the ladder as factory_health prints it, §24.5


def _thirsty(projection):
    """The reference world with one real refinery emptied of its water.

    A perturbation rather than a hand-built world: the rendering under test reaches for the
    graph, the labels and the conduit runs, and a stub projection answers none of them.
    """
    wet = next(
        record
        for record in projection["machines"]
        if "Desc_Water_C" in ((record.get("buffers") or {}).get("in") or {}).get("items", {})
    )
    dry = dict(
        wet,
        buffers={"in": {"items": {}, "slots": 2}, "out": {"items": {}, "slots": 3}},
        uptime={"window_s": 300.0, "produce_s": 0.0, "cur_window_s": 10.0, "producing": False},
    )
    machines = [dry if r is wet else r for r in projection["machines"]]
    return dict(projection, machines=machines), dry["instance"].rsplit(".", 1)[-1]


def _crested(consumers, fluid="Desc_Water_C"):
    crest = headlift.Crest(
        fluid=fluid,
        crest_m=26.4,
        head_m=17.2,
        consumers=tuple(consumers),
        marginal=False,
        assumed=True,
        pos=(1000.0, 2000.0, 26.4),
    )
    return headlift.HeadLift(
        crests=(crest,),
        consumers=1,
        unfed_ports=(),
        networks=1,
        gas_networks=0,
        ambiguous_ports=0,
    )


def test_the_sweep_names_the_machines_on_a_pipe_network_no_source_reaches(monkeypatch, labelled):
    """Rung (1), world-wide, and the only rung that fires on this author's saves."""
    monkeypatch.setattr(tool, "_state", lambda save=None, world=None, as_of=None: labelled)
    monkeypatch.setattr(
        headlift,
        "head_lift",
        lambda *_a, **_k: headlift.HeadLift(
            crests=(),
            consumers=2,
            unfed_ports=("Build_OilRefinery_C_1", "Build_OilRefinery_C_2"),
            networks=1,
            gas_networks=0,
            ambiguous_ports=0,
        ),
    )
    out = tool.factory_health(factory="all")
    assert "2 machine(s) draw a fluid from a pipe network that reaches NO source at all" in out
    assert "rung (1)" in out
    assert "Build_OilRefinery_C_1, Build_OilRefinery_C_2" in out


def test_a_machine_behind_a_crest_is_told_where_the_pump_goes(monkeypatch, game, projection):
    """Rung (2) end to end: the cause names the rung, and the crest that binds is named
    beside it -- twenty machines behind one hill are one problem with one fix."""
    patched, short = _thirsty(projection)
    monkeypatch.setattr(
        tool, "_state", lambda save=None, world=None, as_of=None: WorldState(patched, game)
    )
    monkeypatch.setattr(headlift, "head_lift", lambda *_a, **_k: _crested([short]))
    out = tool.factory_health(factory=f"machine:{short}")
    assert "Water (head lift)" in out
    assert "## where the fluid stops climbing" in out
    assert "Water\t26.4\t17.2\t9.2\t1" in out
    assert "1 at (2) head lift" in out
    assert "a pump placed BEFORE the crest is the fix" in out
    (row,) = [line for line in out.splitlines() if line.startswith(f"{short}\tstarved")]
    assert row.endswith("Copper Ore, Water (head lift)"), (
        "the whole point: the fluid is answered at the rung that fires and the solid beside "
        "it is left alone"
    )
    assert "at (3) flow rate" not in out, "a head-lift failure never gets the rates answer"


def test_a_crest_on_another_fluid_does_not_claim_this_machine(monkeypatch, game, projection):
    """The reference world reports no crest at all, so an unrelated one leaves the same
    machine on rung (3) -- which is what makes the assertion above a verdict."""
    patched, short = _thirsty(projection)
    monkeypatch.setattr(
        tool, "_state", lambda save=None, world=None, as_of=None: WorldState(patched, game)
    )
    monkeypatch.setattr(
        headlift, "head_lift", lambda *_a, **_k: _crested([short], fluid="Desc_LiquidOil_C")
    )
    out = tool.factory_health(factory=f"machine:{short}")
    assert "Water (flow rate)" in out
    assert "1 at (3) flow rate" in out
    assert "the head-lift model checked the climb and ruled its own rung out" in out

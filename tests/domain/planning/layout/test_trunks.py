"""Which nodes share a pipe.

`logistics` already counts LINES -- ceil(rate / capacity) -- which is the right total and
says nothing about which nodes share one, and the layout schematic starts at the factory
edge with the crude already arrived. The gap between them is the walk a player has to plan,
and it is where the 40 m head span from search_resource_nodes becomes actionable: it is not
spread over the field, it sits on one trunk out of six.
"""

from __future__ import annotations

import math

import pytest

from satisfactory_mcp import server as srv
from satisfactory_mcp.domain.planning.prepare import prepare
from satisfactory_mcp.domain.planning.trunks import plan_trunks
from tests.support.reference_world import REFERENCE_MAX_MW_ARGS

pytestmark = pytest.mark.integration


@pytest.fixture
def reference_trunks(game, state):
    return plan_trunks(prepare(game, state, dict(REFERENCE_MAX_MW_ARGS)), game)


# ------------------------------------------------------- the packing invariants


def test_no_trunk_is_over_capacity(reference_trunks):
    """The whole point. A Mk2 pipe carries 600 m3/min and a plan that puts 700 on one is
    not a plan."""
    assert reference_trunks.trunks
    for t in reference_trunks.trunks:
        assert t.rate <= t.capacity + 1e-6, (t.name, t.rate, t.capacity)
        assert t.used <= 1.0 + 1e-9


def test_every_extracted_unit_lands_on_exactly_one_trunk(reference_trunks, game, state):
    """Conservation. A packing that quietly dropped a node would read as a complete
    answer, which is the failure mode that matters here."""
    prepared = prepare(game, state, dict(REFERENCE_MAX_MW_ARGS))
    placeless_items = {name for name, _, _ in reference_trunks.placeless}
    for proc in prepared.solution.processes:
        if proc["kind"] != "extractor":
            continue
        item = next(i for i, r in proc["rates"].items() if r > 0)
        if game.item_name(item) in placeless_items:
            continue
        on_trunks = sum(t.rate for t in reference_trunks.trunks if t.item == item)
        planned = sum(
            p["rates"][item]
            for p in prepared.solution.processes
            if p["kind"] == "extractor" and p["rates"].get(item, 0) > 0
        )
        assert on_trunks == pytest.approx(planned, rel=1e-6)

    seen = [m.instance for t in reference_trunks.trunks for m in t.members]
    assert len(seen) == len(set(seen)), "a node was assigned to two trunks"


def test_the_crude_field_needs_six_pipes(reference_trunks):
    """Measured: 13 Oil Extractors at 250% make 3,450 m3/min, and 3,450/600 is 5.75, so
    six -- but only because the packing is geographic. Two pure nodes make exactly 600
    each and fill a pipe on their own."""
    crude = [t for t in reference_trunks.trunks if t.name == "Crude Oil"]
    assert len(crude) == 6
    assert sum(t.rate for t in crude) == pytest.approx(3450.0)
    assert sum(len(t.members) for t in crude) == 13
    full = [t for t in crude if len(t.members) == 1]
    assert full and all(t.used == pytest.approx(1.0) for t in full)


def test_a_node_that_alone_exceeds_a_line_still_gets_a_run(game, state):
    """Never dropped and never silently split: it is a real situation the player solves
    with a second pipe off one extractor, and hiding it would be the wrong help."""
    from satisfactory_mcp.domain.planning.trunks import TrunkMember, _split

    big = TrunkMember("solo", 0.0, 0.0, 0.0, "pure", 1200.0)
    small = TrunkMember("b", 100.0, 0.0, 0.0, "normal", 100.0)
    runs = _split([small, big, small], 600.0)
    assert [len(r) for r in runs] == [1, 1, 1]
    assert runs[1][0] is big


# ------------------------------------------------------- geography


def test_a_trunk_is_a_chain_not_a_blob(reference_trunks):
    """Pipes are laid end to end. The run length is the sum of consecutive hops, so a
    trunk whose members were assigned by proximity-to-centroid rather than by chaining
    would report a longer walk than it needs."""
    for t in reference_trunks.trunks:
        if len(t.members) < 3:
            continue
        chained = t.run_m
        reversed_order = (
            sum(
                math.dist((a.x, a.y, a.z), (b.x, b.y, b.z))
                for a, b in zip(t.members[::-1], t.members[::-1][1:], strict=False)
            )
            / 100.0
        )
        # A chain is symmetric; this pins that run_m measures hops, not a centroid.
        assert chained == pytest.approx(reversed_order)


def test_head_lands_on_one_trunk_not_the_whole_field(reference_trunks):
    """search_resource_nodes reports the Spire crude field spanning 40 m. That number is
    only useful once it is attached to a pipe: five of the six trunks are flat and one
    climbs the lot."""
    crude = [t for t in reference_trunks.trunks if t.name == "Crude Oil"]
    climbing = [t for t in crude if abs(t.lift_m) >= 1.0]
    assert len(climbing) == 1
    assert abs(climbing[0].lift_m) == pytest.approx(40, abs=1)
    assert all(abs(t.lift_m) < 1.0 for t in crude if t is not climbing[0])


def test_lift_is_signed_and_measured_inward(reference_trunks, game, state):
    """head_m is a span and cannot say which way. lift_m is the climb from the far end
    to the plant, so its SIGN is the whole answer: downhill needs no pumps."""
    for t in reference_trunks.trunks:
        assert t.head_m >= 0
        assert abs(t.lift_m) <= t.head_m + 1e-6


def test_the_destination_decides_which_end_is_far(game, state):
    """Reverse the destination and the chains reverse, so every lift flips sign. That is
    the argument doing real work rather than decorating the output."""
    prepared = prepare(game, state, dict(REFERENCE_MAX_MW_ARGS))
    nodes = [r for r in prepared.request.node_rows if r["resource"] == "Desc_LiquidOil_C"]
    west = (min(r["x"] for r in nodes) - 500_000, 0.0)
    east = (max(r["x"] for r in nodes) + 500_000, 0.0)
    a = {
        t.members[0].instance
        for t in plan_trunks(prepared, game, west).trunks
        if t.name == "Crude Oil"
    }
    b = {
        t.members[0].instance
        for t in plan_trunks(prepared, game, east).trunks
        if t.name == "Crude Oil"
    }
    assert a != b


# ------------------------------------------------------- what has no place


def test_water_extractors_get_no_trunk_and_are_named(reference_trunks):
    """Water comes from water volumes, which carry no node, purity or geometry anywhere
    this project can read. Dropping them silently would leave 9,200 m3/min unaccounted
    for in a table that otherwise conserves everything."""
    assert [name for name, _, _ in reference_trunks.placeless] == ["Water"]
    _, rate, count = reference_trunks.placeless[0]
    assert rate == pytest.approx(9200.0)
    assert count > 0
    assert not any(t.name == "Water" for t in reference_trunks.trunks)


def test_a_node_id_stays_pasteable(reference_trunks):
    """Truncating from the right turned BP_ResourceNode621 into "ode621" -- no longer
    something search_resource_nodes accepts back."""
    for t in reference_trunks.trunks:
        for m in t.members:
            assert not m.short.startswith("ode")
            assert m.short


# ------------------------------------------------------- the tool


def test_plan_layout_renders_trunks(game):
    out = srv.plan_layout(show="trunks", limit=12, **REFERENCE_MAX_MW_ARGS)
    assert not out.startswith("! ")
    assert "trunk\titem\tnodes" in out
    assert "UP 40m" in out
    assert "LOWER BOUND" in out


def test_a_climbing_trunk_gets_a_real_pump_count(game):
    """This used to assert that NO count was given, on the grounds that head-per-pump had
    no data behind it. It does: mDesignPressure, 20 m on a Mk1 pump and 50 m on a Mk2. The
    40 m Spire climb is therefore one Mk2."""
    out = srv.plan_layout(show="trunks", limit=12, **REFERENCE_MAX_MW_ARGS)
    assert "UP 40m (1x Pipeline Pump Mk.2)" in out
    assert "mDesignPressure" in out
    assert "LOWER bound" in out


def test_a_falling_trunk_needs_no_pumps(game):
    """The reason lift_m is signed. Fluid runs downhill unaided."""
    from satisfactory_mcp.domain.planning.trunks import Trunk, TrunkMember

    downhill = Trunk(
        item="x",
        name="X",
        carrier="pipe",
        capacity=600.0,
        members=[
            TrunkMember("a", 0.0, 0.0, 5000.0, "normal", 100.0),
            TrunkMember("b", 100.0, 0.0, 0.0, "normal", 100.0),
        ],
    )
    assert downhill.lift_m == pytest.approx(50.0)
    assert downhill.pumps(50.0) == 0


def test_pump_counts_round_up_per_pump_head(game):
    """A 51 m climb is two Mk2 pumps, not one and a bit."""
    from satisfactory_mcp.domain.planning.trunks import Trunk, TrunkMember

    def climb(metres: float) -> Trunk:
        return Trunk(
            item="x",
            name="X",
            carrier="pipe",
            capacity=600.0,
            members=[
                TrunkMember("a", 0.0, 0.0, 0.0, "normal", 1.0),
                TrunkMember("b", 100.0, 0.0, metres * 100.0, "normal", 1.0),
            ],
        )

    assert climb(50.0).pumps(50.0) == 1
    assert climb(51.0).pumps(50.0) == 2
    assert climb(50.0).pumps(20.0) == 3
    # An unknown pump head answers 0 rather than dividing by zero.
    assert climb(50.0).pumps(0.0) == 0


def test_a_belt_trunk_reports_no_head(game):
    """A belt does not care that its sulfur climbs 218 m, and printing a number there
    invites a pump that cannot exist."""
    out = srv.plan_layout(show="trunks", limit=12, **REFERENCE_MAX_MW_ARGS)
    rows = [line.split("\t") for line in out.splitlines() if line.startswith("T")]
    assert rows
    for row in rows:
        if row[1].startswith(("Coal", "Sulfur")):
            assert row[6] == "", row

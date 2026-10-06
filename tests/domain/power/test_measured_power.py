"""Measured power, water volumes, and pushing back on "not modelled".

`power_report` said "fuel supply and uptime are not modelled here" and reported pure
nameplate. The uptime was in the projection all along -- the 300 s productivity monitor,
on 520 of 566 records -- and the gap it hid was not small: nameplate draw 6,839 MW against
a measured 1,516, so headroom read 711 MW where roughly 6,034 was free. `commission_plan`
sized a startup against the wrong one.

OQ5 said water pumps "carry no node, purity or geometry" and could not be matched to
anything. The volume's SHAPE really is level geometry and absent, but its IDENTITY is in
every pump's `mExtractableResource`, which the sidecar had been storing in `node`.
"""

from __future__ import annotations

import pytest

from satisfactory_mcp import server as srv
from tests.support.reference_world import REFERENCE_FIELD

pytestmark = pytest.mark.integration


# ------------------------------------------------------------ measured power


def test_measured_draw_never_exceeds_nameplate(live):
    """Utilisation is a fraction, so weighting by it can only reduce. If this inverted,
    the weighting would be being applied the wrong way round."""
    pw = live.power_report()
    assert pw["measured_draw_mw"] <= pw["draw_mw"] + 1e-6
    assert pw["measured_headroom_mw"] >= pw["headroom_mw"] - 1e-6
    assert 0.0 <= pw["utilisation"] <= 1.0


def test_an_unmonitored_machine_is_charged_in_full(game):
    """Unknown utilisation must not read as idle, or a factory the save cannot see would
    look free. Charging it fully keeps the measured figure conservative."""
    from satisfactory_mcp.domain.world.state import WorldState

    machines = [
        {"instance": "L:P.a", "cls": "Build_ConstructorMk1_C", "recipe": None},
        {
            "instance": "L:P.b",
            "cls": "Build_ConstructorMk1_C",
            "recipe": None,
            "uptime": {"window_s": 300.0, "produce_s": 0.0},
        },
    ]
    st = WorldState(projection={"machines": machines}, game=game)
    pw = st.power_report()
    rated = game.buildings["Build_ConstructorMk1_C"].power_at(1.0)
    assert pw["draw_mw"] == pytest.approx(2 * rated)
    # The monitored one produced nothing, so only the UNMONITORED one is charged.
    assert pw["measured_draw_mw"] == pytest.approx(rated)
    assert (pw["monitored"], pw["unmonitored"]) == (1, 1)


def test_generation_is_capacity_on_both_figures(live):
    """Generators burn to meet demand rather than at a rate of their own, so weighting
    them by uptime would double-count the same idleness already seen on the draw side."""
    pw = live.power_report()
    assert pw["generation_mw"] > 0
    assert pw["measured_headroom_mw"] == pytest.approx(pw["generation_mw"] - pw["measured_draw_mw"])


def test_the_tool_shows_both_and_says_which_is_safe(game):
    out = srv.power_report()
    assert "headroom_MW_nameplate" in out
    assert "headroom_MW_measured" in out
    assert "SAFE bound" in out
    assert "not modelled" not in out


def test_commissioning_defaults_to_measured_but_names_the_safe_bound(game, live):
    """Measured is the default: nameplate leaves the reference save no startup order at
    all. Energising a block can un-starve idle machines, though, so the text still names
    the nameplate figure as the safe bound and how to plan against it."""
    kw = dict(
        objective="max_mw",
        sources=list(REFERENCE_FIELD),
        exports=["MW"],
        extractor_clocks=[1, 1.5, 2, 2.5],
        limit=4,
    )
    out = srv.commission_plan(**kw)
    pw = live.power_report()
    assert "source: measured from the save" in out
    if pw["headroom_mw"] < pw["measured_headroom_mw"]:
        assert "safe bound" in out
    plate = srv.commission_plan(headroom_mw=max(pw["headroom_mw"], 1.0), **kw)
    assert "source: given by caller" in plate


def test_a_bigger_headroom_really_does_mean_fewer_waves(monkeypatch, state):
    """The reason the safe-bound note is worth printing at all.

    On the reference world, where the nameplate headroom clears the minimum slice: below it
    there is no order at all, and zero waves is not fewer.
    """
    from satisfactory_mcp.interfaces.mcp.tools import planning

    monkeypatch.setattr(planning, "_state", lambda save=None, world=None, as_of=None: state)
    kw = dict(
        objective="max_mw",
        sources=list(REFERENCE_FIELD),
        exports=["MW"],
        extractor_clocks=[1, 1.5, 2, 2.5],
        limit=4,
    )
    safe = srv.commission_plan(headroom_mw=state.power_report()["headroom_mw"], **kw)
    real = srv.commission_plan(**kw)

    def waves(out: str) -> int:
        return int(out.split(" wave(s)")[0].rsplit(", ", 1)[1])

    assert 0 < waves(real) < waves(safe)


# ------------------------------------------------------------ starved generators


def _coal_plant(n: int, coal: int, water: int, produce_s: float | None) -> dict:
    """One coal generator holding a given hopper and pipe buffer.

    The two are in the SAME inventory, which is the whole difficulty: a plant with coal and
    no water is out of water, and nothing about "is this inventory empty" can see it.
    """
    record = {
        "instance": f"L:P.Build_GeneratorCoal_C_{n}",
        "cls": "Build_GeneratorCoal_C",
        "fuel": "Desc_Coal_C",
        "buffers": {"fuel": {"items": {"Desc_Coal_C": coal, "Desc_Water_C": water}, "slots": 2}},
    }
    if produce_s is not None:
        record["uptime"] = {"window_s": 300.0, "produce_s": produce_s}
    return record


def _ledger(game, generators: list[dict]):
    from satisfactory_mcp.domain.world.state import WorldState

    return WorldState(projection={"generators": generators}, game=game).power_report()


def test_a_dry_water_pipe_is_found_behind_a_full_coal_hopper(game):
    """The failure this exists for. `power_report` counted a plant with a broken water pipe
    as full capacity, and the empty-tank test it could have borrowed asks whether the fuel
    inventory is empty -- which a hopper with 100 coal in it is not."""
    pw = _ledger(game, [_coal_plant(1, coal=100, water=0, produce_s=0.0)])
    assert pw["generation_mw"] == pytest.approx(75.0), "still counted as built capacity"
    assert pw["starved_generation_mw"] == pytest.approx(75.0)
    (row,) = pw["starved_generators"]
    assert row["missing"] == ["Water"], "names the input, not just the plant"
    assert row["instance"] == "Build_GeneratorCoal_C_1"


def test_a_load_following_generator_is_not_called_starved(game):
    """A generator burns to meet demand, so a full plant on a quiet grid legitimately reads
    below 1.0. Leading with uptime would condemn every healthy plant on the map."""
    pw = _ledger(game, [_coal_plant(1, coal=100, water=50000, produce_s=12.0)])
    assert pw["starved_generators"] == []
    assert pw["starved_generation_mw"] == 0.0


def test_uptime_only_corroborates_and_never_accuses_on_its_own(game):
    """Zero uptime with full tanks is a grid with nothing to power, not a supply fault; and
    an empty tank with no monitor has no corroboration, so it is left alone rather than
    accused. Both are the empty-tank test leading."""
    quiet = _ledger(game, [_coal_plant(1, coal=100, water=50000, produce_s=0.0)])
    unreadable = _ledger(game, [_coal_plant(2, coal=0, water=0, produce_s=None)])
    assert quiet["starved_generators"] == []
    assert unreadable["starved_generators"] == []


def test_the_tool_names_which_plant_and_what_it_is_out_of(game, monkeypatch):
    from satisfactory_mcp.domain.world.state import WorldState
    from satisfactory_mcp.interfaces.mcp.tools import world as wtools

    projection = {
        "generators": [
            _coal_plant(1, coal=100, water=0, produce_s=0.0),
            _coal_plant(2, coal=100, water=50000, produce_s=300.0),
        ],
        "header": {"save_identifier": "TEST-starved", "session_name": "t"},
    }
    st = WorldState(projection=projection, game=game)
    monkeypatch.setattr(wtools, "_state", lambda save=None, world=None, as_of=None: st)
    out = srv.power_report()
    assert "generation_MW_starved=75" in out
    assert "## starved generators" in out
    assert "Build_GeneratorCoal_C_1" in out
    assert "Build_GeneratorCoal_C_2" not in out, "the healthy one is not accused"
    assert "Water" in out


# ------------------------------------------------------------ water volumes


def test_water_pumps_group_by_the_body_they_draw_from(live):
    """OQ5 concluded they could not be matched to anything. The volume object is level
    geometry and is not in the save -- so its shape and capacity are genuinely unknowable
    -- but mExtractableResource names it, and that is enough to count separate shorelines."""
    water = live.water_volumes()
    if not water["pumps"]:
        pytest.skip("no water extractors built")
    assert sum(water["volumes"].values()) == water["pumps"]
    assert all(k.startswith("FGWaterVolume") for k in water["volumes"])


def test_sea_level_is_measured_rather_than_asserted(live):
    """Every pump has to be at it, so the pumps ARE the measurement. It turns "water must
    be drawn at sea level" from a rule of thumb into a number deck ordering can check."""
    water = live.water_volumes()
    if not water["pumps"]:
        pytest.skip("no water extractors built")
    assert water["sea_level_m"] is not None
    # They are all at the same height, which is what makes it sea level and not a mean.
    assert water["sea_level_span_m"] < 1.0


def test_the_water_warning_reports_the_bodies_and_the_level(game):
    out = srv.plan_factory(
        sources=list(REFERENCE_FIELD),
        objective="max_mw",
        exports=["MW"],
        extractor_clocks=[1, 1.5, 2, 2.5],
        limit=2,
    )
    line = next(x for x in out.splitlines() if "Water Extractor(s)" in x)
    assert "distinct water bod" in line
    assert "sea level" in line
    # What is genuinely unknown is still said plainly.
    assert "shape is level geometry and is not in the save" in line


def test_asking_for_water_nodes_does_not_report_water_as_absent(live):
    """The tool answered "0 free and reachable" for the resource that decides aluminium and
    nuclear, on a map ringed by lakes. Every row it CAN return is a fracking satellite, so
    the number was true and the answer was not: no node is free because open water has no
    node, and the bodies already being pumped are what was actually asked for."""
    if not live.water_volumes()["pumps"]:
        pytest.skip("no water extractors built")
    out = srv.search_resource_nodes(resource="Water", limit=4)
    assert "open water carries NO NODE" in out
    assert "fracking satellite" in out
    assert "## open water" in out
    assert "pumps built=" in out and "sea level=" in out
    # The one thing that is genuinely unknown stays unknown.
    assert "SHAPE is level geometry and is not in the save" in out


def test_a_dry_land_resource_gets_no_water_block(live):
    """Water is an exception to the node table, not a preamble on every answer."""
    out = srv.search_resource_nodes(resource="Coal", limit=4)
    assert "## open water" not in out
    assert "NO NODE" not in out

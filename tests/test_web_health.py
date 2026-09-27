"""``/api/factories/health`` and ``/api/power/circuits``: the side panel's two payloads.

``importorskip`` at module scope: ``fastapi`` lives in the optional ``web`` extra. Both
loaders are injected by the ``client`` fixture, so nothing here reads a ``.sav``.
"""

from __future__ import annotations

import pytest

fastapi = pytest.importorskip("fastapi")

from satisfactory_mcp.domain.factories.health import ACTIONABLE, OK, STATES, assess


def test_every_named_factory_gets_a_health_row(client, state):
    body = client.get("/api/factories/health").json()
    assert body["states"] == list(STATES)
    assert body["actionable_states"] == list(ACTIONABLE)
    assert body["ok_states"] == [s for s in STATES if s in OK]
    assert set(body["ok_states"]) == OK
    assert {r["name"] for r in body["factories"]} == {x.name for x in state.labels.labels}


def test_a_health_row_matches_what_assess_says_about_that_factory(client, state):
    body = client.get("/api/factories/health").json()
    if not body["factories"]:
        pytest.skip("the fixture world has no named factories")
    alive = set(state.graph.machines())
    by_name = {x.name: x for x in state.labels.labels}
    for row in body["factories"]:
        standing = [m for m in by_name[row["name"]].anchors if m in alive]
        report = assess(row["name"], standing, state.game, state.projection, state.graph)
        assert row["alive"] == len(standing)
        assert row["machines"] == len(report.machines)
        assert {s["state"]: s["count"] for s in row["states"]} == {
            s: n for s, n in report.by_state.items() if n
        }
        assert row["unwired"] == len(report.unwired)
        assert row["actionable"] == sum(report.by_state[s] for s in ACTIONABLE)
        assert len(row["worst"]) <= 8
        for issue in row["worst"]:
            assert issue["state"] in STATES
            if issue["x_m"] is not None:
                assert abs(issue["x_m"]) < 5000, "metres, not centimetres"


def test_factories_are_ordered_worst_first(client):
    rows = client.get("/api/factories/health").json()["factories"]
    todo = [r["actionable"] for r in rows]
    assert todo == sorted(todo, reverse=True)


def test_the_sweep_counts_blocked_machines_as_todo_like_the_route(client, state, monkeypatch):
    """The MCP sweep's ``todo`` and the route's ``actionable`` are one number per factory."""
    from satisfactory_mcp.interfaces.mcp.tools import factories as tool

    rows = client.get("/api/factories/health").json()["factories"]
    if not rows:
        pytest.skip("the fixture world has no named factories")
    monkeypatch.setattr(tool, "_state", lambda save=None, world=None, as_of=None: state)
    out = tool.factory_health(factory="all", limit=500)
    todo = {}
    for line in out.splitlines():
        cells = line.split("\t")
        if len(cells) == 11 and cells[0] != "factory":
            todo[cells[0]] = int(cells[-1] or 0)
    assert todo == {r["name"]: r["actionable"] for r in rows}


def test_the_world_ledger_is_power_report(client, state):
    body = client.get("/api/power/circuits").json()
    report = state.power_report()
    assert body["world"]["generation_mw"] == pytest.approx(report["generation_mw"], abs=0.1)
    assert body["world"]["measured_draw_mw"] == pytest.approx(report["measured_draw_mw"], abs=0.1)
    assert body["paused"] == report["paused_count"]
    assert len(body["starved"]) == len(report["starved_generators"])


def test_circuits_split_the_generation_without_losing_or_inventing_any(client, state):
    body = client.get("/api/power/circuits").json()
    circuits = body["circuits"]
    assert circuits, "the fixture world is wired"
    assert [c["index"] for c in circuits] == list(range(len(circuits)))
    total = sum(c["ledger"]["generation_mw"] for c in circuits)
    assert total <= body["world"]["generation_mw"] + 0.1 * len(circuits)
    for c in circuits:
        led = c["ledger"]
        assert led["headroom_mw"] == pytest.approx(led["generation_mw"] - led["draw_mw"], abs=0.2)
        if c["bbox_m"] is not None:
            x_min, y_min, x_max, y_max = c["bbox_m"]
            assert x_min <= c["centroid_m"][0] <= x_max
            assert y_min <= c["centroid_m"][1] <= y_max


def test_the_dark_machines_are_the_ones_assess_finds(client, state):
    body = client.get("/api/power/circuits").json()
    report = assess("world", state.graph.machines(), state.game, state.projection, state.graph)
    assert [r["instance"] for r in body["unwired"]] == sorted(report.unwired)
    assert [r["instance"] for r in body["no_generator"]] == sorted(report.no_generator)
    for circuit in body["circuits"]:
        if circuit["ledger"]["generation_mw"] == 0 and circuit["consumers"]:
            assert body["no_generator"], "a consumer circuit with no source is a dark machine"


def test_an_unreadable_save_is_an_error_not_an_empty_panel(client, monkeypatch):
    def boom(save=None, world=None):
        raise RuntimeError("sidecar produced no output")

    monkeypatch.setattr(client.app.state, "load_state", boom)
    for path in ("/api/factories/health", "/api/power/circuits"):
        r = client.get(path)
        assert r.status_code == 404
        assert "could not read save" in r.json()["error"]


def test_the_world_ledger_is_the_sum_of_the_circuits(client, state):
    """What stands on no wire draws from nothing, so it is off every ledger, the world's too."""
    from satisfactory_mcp.domain.power.report import PowerLedger

    body = client.get("/api/power/circuits").json()
    world, circuits = body["world"], body["circuits"]
    for key in ("generation_mw", "draw_mw", "measured_draw_mw", "headroom_mw"):
        total = sum(c["ledger"][key] for c in circuits)
        assert world[key] == pytest.approx(total, abs=0.1 * len(circuits))
    everything = PowerLedger(projection=state.projection, game=state.game).power_report()
    off = body["off_grid"]
    assert world["draw_mw"] + off["draw_mw"] == pytest.approx(everything["draw_mw"], abs=0.2)
    assert off["consumers"] > 0, "the fixture world has machines on no wire"


def test_a_machine_ref_names_the_circuit_it_stands_on(client, state):
    body = client.get("/api/power/circuits").json()
    indices = {c["index"] for c in body["circuits"]}
    names = {x.name for x in state.labels.labels}
    for r in body["unwired"] + body["no_generator"] + body["unwired_generators"]:
        assert r["factory"] is None or r["factory"] in names
        assert r["region"] is None or r["region"]["name"]
    assert all(r["circuit"] is None for r in body["unwired"])
    assert all(r["circuit"] in indices for r in body["no_generator"])
    for c in body["circuits"]:
        assert c["factory_count"] == len(c["factories"])


def test_worst_actionable_holds_only_machines_needing_action(client):
    rows = client.get("/api/factories/health").json()["factories"]
    if not rows:
        pytest.skip("the fixture world has no named factories")
    for row in rows:
        assert all(i["state"] in ACTIONABLE for i in row["worst_actionable"])
        assert len(row["worst_actionable"]) == min(8, row["actionable"])


def _one_circuit(game, generators, machines=(), query=""):
    """A hand-built world: every record wired to one pole."""
    from fastapi.testclient import TestClient

    from satisfactory_mcp.domain.world.state import WorldState
    from satisfactory_mcp.interfaces.web.app import create_app

    records = [*generators, *machines]
    actors = ["Build_PowerPoleMk1_C_1"] + [r["instance"] for r in records]
    projection = {
        "generators": list(generators),
        "machines": list(machines),
        "graph": {"actors": actors, "power": [[0, i] for i in range(1, len(actors))]},
    }
    st = WorldState(projection=projection, game=game)
    app = create_app(state_loader=lambda save=None, world=None: st, game_loader=lambda: game)
    return TestClient(app).get("/api/power/circuits" + query).json()


def test_a_standing_biomass_burner_is_rated_from_game_data(game):
    """The save calls it Build_GeneratorBiomass_C; the dump rates it under another class."""
    burner = {"instance": "Build_GeneratorBiomass_C_1", "cls": "Build_GeneratorBiomass_C"}
    body = _one_circuit(game, [burner], query="?biomass=include")
    rated = game.buildings["Build_GeneratorBiomass_Automated_C"].power_production_mw
    assert body["circuits"][0]["ledger"]["generation_mw"] == pytest.approx(rated)
    assert body["unmodellable"] == []


BURNERS = [
    {"instance": "Build_GeneratorBiomass_C_1", "cls": "Build_GeneratorBiomass_C"},
    {"instance": "Build_GeneratorIntegratedBiomass_C_1", "cls": "Build_GeneratorIntegratedBiomass_C"},
    {"instance": "Build_GeneratorCoal_C_1", "cls": "Build_GeneratorCoal_C"},
]


def test_biomass_burners_are_left_out_of_headroom_by_default(game):
    """Decided 2026-09-27: hand-fed burners are not a power plant. Absent means exclude."""
    burner = game.buildings["Build_GeneratorBiomass_Automated_C"].power_production_mw
    coal = game.buildings["Build_GeneratorCoal_C"].power_production_mw
    default = _one_circuit(game, BURNERS)
    assert _one_circuit(game, BURNERS, query="?biomass=exclude") == default
    for led in (default["world"], default["circuits"][0]["ledger"]):
        assert led["generation_mw"] == pytest.approx(coal)
        assert led["headroom_mw"] == pytest.approx(coal)
        assert led["measured_headroom_mw"] == pytest.approx(coal)
        assert led["biomass_mw"] == pytest.approx(burner)
        assert led["biomass_generators"] == 1
    assert default["unmodellable"] == ["Build_GeneratorIntegratedBiomass_C"]
    assert [g["name"] for g in default["generators"]] == [game.buildings["Build_GeneratorCoal_C"].name]


def test_the_hub_burners_read_the_same_in_both_modes(game):
    """The HUB's built-in burners have no rating, so they are unmodellable either way and
    never a biomass burner left out."""
    hub = [
        {"instance": f"Build_GeneratorIntegratedBiomass_C_{i}", "cls": "Build_GeneratorIntegratedBiomass_C"}
        for i in (1, 2)
    ]
    off = _one_circuit(game, hub, query="?biomass=exclude")
    on = _one_circuit(game, hub, query="?biomass=include")
    assert off == on
    assert off["circuits"][0]["unmodellable"] == ["Build_GeneratorIntegratedBiomass_C"]
    assert (off["world"]["biomass_mw"], off["world"]["biomass_generators"]) == (0, 0)


def test_no_biomass_line_at_zero_mw():
    from satisfactory_mcp.domain.power.report import biomass_note

    assert biomass_note({"biomass_generators": 2, "biomass_mw": 0.0}) == ""
    assert biomass_note({"biomass_generators": 0, "biomass_mw": 0.0}) == ""
    assert "+30 MW biomass not counted" in biomass_note({"biomass_generators": 1, "biomass_mw": 30.0})


def test_the_vanilla_save_counts_only_its_rated_burners(game):
    """Vanilla: 11 wired Biomass Burners at 330 MW, plus the HUB's two unrated ones."""
    from satisfactory_mcp.core.saveio.projection import SaveError
    from satisfactory_mcp.domain.world.state import load_state

    try:
        st = load_state(game, world="Vanilla")
    except SaveError as exc:
        pytest.skip(f"the Vanilla save is not on this machine: {exc}")
    off = st.power_report(biomass=False)
    on = st.power_report(biomass=True)
    assert (off["biomass_generators"], off["biomass_mw"]) == (11, pytest.approx(330.0))
    assert off["unmodellable"] == on["unmodellable"] == ["Build_GeneratorIntegratedBiomass_C"]


def test_biomass_include_counts_every_burner_and_reports_nothing_left_out(game):
    burner = game.buildings["Build_GeneratorBiomass_Automated_C"].power_production_mw
    coal = game.buildings["Build_GeneratorCoal_C"].power_production_mw
    body = _one_circuit(game, BURNERS, query="?biomass=include")
    led = body["world"]
    assert led["generation_mw"] == pytest.approx(coal + burner)
    assert led["headroom_mw"] == pytest.approx(coal + burner)
    assert (led["biomass_mw"], led["biomass_generators"]) == (0, 0)
    assert body["unmodellable"] == ["Build_GeneratorIntegratedBiomass_C"]


def test_an_unknown_biomass_value_is_refused(game):
    from fastapi.testclient import TestClient

    from satisfactory_mcp.domain.world.state import WorldState
    from satisfactory_mcp.interfaces.web.app import create_app

    st = WorldState(projection={}, game=game)
    app = create_app(state_loader=lambda save=None, world=None: st, game_loader=lambda: game)
    assert TestClient(app).get("/api/power/circuits?biomass=maybe").status_code == 422


@pytest.mark.parametrize("counted", [False, True])
def test_the_page_and_chat_agree_on_headroom_either_way(client, state, counted, monkeypatch):
    """Same save, same setting: /api/power/circuits, /api/summary and power_report print
    one set of figures, with biomass left out or counted."""
    from satisfactory_mcp.interfaces.mcp.tools import world as tool
    from satisfactory_mcp.presenters.text import primitives as render

    monkeypatch.setattr(tool, "_state", lambda *a, **k: state)

    query = "?biomass=" + ("include" if counted else "exclude")
    report = state.power_report(biomass=counted)
    led = client.get("/api/power/circuits" + query).json()["world"]
    summary = client.get("/api/summary" + query).json()["power"]
    text = tool.power_report(biomass=counted)
    for key, label in (
        ("generation_mw", "generation_MW"),
        ("headroom_mw", "headroom_MW_nameplate"),
        ("measured_headroom_mw", "headroom_MW_measured"),
    ):
        assert led[key] == pytest.approx(report[key], abs=0.1)
        assert summary[key] == pytest.approx(report[key], abs=1e-6)
        assert f"{label}={render.num(report[key])}" in text
    assert led["biomass_mw"] == pytest.approx(report["biomass_mw"], abs=0.1)
    assert ("biomass not counted" in text) == bool(report["biomass_generators"])


def test_the_reference_save_reads_differently_with_and_without_biomass(state):
    without = state.power_report()
    with_ = state.power_report(biomass=True)
    assert without == state.power_report(biomass=False)
    assert with_["generation_mw"] == pytest.approx(without["generation_mw"] + without["biomass_mw"])
    assert with_["headroom_mw"] - without["headroom_mw"] == pytest.approx(without["biomass_mw"])
    assert with_["draw_mw"] == pytest.approx(without["draw_mw"])


def test_a_generator_on_no_wire_is_not_a_machine_on_no_wire(game):
    from satisfactory_mcp.domain.world.state import WorldState

    burner = {"instance": "Build_GeneratorCoal_C_1", "cls": "Build_GeneratorCoal_C"}
    projection = {"generators": [burner], "graph": {"actors": [], "power": []}}
    st = WorldState(projection=projection, game=game)
    report = assess("world", st.graph.machines(), game, projection, st.graph)
    assert report.unwired == []
    assert report.unwired_generators == ["Build_GeneratorCoal_C_1"]


def test_an_empty_hand_fed_generator_says_no_fuel_loaded(game):
    coal = {
        "instance": "Build_GeneratorCoal_C_1",
        "cls": "Build_GeneratorCoal_C",
        "buffers": {"fuel": {"items": {}}},
        "uptime": {"window_s": 300.0, "produce_s": 0.0},
    }
    body = _one_circuit(game, [coal])
    assert [s["cause"] for s in body["starved"]] == ["no fuel loaded"]


def test_the_off_grid_counts_are_the_unwired_lists(client):
    body = client.get("/api/power/circuits").json()
    off = body["off_grid"]
    assert off["consumers"] == len(body["unwired"])
    assert off["generators"] == len(body["unwired_generators"])
    for c in body["circuits"]:
        led = c["ledger"]
        assert c["consumers"] >= led["monitored"] + led["unmonitored"] + led["paused"]


def test_a_paused_machine_on_no_wire_is_listed_and_counted_but_draws_nothing(game):
    from fastapi.testclient import TestClient

    from satisfactory_mcp.domain.world.state import WorldState
    from satisfactory_mcp.interfaces.web.app import create_app

    def smelter(n, **extra):
        return {"instance": f"Build_SmelterMk1_C_{n}", "cls": "Build_SmelterMk1_C", **extra}

    projection = {
        "machines": [smelter(1), smelter(2, paused=True)],
        "graph": {"actors": [], "power": []},
    }
    st = WorldState(projection=projection, game=game)
    app = create_app(state_loader=lambda save=None, world=None: st, game_loader=lambda: game)
    body = TestClient(app).get("/api/power/circuits").json()
    rated = game.buildings["Build_SmelterMk1_C"].power_at(1.0)
    assert body["off_grid"]["consumers"] == len(body["unwired"]) == 2
    assert body["off_grid"]["paused"] == 1
    assert body["off_grid"]["draw_mw"] == pytest.approx(rated, abs=0.1)

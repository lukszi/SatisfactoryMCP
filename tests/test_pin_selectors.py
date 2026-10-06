"""``pin:N`` in every grammar that takes one (docs/planner-p3_contract.md §7), and its refusals."""

from __future__ import annotations

import pytest

from satisfactory_mcp import config
from satisfactory_mcp.domain.factories import select as gsel
from satisfactory_mcp.domain.planning.planlog import Actor, PlanLog
from satisfactory_mcp.domain.planning.recall import plan_ref, recall_plan
from satisfactory_mcp.domain.session import pins
from satisfactory_mcp.domain.spatial import nodes as nodes_mod
from satisfactory_mcp.domain.spatial.origin import resolve_origin
from satisfactory_mcp.domain.spatial.select import select_nodes

WORLD = "X2faPVKjX06VaRzClNv5KQ"
CHAT = Actor("chat", "claude-code", 4242)
RIP = "Reinforced Iron Plate"
HMF = {"objective": "min_machines", "exports": [RIP], "export_minimums": {RIP: 5}}
OIL = "BP_ResourceNode26_99"


@pytest.fixture
def world(tmp_path, monkeypatch, labelled):
    for name in ("plans_dir", "pins_dir"):
        root = tmp_path / name
        root.mkdir()
        monkeypatch.setattr(config, name, lambda root=root: root)
    return labelled


def _nodes():
    return nodes_mod.load_nodes().nodes


def _machine(st) -> str:
    return st.projection["machines"][0]["instance"].rsplit(".", 1)[-1]


def test_every_located_kind_is_a_place(world):
    point, _ = pins.create(world, "point", {"x_m": 100.0, "y_m": -200.0}, label="spot")
    node, _ = pins.create(world, "node", {"node": OIL})
    machine, _ = pins.create(world, "machine", {"machine": _machine(world)})
    factory, _ = pins.create(world, "factory", {"factory": world.labels.labels[0].name})
    made = PlanLog(WORLD).create("sited", HMF, actor=CHAT, siting={"origin_m": [5.0, 6.0, 0.0]})
    plan, _ = pins.create(world, "plan", {"plan": made.key})
    centre, echo = resolve_origin(world, "pin:1")
    assert centre == (10000.0, -20000.0) and echo == "pin:1 = 100,-200 (“spot” point)"
    centre, echo = resolve_origin(world, f"PIN:{node['n']}")
    assert echo.startswith(f"pin:{node['n']} = node:{OIL} (Crude Oil, ")
    for pin in (machine, factory, plan):
        centre, echo = resolve_origin(world, pin["id"])
        assert centre == (pin["x_m"] * 100.0, pin["y_m"] * 100.0)
        assert echo.startswith(f"{pin['id']} = {pin['selector']}")
    assert point["n"] == 1


def test_a_point_is_spelt_like_its_selector(world):
    point, _ = pins.create(world, "point", {"x_m": -7.9, "y_m": -5.5})
    assert point["selector"] == "-7.9,-5.5" and point["text"] == "point x -8, y -6 m"


def test_a_place_refuses_what_has_no_place(world):
    made = PlanLog(WORLD).create("rip", HMF, actor=CHAT)
    plan, _ = pins.create(world, "plan", {"plan": made.key})
    PlanLog(WORLD).push(
        made.key,
        1,
        [{"op": "add", "field": "required", "member": "Recipe_IronPlate_C"}],
        actor=CHAT,
    )
    process, _ = pins.create(world, "process", {"plan": made.key, "recipe": "Recipe_IronPlate_C"})
    with pytest.raises(ValueError, match=f"^{plan['id']} is a plan with no site$"):
        resolve_origin(world, plan["id"])
    with pytest.raises(ValueError, match="is a process: it has no place on the map"):
        resolve_origin(world, process["id"])
    with pytest.raises(ValueError, match=r"pin:9 does not exist \(pins run to pin:2\)"):
        resolve_origin(world, "pin:9")
    with pytest.raises(ValueError, match="needs a readable save"):
        resolve_origin(None, "pin:1")


def test_near_a_pin_works_in_both_selector_languages(world):
    machine, _ = pins.create(world, "machine", {"machine": _machine(world)})
    around = gsel.select_machines([f"near:{machine['id']}@50"], world)
    assert _machine(world) in around
    node, _ = pins.create(world, "node", {"node": OIL})
    sel = select_nodes([f"near:{node['id']}@10"], _nodes(), st=world)
    assert [n["instance"].rsplit(".", 1)[-1] for n in sel.nodes] == [OIL]
    assert sel.described[0].startswith(f"within 10m of {node['id']} = node:{OIL}")


def test_node_sources_take_node_and_field_pins(world):
    node, _ = pins.create(world, "node", {"node": OIL})
    sel = select_nodes([node["id"]], _nodes(), st=world)
    assert [n["instance"].rsplit(".", 1)[-1] for n in sel.nodes] == [OIL] and not sel.errors
    assert sel.described == [
        f"pin:{node['n']} = node:{OIL} (Crude Oil, {sel.nodes[0]['purity']}) (1 nodes)"
    ]
    iron = next(n for n in _nodes() if n["resource"] == "Desc_OreIron_C")
    field, _ = pins.create(world, "field", {"node": iron["instance"]})
    sel = select_nodes([field["id"], "resource:Desc_OreIron_C"], _nodes(), st=world)
    assert sorted(n["instance"].rsplit(".", 1)[-1] for n in sel.nodes) == field["ref"]["nodes"]


def test_node_sources_refuse_other_kinds_in_words(world):
    point, _ = pins.create(world, "point", {"x_m": 1.0, "y_m": 2.0})
    machine, _ = pins.create(world, "machine", {"machine": _machine(world)})
    sel = select_nodes([point["id"], machine["id"]], _nodes(), st=world)
    assert sel.nodes == []
    assert sel.errors == [
        f"pin:{point['n']} is a point: write near:pin:{point['n']}@<radius_m>",
        f"pin:{machine['n']} is a machine: it cannot stand for resource nodes",
    ]


def test_machine_selects_take_machine_and_factory_pins_and_negate_them(world):
    inst = _machine(world)
    machine, _ = pins.create(world, "machine", {"machine": inst})
    label = world.labels.labels[0]
    factory, _ = pins.create(world, "factory", {"factory": label.name})
    assert gsel.select_machines([machine["id"]], world) == [inst]
    alive = set(world.graph.machines())
    named = sorted(m for m in label.anchors if m in alive)
    assert gsel.select_machines([factory["id"]], world) == named
    without = gsel.select_machines(["all", f"-{machine['id']}"], world)
    assert inst not in without and len(without) == len(alive) - 1
    assert gsel.pin_notes([f"-{machine['id']}", "all"], world) == [
        f"{machine['id']} = machine:{inst} ({machine['text'].removeprefix('machine ')})"
    ]
    node, _ = pins.create(world, "node", {"node": OIL})
    with pytest.raises(gsel.SelectorError, match="is a node: it cannot stand for machines"):
        gsel.select_machines([node["id"]], world)


def test_plan_takes_a_plan_pin_and_refuses_others(world):
    made = PlanLog(WORLD).create("rip", HMF, actor=CHAT)
    plan, _ = pins.create(world, "plan", {"plan": made.key})
    point, _ = pins.create(world, "point", {"x_m": 1.0, "y_m": 2.0})
    assert plan_ref(world, plan["id"]) == (made.key, f"{plan['id']} = rip (plan “rip”)")
    assert plan_ref(world, "rip") == ("rip", "")
    kwargs, name, notes = recall_plan(world, plan["id"], {})
    assert name == "rip" and kwargs["export_minimums"] == {RIP: 5.0}
    assert f"{plan['id']} = rip (plan “rip”)" in notes
    with pytest.raises(KeyError, match="is a point: it cannot stand for a plan"):
        recall_plan(world, point["id"], {})


def test_deleted_and_gone_pins_refuse_everywhere(world):
    made = PlanLog(WORLD).create("rip", HMF, actor=CHAT)
    plan, _ = pins.create(world, "plan", {"plan": made.key})
    node, _ = pins.create(world, "node", {"node": OIL})
    pins.drop(WORLD, node["n"], 1)
    sel = select_nodes([node["id"]], _nodes(), st=world)
    assert sel.errors == [f"{node['id']} was deleted"] and sel.nodes == []
    with pytest.raises(ValueError, match=f"{node['id']} was deleted"):
        resolve_origin(world, node["id"])
    PlanLog(WORLD).push(made.key, 1, [{"op": "forget"}], actor=CHAT)
    with pytest.raises(KeyError, match=f"{plan['id']} is gone: plan forgotten"):
        recall_plan(world, plan["id"], {})


def test_canonical_rewrites_what_a_stored_plan_would_hold(world):
    made = PlanLog(WORLD).create("rip", HMF, actor=CHAT)
    PlanLog(WORLD).push(
        made.key,
        1,
        [{"op": "add", "field": "required", "member": "Recipe_IronPlate_C"}],
        actor=CHAT,
    )
    process, _ = pins.create(world, "process", {"plan": made.key, "recipe": "Recipe_IronPlate_C"})
    iron = next(n for n in _nodes() if n["resource"] == "Desc_OreIron_C")
    field, _ = pins.create(world, "field", {"node": iron["instance"]})
    point, _ = pins.create(world, "point", {"x_m": 100.0, "y_m": -200.5})
    sources, said = pins.canonical(
        world, "sources", [field["id"], f"near:{point['id']}@200", "region:Somewhere"]
    )
    assert sources == [
        *(f"node:{m}" for m in field["ref"]["nodes"]),
        "near:100,-200.5@200",
        "region:Somewhere",
    ]
    assert len(said) == 2
    required, _ = pins.canonical(world, "required", [process["id"], "Recipe_Other_C"])
    assert required == ["Recipe_IronPlate_C", "Recipe_Other_C"]
    banned, _ = pins.canonical(world, "exclude_recipes", [process["id"]])
    assert banned == ["Recipe_IronPlate_C"]
    with pytest.raises(pins.PinError, match="is a point: it cannot stand for a recipe"):
        pins.canonical(world, "required", [point["id"]])
    ops, _ = pins.canonical_ops(
        world,
        [
            {"op": "add", "field": "sources", "member": field["id"]},
            {"op": "remove", "field": "sources", "member": "pin:77"},
            {"op": "set", "field": "sloops", "value": 1},
        ],
    )
    assert [o["member"] for o in ops[: len(field["ref"]["nodes"])]] == [
        f"node:{m}" for m in field["ref"]["nodes"]
    ]
    assert ops[-2] == {"op": "remove", "field": "sources", "member": "pin:77"}
    args, _ = pins.canonical_args(world, {"sources": [point and field["id"]], "banned": []})
    assert all(not s.startswith("pin:") for s in args["sources"])


def test_plan_management_tools_take_a_plan_pin(world, tmp_path, monkeypatch):
    from satisfactory_mcp.domain.session import journal
    from satisfactory_mcp.interfaces.mcp.tools import planning

    monkeypatch.setattr(journal.config, "activity_dir", lambda: tmp_path / "activity")
    monkeypatch.setattr(planning, "_state", lambda *a, **k: world)
    monkeypatch.setattr(planning, "_sav", lambda st: "sav:test")
    made = PlanLog(WORLD).create("rip", HMF, actor=CHAT)
    plan, _ = pins.create(world, "plan", {"plan": made.key})
    point, _ = pins.create(world, "point", {"x_m": 1.0, "y_m": 2.0})
    assert "no saved plan" not in planning.list_plans(name=plan["id"])
    assert "no saved plan" not in planning.plan_log(name=plan["id"])
    out = planning.rename_plan(name=plan["id"], to="rip two", base_rev=1)
    assert "renamed plan 'rip' to 'rip two'" in out
    refused = planning.forget_plan(name=point["id"], base_rev=2)
    assert refused == f"! {point['id']} is a point: it cannot stand for a plan"
    assert planning.forget_plan(name=plan["id"], base_rev=2).startswith("forgot plan ")


def test_a_site_at_a_pin_stores_the_place_not_the_pin(world):
    from satisfactory_mcp.domain.planning.siting import resolve_plan_site

    point, _ = pins.create(world, "point", {"x_m": -7.9, "y_m": -5.5})
    site = resolve_plan_site(world, point["id"], terrain_field=None)
    assert site.describe().startswith(f"origin -7.9,-5.5m (from {point['id']} = -7.9,-5.5 (point))")
    assert site.to_dict()["origin_label"] == "-7.9,-5.5 (point)"


def test_show_on_map_pins_what_it_shows_once(world, tmp_path, monkeypatch):
    from satisfactory_mcp.domain.session import journal
    from satisfactory_mcp.interfaces.mcp.tools import spatial

    monkeypatch.setattr(journal.config, "activity_dir", lambda: tmp_path / "activity")
    monkeypatch.setattr(journal, "_writer", "")
    monkeypatch.setattr(journal, "_seq", {})
    journal.set_writer("chat")
    monkeypatch.setattr(spatial, "_state", lambda *a, **k: world)
    out = spatial.show_on_map("120,-340", pin=True)
    assert "pin: pinned as pin:1 point" in out
    assert "pin: already pin:1 point" in spatial.show_on_map("120,-340", pin=True)
    assert "pin: pinned as pin:2 node" in spatial.show_on_map(f"node:{OIL}", pin=True)
    name = world.labels.labels[0].name
    assert "pin: pinned as pin:3 factory" in spatial.show_on_map(name, pin=True)
    assert "pin: already pin:2" in spatial.show_on_map("pin:2", pin=True)
    assert "not pinned: a whole resource" in spatial.show_on_map("resource:Crude Oil", pin=True)
    assert "pin:" not in spatial.show_on_map("1,2")
    [row] = [p for p in pins.live(world) if p["n"] == 1]
    assert (row["x_m"], row["y_m"]) == (120.0, -340.0)
    added = [e for e in journal.read(WORLD) if e["kind"] == "pin.add"]
    assert [e["args"]["n"] for e in added] == [1, 2, 3]
    assert all(e["actor"]["kind"] == "chat" for e in added)

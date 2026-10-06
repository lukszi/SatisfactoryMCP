"""The pins store (docs/planner-p3_contract.md §4): numbers, revs, dedupe, fields and gone rules."""

from __future__ import annotations

import json

import pytest

from satisfactory_mcp import config
from satisfactory_mcp.core.schema import NewerSchema
from satisfactory_mcp.domain.planning.stored.planlog import Actor, PlanLog
from satisfactory_mcp.domain.session import pins
from satisfactory_mcp.domain.spatial import geo
from satisfactory_mcp.domain.spatial import nodes as nodes_mod
from satisfactory_mcp.domain.world.state import WorldState
from tests.support.reference_world import FIVE_RIP_ARGS, FIXTURE_WORLD

CHAT = Actor("chat", "claude-code", 4242)
OIL = "BP_ResourceNode26_99"


@pytest.fixture
def world(labelled):
    return labelled


def _first_machine_id(st: WorldState) -> str:
    return st.projection["machines"][0]["instance"].rsplit(".", 1)[-1]


def _iron_node() -> dict:
    table = nodes_mod.load_nodes()
    iron = [n for n in table.nodes if n["resource"] == "Desc_OreIron_C"]
    return max(
        iron,
        key=lambda n: sum(geo.distance_m((n["x"], n["y"]), (m["x"], m["y"])) < 200 for m in iron),
    )


def test_numbers_start_at_one_and_are_never_reused(world):
    first, existing = pins.create(world, "point", {"x_m": 10.0, "y_m": 20.0})
    assert (first["n"], first["id"], existing) == (1, "pin:1", False)
    second, _ = pins.create(world, "point", {"x_m": 11.0, "y_m": 20.0})
    assert second["n"] == 2
    pins.drop(FIXTURE_WORLD, 2, 1)
    third, _ = pins.create(world, "point", {"x_m": 12.0, "y_m": 20.0})
    assert third["n"] == 3
    data = pins.read(FIXTURE_WORLD)
    assert data["next"] == 4 and data["version"] == 4
    assert [p["n"] for p in pins.live(world)] == [1, 3]


def test_a_live_duplicate_returns_the_existing_pin(world):
    pin, _ = pins.create(world, "point", {"x_m": 10.04, "y_m": 20.0})
    again, existing = pins.create(world, "point", {"x_m": 10.0, "y_m": 20.01}, label="x")
    assert existing and again["n"] == pin["n"] and again["label"] == ""
    assert pins.read(FIXTURE_WORLD)["version"] == 1
    pins.drop(FIXTURE_WORLD, pin["n"], 1)
    fresh, existing = pins.create(world, "point", {"x_m": 10.0, "y_m": 20.0})
    assert not existing and fresh["n"] == 2


def test_rename_and_drop_count_revs_and_refuse_a_stale_rev(world):
    pin, _ = pins.create(world, "point", {"x_m": 1.0, "y_m": 2.0}, label="  spot  ")
    assert pin["label"] == "spot" and pin["rev"] == 1
    renamed = pins.rename(FIXTURE_WORLD, 1, 1, "the spot")
    assert renamed["rev"] == 2 and renamed["label"] == "the spot"
    with pytest.raises(pins.PinStale) as stale:
        pins.rename(FIXTURE_WORLD, 1, 1, "late")
    assert str(stale.value) == "pin:1 changed since you read it"
    assert stale.value.pin["label"] == "the spot"
    assert pins.read(FIXTURE_WORLD)["version"] == 2
    pins.drop(FIXTURE_WORLD, 1, 2)
    with pytest.raises(pins.PinMissing) as gone:
        pins.rename(FIXTURE_WORLD, 1, 3, "x")
    assert gone.value.deleted and str(gone.value) == "pin:1 was deleted"
    with pytest.raises(pins.PinMissing, match=r"pin:9 does not exist \(pins run to pin:1\)"):
        pins.get(world, 9)


def test_labels_are_capped_and_kinds_checked(world):
    with pytest.raises(pins.PinError, match="at most 80"):
        pins.create(world, "point", {"x_m": 1.0, "y_m": 2.0}, label="x" * 81)
    with pytest.raises(pins.PinError, match="a pin kind is one of"):
        pins.create(world, "ask", {})
    with pytest.raises(pins.PinError, match="outside the map"):
        pins.create(world, "point", {"x_m": 99999.0, "y_m": 0.0})
    with pytest.raises(pins.PinError, match="finite"):
        pins.create(world, "point", {"x_m": float("nan"), "y_m": 0.0})
    pins.create(world, "point", {"x_m": 1.0, "y_m": 2.0}, label="x" * 80)


def test_the_live_cap_refuses_the_next_pin(world, monkeypatch):
    monkeypatch.setattr(pins, "MAX_LIVE", 2)
    pins.create(world, "point", {"x_m": 1.0, "y_m": 2.0})
    pins.create(world, "point", {"x_m": 2.0, "y_m": 2.0})
    with pytest.raises(pins.PinError, match="already has 2 pins"):
        pins.create(world, "point", {"x_m": 3.0, "y_m": 2.0})


def test_a_newer_schema_is_refused_and_a_torn_file_reads_empty(world):
    path = pins.path_for(FIXTURE_WORLD)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"schema": 2, "pins": []}), encoding="utf-8")
    with pytest.raises(NewerSchema):
        pins.read(FIXTURE_WORLD)
    with pytest.raises(NewerSchema):
        pins.create(world, "point", {"x_m": 1.0, "y_m": 2.0})
    path.write_text('{"schema": 1, "pins": [', encoding="utf-8")
    assert pins.read(FIXTURE_WORLD)["pins"] == []
    path.unlink()
    assert pins.read(FIXTURE_WORLD) == {"schema": 1, "version": 0, "next": 1, "pins": []}


def test_a_field_is_the_200m_cluster_of_the_node_frozen_at_pin_time(world):
    node = _iron_node()
    short = node["instance"].rsplit(".", 1)[-1]
    pin, _ = pins.create(world, "field", {"node": node["instance"]})
    same = [n for n in nodes_mod.load_nodes().nodes if n["resource"] == node["resource"]]
    cluster = next(
        c
        for c in geo.cluster(same, 200.0)
        if any(m["instance"] == node["instance"] for m in c.members)
    )
    members = sorted(m["instance"].rsplit(".", 1)[-1] for m in cluster.members)
    assert pin["ref"] == {"node": short, "resource": "Desc_OreIron_C", "nodes": members}
    assert len(members) > 1
    assert pin["x_m"] == round(cluster.centroid[0] / 100, 1)
    assert pin["text"] == f"field Iron Ore · {len(members)} nodes"
    assert pin["selector"] == ",".join(f"node:{m}" for m in members)
    again, existing = pins.create(world, "field", {"node": members[-1]})
    assert existing and again["n"] == pin["n"]


def test_a_node_pin_names_its_resource_and_purity(world):
    pin, _ = pins.create(world, "node", {"node": OIL})
    node = nodes_mod.load_nodes().by_instance()
    found = next(n for k, n in node.items() if k.endswith(OIL))
    assert pin["selector"] == f"node:{OIL}" and pin["text"] == f"node Crude Oil, {found['purity']}"
    assert pin["x_m"] == round(found["x"] / 100, 1) and not pin["gone"]
    with pytest.raises(pins.ObjectMissing):
        pins.create(world, "node", {"node": "BP_NoSuchNode"})


def test_machine_and_factory_pins_resolve_and_go_gone(world, projection, game):
    inst = _first_machine_id(world)
    machine, _ = pins.create(
        world, "machine", {"machine": "Persistent_Level:PersistentLevel." + inst}
    )
    assert machine["ref"] == {"machine": inst} and machine["selector"] == f"machine:{inst}"
    assert machine["text"].startswith("machine ") and machine["x_m"] is not None
    label = world.labels.labels[0]
    factory, _ = pins.create(world, "factory", {"factory": label.name.upper()})
    assert factory["ref"] == {"factory": label.name}
    assert factory["selector"] == f"label:{label.name}" and factory["x_m"] is not None
    with pytest.raises(pins.ObjectMissing, match="no factory named"):
        pins.create(world, "factory", {"factory": "no such factory"})
    with pytest.raises(pins.ObjectMissing, match="no machine"):
        pins.create(world, "machine", {"machine": "Build_Nothing_C_1"})

    stripped = dict(projection)
    stripped["machines"] = [m for m in projection["machines"] if not m["instance"].endswith(inst)]
    (config.labels_dir() / f"{FIXTURE_WORLD}.json").unlink()
    moved = WorldState(projection=stripped, game=game)
    rows = {p["kind"]: p for p in pins.live(moved)}
    assert rows["machine"]["gone"] and rows["machine"]["gone_why"] == "machine not in this save"
    assert rows["factory"]["gone"]
    assert rows["factory"]["gone_why"] == f"no factory named “{label.name}” in this save"
    assert (rows["factory"]["x_m"], rows["factory"]["y_m"]) == (factory["x_m"], factory["y_m"])


def test_plan_and_process_pins_go_gone_when_the_plan_is_forgotten(world):
    made = PlanLog(FIXTURE_WORLD).create("rip", FIVE_RIP_ARGS, actor=CHAT)
    plan, _ = pins.create(world, "plan", {"plan": "RIP"})
    assert plan["ref"] == {"plan": made.key} and plan["selector"] == "rip"
    assert plan["x_m"] is None and plan["text"] == "plan “rip”"
    required = "Recipe_IronPlate_C"
    PlanLog(FIXTURE_WORLD).push(
        made.key, 1, [{"op": "add", "field": "required", "member": required}], actor=CHAT
    )
    process, _ = pins.create(world, "process", {"plan": made.key, "recipe": required})
    assert process["selector"] == required
    assert process["text"] == "process Constructor · Iron Plate in “rip”"
    with pytest.raises(pins.ObjectMissing, match="is not in plan"):
        pins.create(world, "process", {"plan": made.key, "recipe": "Recipe_Alternate_Turbofuel_C"})
    with pytest.raises(pins.ObjectMissing, match="no plan"):
        pins.create(world, "plan", {"plan": "nothing"})
    PlanLog(FIXTURE_WORLD).push(made.key, 2, [{"op": "forget"}], actor=CHAT)
    rows = pins.live(world)
    assert all(p["gone"] and p["gone_why"] == "plan forgotten" for p in rows)


def test_a_sited_plan_pin_sits_at_its_site(world):
    made = PlanLog(FIXTURE_WORLD).create(
        "rip", FIVE_RIP_ARGS, actor=CHAT, siting={"origin_m": [120.0, -340.0, 0.0]}
    )
    plan, _ = pins.create(world, "plan", {"plan": made.key})
    assert (plan["x_m"], plan["y_m"]) == (120.0, -340.0)


def test_parse_is_strict():
    assert pins.parse("pin:3") == 3 and pins.parse(" PIN:12 ") == 12
    assert pins.parse("pin:") is None and pins.parse("pin:x") is None
    assert pins.parse("near:pin:3@2") is None and pins.parse(None) is None

"""The advisor rules (docs/advisors_contract.md §2-§3): each kind fires and stays quiet, the
order, the caps, the keys and the ids. The fixture world with its reference factory names."""

from __future__ import annotations

import itertools

import pytest

from satisfactory_mcp import config
from satisfactory_mcp.domain.advice import rules
from satisfactory_mcp.domain.factories import health
from satisfactory_mcp.domain.planning.stored import manage
from satisfactory_mcp.domain.planning.stored.manage import PlanStatus
from satisfactory_mcp.domain.spatial import surroundings


@pytest.fixture
def world(labelled, tmp_path, monkeypatch):
    monkeypatch.setattr(config, "plans_dir", lambda: tmp_path / "plans")
    monkeypatch.setattr(rules, "_PLANS", {})
    return labelled


def _by(rows, kind):
    return [r for r in rows if r.kind == kind]


def test_the_fixture_fires_its_measured_rows(world):
    rows = rules.compute(world)
    assert [(r.kind, r.subject) for r in rows] == [
        ("unconnected", "Northern Forest"),
        ("dead_node", "blackpowder factory"),
        ("dead_node", "speedwire factory"),
        ("starved", "steel factory"),
        ("starved", "speedwire factory"),
        ("starved", "Northern Forest"),
        ("power", "world"),
        ("no_recipe", "tier 1&2"),
        ("pickups", "world"),
    ]
    assert {r.severity for r in _by(rows, "starved")} == {"act"}
    assert rules.TONE["act"] == "blocked" and "bad" not in rules.TONE.values()


def test_every_line_fits_the_page_and_chat(world):
    for r in rules.compute(world, box_fed=True):
        assert len(r.text) <= rules.TEXT_MAX and len(r.tool_text) <= rules.TOOL_MAX
        assert len(r.lines) <= 3 and len(r.spots) <= rules.SPOTS_SENT
        assert "None" not in r.text and "nan" not in r.text.lower()


def test_the_order_is_a_fixed_tuple_and_the_same_every_time(world):
    first, again = rules.compute(world), rules.compute(world)
    assert first == again
    assert first == sorted(first, key=rules.Advisory.rank)
    kinds = [rules.KINDS.index(r.kind) for r in first]
    assert kinds == sorted(kinds)


def test_k1_needs_every_missing_input_to_have_nothing_arriving():
    nothing = health.Feed("Coal", health.NOTHING, medium="conveyor")
    fed = health.Feed("Sulfur", health.FED, far="Build_ConveyorAttachmentSplitter_C_1")
    pipe = health.Feed("Water", health.OPEN, medium="pipe", rung=health.CONNECTION)
    m = health.MachineHealth("A_1", "Build_AssemblerMk1_C", "", "starved", 0.0)
    assert rules._unconnected(health.MachineHealth(**{**m.__dict__, "feeds": (nothing,)}))
    assert rules._unconnected(health.MachineHealth(**{**m.__dict__, "feeds": (nothing, pipe)}))
    assert not rules._unconnected(health.MachineHealth(**{**m.__dict__, "feeds": (nothing, fed)}))
    assert not rules._unconnected(m)


def test_k3_carries_the_blocked_holders_and_leaves_box_fed_machines_out(world):
    rows = rules.compute(world)
    speedwire = next(r for r in _by(rows, "starved") if r.subject == "speedwire factory")
    assert speedwire.text.endswith("starve of Caterium Ingot · 6 blocked here hold it")
    assert speedwire.lines[0] == "blocked holding Caterium Ingot: 6 here"
    assert speedwire.seed in speedwire.members
    boxed = _by(rules.compute(world, box_fed=True), "box_empty")
    assert boxed and boxed[0].severity == "note"
    starved = {m for r in _by(rows, "starved") for m in r.members}
    assert not starved & set(boxed[0].members)
    assert not _by(rows, "box_empty")


def test_k3_lists_items_rawest_first(world):
    steel = next(r for r in _by(rules.compute(world), "starved") if r.subject == "steel factory")
    assert "starve of Iron Ore" in steel.tool_text
    assert steel.tool_text.index("Iron Ore") < steel.tool_text.index("Steel Ingot")


class _Ledger:
    def __init__(self, gen: float, free: float) -> None:
        self.pw = {
            "generation_mw": gen,
            "headroom_mw": free,
            "measured_headroom_mw": free,
            "starved_generators": [],
            "starved_generation_mw": 0.0,
            "unwired_consumers": 0,
            "unwired_draw_mw": 0.0,
            "biomass_generators": 0,
        }

    def power_report(self, biomass=False):
        return self.pw


class _W:
    def __init__(self, gen: float, free: float) -> None:
        self.st = _Ledger(gen, free)


@pytest.mark.parametrize("free, fires", [(49.0, True), (50.0, False), (300.0, False)])
def test_k5_fires_under_five_percent_and_not_at_it(free, fires):
    rows = rules._power(_W(1000.0, free), False, "measured")
    assert bool(_by(rows, "headroom")) is fires
    if fires:
        assert rows[0].text == "headroom now is 49 MW of 1,000 MW"
        assert rows[0].lines[0] == "under 5% of generation · stage headroom: measured"


def test_k5_names_the_figure_the_shared_setting_picks():
    rows = rules._power(_W(1000.0, 10.0), False, "nameplate")
    assert rows[0].text.startswith("headroom at full rate is 10 MW")


def _starving(leaf: str, item: str) -> health.MachineHealth:
    return health.MachineHealth(leaf, "Build_FoundryMk1_C", "", "starved", 0.0, cause=(item,))


def test_k6_needs_a_starved_consumer_in_the_same_named_factory(world):
    w = rules._World(world)
    out = {"out": {"items": {"Desc_OreIron_C": 3}}}
    w.records = {
        "Miner_1": ("extractors", {"cls": "Build_MinerMk2_C", "clock": 0.5, "buffers": out}),
        "Foundry_1": ("machines", {"cls": "Build_FoundryMk1_C"}),
    }
    w.owner = {"Miner_1": "steel", "Foundry_1": "steel"}
    pool = [_starving("Foundry_1", "Iron Ore")]
    left, rows = rules._underclock(w, pool)
    assert left == [] and [r.kind for r in rows] == ["underclock"]
    assert rows[0].text == "Miner Mk.2 at 50% while 1 Foundry in “steel” starves of Iron Ore"
    w.owner["Foundry_1"] = "elsewhere"
    left, rows = rules._underclock(w, pool)
    assert rows == [] and left == pool
    w.owner["Foundry_1"] = "steel"
    w.records["Miner_1"][1]["clock"] = 1.0
    assert rules._underclock(w, pool)[1] == []


def test_k8_never_fires_on_world_moved_alone(labelled, monkeypatch, planned):
    seen = []

    def status(st, plan, g=None):
        seen.append(plan.name)
        return PlanStatus(flags=["world moved"])

    monkeypatch.setattr(manage, "plan_status", status)
    monkeypatch.setattr(rules, "_PLANS", {})
    assert _by(rules.compute(planned), "plan") == []
    assert seen == ["spire-coast-full"]


def test_k8_fires_on_a_drifted_field(labelled, monkeypatch, planned):
    monkeypatch.setattr(
        manage,
        "plan_status",
        lambda st, plan, g=None: PlanStatus(flags=["field 12->9"], drift=True),
    )
    monkeypatch.setattr(rules, "_PLANS", {})
    [row] = _by(rules.compute(planned), "plan")
    assert row.text == "plan “spire-coast-full”: a source selector found 12 nodes when saved, 9 now"
    assert row.key.startswith("plan|plan:") and row.plan and row.severity == "consider"
    assert row.next_call == 'diff_vs_save plan="spire-coast-full"'


def _pickup(category: str, spoiler: bool, d: float) -> dict:
    return {
        "category": category,
        "name": f"{category}_{d:.0f}",
        "pos": (100.0, 200.0, 0.0),
        "distance_m": d,
        "label": category.replace("_", " ") + "s",
        "spoiler": spoiler,
    }


def test_k9_honours_spoilers_and_never_counts_hard_drives(world, monkeypatch):
    near = [
        _pickup("power_slug_blue", False, 100.0),
        _pickup("mercer_sphere", True, 200.0),
        _pickup("hard_drive", False, 50.0),
        _pickup("crashed_drop_pod", False, 60.0),
    ]
    monkeypatch.setattr(surroundings, "_pickups_near", lambda st, x, y: [dict(p) for p in near])
    [quiet] = rules._pickups(world, spoilers=False)
    assert quiet.weight == 1 and quiet.text.endswith("power slug blue 100 m")
    [told] = rules._pickups(world, spoilers=True)
    assert told.weight == 2 and told.text.endswith("mercer sphere 200 m")
    assert told.reveal == ("pickup: mercer_sphere", "pickup: power_slug_blue")


def test_capped_keeps_three_of_a_kind_and_five_in_all(world):
    rows = rules.compute(world)
    many = [r for r in rows if r.kind == "starved"] * 2 + rows
    shown, rest = rules.capped(sorted(many, key=rules.Advisory.rank))
    assert len(shown) == 5 and len(shown) + len(rest) == len(many)
    assert max(sum(1 for r in shown if r.kind == k) for k in rules.KINDS) <= 3


def test_the_key_grammar_escapes_a_bar_and_splits_cleanly():
    key = rules.key_for("starved", "factory", "a|b")
    assert key == "starved|factory:a%7Cb"
    kind, _, subject = key.partition("|")
    assert kind == "starved" and subject == "factory:a%7Cb"
    assert rules.key_for("power", "world", "world") == "power|world"


def test_ids_are_four_hex_and_a_collision_lengthens_both_to_six():
    ids = rules.ids_for(["power|world", "pickups|world"])
    assert all(len(v) == len("adv:") + 4 for v in ids.values())
    seen: dict[str, str] = {}
    for n in itertools.count():
        key = f"starved|factory:f{n}"
        prefix = rules.ids_for([key])[key]
        if prefix in seen:
            pair = [seen[prefix], key]
            break
        seen[prefix] = key
    both = rules.ids_for(pair + ["power|world"])
    assert len({both[k] for k in pair}) == 2
    assert all(len(both[k]) == len("adv:") + 6 for k in pair)
    assert len(both["power|world"]) == len("adv:") + 4

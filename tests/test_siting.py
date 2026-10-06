"""A stored plan's SITING: origin, yaw, footprint -- recorded, round-tripped, surveyed.

The design decisions worth pinning:

* The siting is a defaulted dict on ``Plan``, exactly the discipline ``provenance``
  used -- so a plan file written before the feature loads unchanged, and "no siting"
  is an ordinary state every reader handles, not an error.
* Re-saving a plan's arguments never touches it: that re-states WHAT the plan is,
  and where it stands is a separate statement with its own verb.
* Containment is a genuinely rotated rectangle, matching the yaw convention the save
  stores machine facing with (degrees about world Z, +X towards +Y). An axis-aligned
  shortcut would count machines standing off a turned pad's corners.
"""

from __future__ import annotations

import json

import pytest

from satisfactory_mcp.domain.planning import siting as siting_mod
from satisfactory_mcp.domain.planning.siting import Siting
from satisfactory_mcp.domain.planning.stored.planlog import Actor, PlanLog
from satisfactory_mcp.domain.planning.stored.recall import PLAN_DEFAULTS, recall_plan
from satisfactory_mcp.domain.planning.stored.store import Plan, PlanStore

ACTOR = Actor("chat")


@pytest.fixture
def store(tmp_path, monkeypatch):
    from satisfactory_mcp.domain.planning.stored import store as store_mod

    monkeypatch.setattr(store_mod.config, "plans_dir", lambda: tmp_path)
    return PlanLog("TESTWORLD")


def _held(**siting) -> PlanStore:
    """One plan held in memory, the shape ``recall_plan`` reads through ``st.plans``."""
    return PlanStore(
        world_id="TESTWORLD",
        plans=[Plan(name="p", args={"objective": "min_power"}, plan_id="x", siting=siting)],
    )


def _sited(**overrides) -> Siting:
    base = dict(x_m=100.0, y_m=-200.0, z_m=40.0, yaw_deg=0.0, width_m=60.0, depth_m=40.0)
    base.update(overrides)
    return Siting(**base)


# ---------------------------------------------------------------- geometry


def test_containment_is_the_rectangle_not_its_bounding_box():
    """At yaw 45 the corner of the AABB is OUTSIDE the pad. A machine there must not
    count, or every turned site over-reports what stands on it."""
    sit = _sited(width_m=60.0, depth_m=40.0, yaw_deg=45.0)
    # Centre always contains.
    assert sit.contains_cm(100.0 * 100, -200.0 * 100)
    # Along the rotated local X axis (cos45, sin45), 29 m out of a 30 m half-width: in.
    d = 29.0 / 2**0.5
    assert sit.contains_cm((100.0 + d) * 100, (-200.0 + d) * 100)
    # The unrotated point (29, 0) sits inside the AABB of the turned pad but outside
    # the pad itself: its local X is 29/sqrt(2) but its local Y is 20.5 > 20.
    assert not sit.contains_cm((100.0 + 29.0) * 100, -200.0 * 100)


def test_yaw_zero_is_the_plain_axis_aligned_box():
    sit = _sited(width_m=60.0, depth_m=40.0, yaw_deg=0.0)
    assert sit.contains_cm((100.0 + 29.9) * 100, -200.0 * 100)
    assert not sit.contains_cm((100.0 + 30.1) * 100, -200.0 * 100)
    assert sit.contains_cm(100.0 * 100, (-200.0 + 19.9) * 100)
    assert not sit.contains_cm(100.0 * 100, (-200.0 + 20.1) * 100)


def test_no_footprint_contains_nothing():
    """An origin alone marks a spot but bounds nothing; a survey over it would just be
    the whole save again."""
    sit = _sited(width_m=0.0, depth_m=0.0)
    assert not sit.contains_cm(100.0 * 100, -200.0 * 100)


def test_footprint_parses_wxd_and_squares():
    assert siting_mod.parse_footprint("96x64") == (96.0, 64.0)
    assert siting_mod.parse_footprint(" 96 X 64 m") == (96.0, 64.0)
    assert siting_mod.parse_footprint("80") == (80.0, 80.0)
    with pytest.raises(ValueError, match="WxD"):
        siting_mod.parse_footprint("96x64x8")
    with pytest.raises(ValueError, match="positive"):
        siting_mod.parse_footprint("-5")


# ---------------------------------------------------------------- storage


def test_a_siting_round_trips_through_disk(store):
    store.create(
        "north oil",
        {"objective": "max_mw"},
        plan_id="abc",
        siting=_sited(yaw_deg=15.0, source="given").to_dict(),
        actor=ACTOR,
    )

    again = PlanLog("TESTWORLD").view()
    sit = siting_mod.parse(again.plans[0])
    assert sit is not None
    assert (sit.x_m, sit.y_m, sit.z_m) == (100.0, -200.0, 40.0)
    assert sit.yaw_deg == 15.0
    assert (sit.width_m, sit.depth_m) == (60.0, 40.0)
    assert sit.source == "given"


def test_a_plan_file_from_before_the_feature_still_loads(store, tmp_path):
    """The actual old bytes: a plan dict with no ``siting`` key at all. It must load,
    and read as "not sited" rather than as anything else."""
    path = PlanStore.path_for("TESTWORLD")
    path.write_text(
        json.dumps(
            {
                "schema": 1,
                "world_id": "TESTWORLD",
                "session_name": "",
                "plans": [
                    {
                        "name": "old",
                        "args": {"objective": "max_mw"},
                        "notes": "",
                        "plan_id": "x",
                        "factory": "",
                        "created": "",
                        "provenance": {},
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    again = PlanStore.load("TESTWORLD")
    assert [p.name for p in again.plans] == ["old"]
    assert again.plans[0].siting == {}
    assert siting_mod.parse(again.plans[0]) is None


def test_resaving_the_arguments_keeps_the_siting(store):
    """A re-save rewrites args and provenance; the siting is a different statement and
    survives a ``save_as`` over the same name."""
    key = store.create(
        "p", {"objective": "max_mw"}, plan_id="one", siting=_sited().to_dict(), actor=ACTOR
    ).key
    store.push_args(key, 1, {"objective": "min_power"}, actor=ACTOR)
    assert siting_mod.parse(store.view().plans[0]) is not None


def test_a_mangled_siting_reads_as_not_sited():
    """A hand-edited record that no longer parses must not crash every planning tool
    that recalls the plan."""
    plan = Plan(name="p", siting={"origin_m": ["not", "numbers"]})
    assert siting_mod.parse(plan) is None
    assert siting_mod.parse(Plan(name="q", siting={})) is None


# ----------------------------------------------------------------- recall


class _FakeState:
    def __init__(self, plans):
        self.plans = plans


def test_recall_prints_the_siting():
    store = _held(**_sited(yaw_deg=15.0, source="layout").to_dict())
    _, _, notes = recall_plan(_FakeState(store), "p", dict(PLAN_DEFAULTS))
    assert any(n.startswith("sited:") for n in notes)
    assert any("100,-200" in n for n in notes)


def test_recall_of_an_unsited_plan_says_nothing_about_siting():
    store = _held()
    _, _, notes = recall_plan(_FakeState(store), "p", dict(PLAN_DEFAULTS))
    assert not any("sited" in n for n in notes)


def test_a_recalled_sited_plan_is_measured_at_its_own_site():
    """The pad and its footprint come back without being retyped, which is most of the
    point of having stored them: `plan_factory plan='x'` measures the ground it stands on."""
    store = _held(**_sited(width_m=60.0, depth_m=40.0).to_dict())
    assert siting_mod.plan_site_args(_FakeState(store), "p", "", "") == ("100,-200", "60x40")
    # An explicit argument wins: the caller is asking about somewhere else.
    assert siting_mod.plan_site_args(_FakeState(store), "p", "me", "") == ("me", "")
    assert siting_mod.plan_site_args(_FakeState(store), None, "", "") == ("", "")


# ----------------------------------------------------------------- survey


@pytest.mark.integration
def test_survey_counts_what_stands_inside_the_footprint(game, state):
    """Centre a pad on a real machine from the fixture save: it must be counted, and a
    pad in open ocean must count nothing."""
    record = next(r for r in state._all_records() if r.get("pos"))
    x_cm, y_cm = record["pos"][0], record["pos"][1]
    sit = Siting(x_m=x_cm / 100, y_m=y_cm / 100, width_m=20.0, depth_m=20.0)

    sv = siting_mod.survey(game, state, sit, [])
    assert sv is not None
    assert sv.standing_total >= 1
    assert any(r.cls == record["cls"] and r.standing >= 1 for r in sv.rows)
    assert sv.planned_total == 0

    empty = siting_mod.survey(
        game, state, Siting(x_m=-4500.0, y_m=-4500.0, width_m=20.0, depth_m=20.0), []
    )
    assert empty is not None and empty.standing_total == 0


@pytest.mark.integration
def test_survey_reports_planned_against_standing_per_class(game, state):
    record = next(r for r in state._all_records() if r.get("pos"))
    x_cm, y_cm = record["pos"][0], record["pos"][1]
    sit = Siting(x_m=x_cm / 100, y_m=y_cm / 100, width_m=20.0, depth_m=20.0)
    processes = [
        {"building_id": record["cls"], "machines": 3},
        {"building_id": "Build_Imaginary_C", "machines": 2},
    ]
    sv = siting_mod.survey(game, state, sit, processes)
    by_cls = {r.cls: r for r in sv.rows}
    assert by_cls[record["cls"]].planned == 3
    assert by_cls[record["cls"]].standing >= 1
    assert by_cls["Build_Imaginary_C"].planned == 2
    assert by_cls["Build_Imaginary_C"].standing == 0
    assert sv.planned_total == 5


@pytest.mark.integration
def test_survey_without_a_footprint_is_none(game, state):
    assert siting_mod.survey(game, state, Siting(x_m=0.0, y_m=0.0), []) is None


# ----------------------------------------------------------------- links


def test_the_local_map_link_matches_the_frontends_own_writer():
    """``writeHash`` in map.ts writes ``#world=…&z=…&c=x,y`` with c in metres to one
    decimal; the deep link must be a fragment that reader accepts."""
    from satisfactory_mcp.domain.spatial.maplink import local_map_url

    url = local_map_url(870.25, -1250.0, world="W1")
    assert url == "http://127.0.0.1:8712/#world=W1&z=1&c=870.2,-1250"
    assert local_map_url(0.0, 0.0) == "http://127.0.0.1:8712/#z=1&c=0,0"

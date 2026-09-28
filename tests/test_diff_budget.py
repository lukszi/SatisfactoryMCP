"""diff rows against what the plan budgets: the clock it plans and the machines it needs.

No game install needed: the rows are built from hand-made save indexes, so these run in the
default suite next to the integration-marked test_diff.py.
"""

from __future__ import annotations

from satisfactory_mcp.domain.planning.diff import (
    RECLOCK_TOLERANCE,
    _planned_clock,
    _reclock_note,
    _row_for,
    _SaveIndex,
)
from satisfactory_mcp.domain.planning.track import page_text

PUMP = "Build_WaterPump_C"


class _World:
    def built(self, cls: str) -> int:
        return 1


def _group(machines: int, clock: float = 1.0) -> dict:
    return {
        "key": ("extractor", PUMP, "Desc_Water_C", ""),
        "kind": "extractor",
        "building_id": PUMP,
        "building": "Water Extractor",
        "recipe": None,
        "resource": "Desc_Water_C",
        "purity": "",
        "machines": machines,
        "clock": clock * machines,
        "mw": 20.0 * machines,
        "labels": [("Water", machines)],
        "rates": {},
    }


def _pumps(running: int, paused: int) -> _SaveIndex:
    rows = [
        {"instance": f"L.{PUMP}_{i}", "pos": (0.0, 0.0, 0.0), "paused": i >= running, "clock": 1.0}
        for i in range(running + paused)
    ]
    return _SaveIndex(
        by_recipe={},
        by_generator={},
        by_extractor_class={PUMP: rows},
        idle={},
        tapped={},
        free={},
        extractor_on={},
    )


def _row(need: int, running: int, paused: int):
    return _row_for(_World(), _group(need), _pumps(running, paused), 1, None, [], set())


def test_a_plan_at_250_percent_is_the_budget_a_250_percent_pump_is_measured_against():
    assert _reclock_note([{"clock": 2.5}], 2.5) == ""
    assert _reclock_note([{"clock": 1.0}], 2.5) == "1 at 100%, plan budgets 250%"
    assert _reclock_note([{"clock": 2.5}], 1.0) == "1 at 250%, plan budgets 100%"


def test_a_derived_ratio_near_100_percent_budgets_100_percent():
    near = _group(53, 1.0 - RECLOCK_TOLERANCE / 2)
    assert _planned_clock(near) == 1.0
    assert _planned_clock(_group(7, 2.5)) == 2.5
    assert _reclock_note([{"clock": 1.0}], _planned_clock(near)) == ""


def test_unpause_asks_only_for_the_shortfall():
    row = _row(need=1, running=16, paused=4)
    assert row.verb == "OK" and row.count == 0
    assert row.act_instances == []
    assert "4 paused, not needed to cover this job" in row.page_note


def test_unpause_takes_the_paused_ones_the_plan_needs_and_notes_the_rest():
    row = _row(need=18, running=16, paused=4)
    assert row.verb == "UNPAUSE" and row.count == 2
    assert len(row.act_instances) == 2
    assert "2 more paused, not needed to cover this job" in row.note


def test_every_paused_pump_is_unpaused_when_the_plan_needs_them_all():
    row = _row(need=30, running=16, paused=4)
    assert row.verb == "UNPAUSE" and row.count == 4
    assert "paused, not needed" not in row.note


def test_page_text_says_stages_and_leaves_the_headline_to_say_no_order_fits():
    said = page_text(
        "no startup order exists at this scope: one machine of every process draws 392 MW"
    )
    assert said == "one machine of every process draws 392 MW"
    assert page_text("could not fit a whole-machine wave inside the headroom") == (
        "could not fit a whole-machine stage inside the headroom"
    )
    assert page_text("gave up after 12 waves") == "gave up after 12 stages"


def test_page_text_writes_ranges_with_a_dash():
    assert page_text("60%..70% built") == "60%–70% built"

"""diff rows against what the plan budgets: the clock it plans and the machines it needs.

No game install needed: the rows are built from hand-made save indexes, so these run in the
default suite next to the integration-marked test_diff.py.
"""

from __future__ import annotations

from satisfactory_mcp.domain.planning.progress.diff import (
    RECLOCK_TOLERANCE,
    _reclock_note,
    _row_for,
    _SaveIndex,
)
from satisfactory_mcp.domain.planning.progress.jobs import BuildJob
from satisfactory_mcp.domain.planning.progress.track import page_text

PUMP = "Build_WaterPump_C"


class _World:
    def built(self, cls: str) -> int:
        return 1


def _group(machines: int, clock: float = 1.0) -> BuildJob:
    return BuildJob(
        key=("extractor", PUMP, "Desc_Water_C", ""),
        kind="extractor",
        building_id=PUMP,
        building="Water Extractor",
        recipe=None,
        resource="Desc_Water_C",
        purity="",
        machines=machines,
        clock_sum=clock * machines,
        mw=20.0 * machines,
        labels=[("Water", machines)],
        rates={},
    )


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


def _row(need: int, running: int, paused: int, near: bool = True):
    points = [(0.0, 0.0)] if near else []
    return _row_for(_World(), _group(need), _pumps(running, paused), 1, None, points, set())


def _clocked(*clocks: float) -> list[dict]:
    return [{"clock": c} for c in clocks]


def test_spreading_the_planned_rate_over_more_machines_is_no_note():
    water = _group(1, 0.9524)
    assert _reclock_note(_clocked(0.4762, 0.4762), 0, water) == ""
    assert _reclock_note(_clocked(0.9524), 0, water) == ""
    assert _reclock_note(_clocked(0.3175, 0.3175, 0.3175), 0, water) == ""


def test_a_total_that_misses_the_plan_is_noted():
    water = _group(1, 0.9524)
    said = _reclock_note(_clocked(0.25, 0.25), 0, water)
    assert said == "clocks give 52% of the planned rate (plan: 1 at 95.24%)"
    assert _reclock_note(_clocked(1.0), 0, _group(1, 2.5)) == (
        "clocks give 40% of the planned rate (plan: 1 at 250%)"
    )
    assert "250%" not in _reclock_note(_clocked(2.5), 0, _group(1, 2.5))


def test_an_overclock_that_outruns_the_plan_is_noted_but_extra_machines_are_not():
    assert _reclock_note(_clocked(2.5, 1.0), 0, _group(2)) == (
        "clocks give 175% of the planned rate (plan: 2 at 100%)"
    )
    assert _reclock_note(_clocked(1.0, 1.0, 1.0), 0, _group(2)) == ""


def test_machines_still_to_build_count_at_the_plans_clock():
    assert _reclock_note(_clocked(0.75), 1, _group(2, 0.75)) == ""
    assert _reclock_note(_clocked(1.0), 1, _group(2, 0.5)) == (
        "clocks give 150% of the planned rate (plan: 2 at 50%)"
    )


def test_a_derived_ratio_near_100_percent_is_met_by_machines_at_100():
    near = _group(53, 1.0 - RECLOCK_TOLERANCE / 2)
    assert _reclock_note(_clocked(*[1.0] * 53), 0, near) == ""
    assert _reclock_note([{"clock": None}] * 53, 0, near) == ""


def test_a_ranged_water_row_spread_over_two_pumps_says_nothing_about_clocks():
    rows = [
        {"instance": f"L.{PUMP}_{i}", "pos": (0.0, 0.0, 0.0), "clock": 0.4762} for i in range(2)
    ]
    index = _SaveIndex(
        by_recipe={},
        by_generator={},
        by_extractor_class={PUMP: rows},
        idle={},
        tapped={},
        free={},
        extractor_on={},
    )
    row = _row_for(_World(), _group(1, 0.9524), index, 1, None, [(0.0, 0.0)], set())
    assert "planned rate" not in row.note


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


def test_paused_pumps_are_not_called_spare_when_the_low_bound_is_under_need():
    row = _row(need=6, running=16, paused=4, near=False)
    assert row.have_min == 0 and row.build_max == 6
    assert "not needed" not in row.note and "not needed" not in row.page_note


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

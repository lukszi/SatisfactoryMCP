"""Matching by rate: machines spread over a lower clock count as the fewer machines they
stand in for, in the diff and in the startup stages (docs/planning.md, "Counting by rate")."""

from __future__ import annotations

from types import SimpleNamespace as NS

import pytest

from satisfactory_mcp.domain.planning.progress.diff import DiffRow, build_diff, rate_units
from satisfactory_mcp.domain.planning.progress.stages import track
from satisfactory_mcp.domain.planning.progress.startup import Energised, Wave
from satisfactory_mcp.domain.world.state import WorldState


def _solution(building, recipe, machines, clock=1.0):
    proc = {
        "kind": "recipe",
        "building_id": building,
        "building": "Constructor",
        "recipe": recipe,
        "machines": machines,
        "clock": clock,
        "mw": 4.0 * machines,
        "label": "Iron Plate",
        "rates": {},
        "pid": "p1",
    }
    return NS(processes=[proc]), NS(node_rows=[], selection=NS(errors=[]))


def _clocked(game, projection, recipe, clocks):
    """The fixture world with its machines on ``recipe`` replaced by ones at ``clocks``."""
    base = next(m for m in projection["machines"] if m.get("recipe") == recipe)
    keep = [m for m in projection["machines"] if m.get("recipe") != recipe]
    made = [
        {**base, "instance": f"{base['instance']}_rate{i}", "clock": c}
        for i, c in enumerate(clocks)
    ]
    return WorldState(projection={**projection, "machines": keep + made}, game=game), made


def test_eight_at_half_speed_are_four_at_full(game, projection):
    st, made = _clocked(game, projection, "Recipe_IronPlate_C", [0.5] * 8)
    sol, req = _solution(made[0]["cls"], "Recipe_IronPlate_C", 4)
    row = build_diff(game, st, sol, req).rows[0]
    assert row.have == 4 and row.build == 0 and row.verb == "OK"
    assert row.have_rate == pytest.approx(4.0) and len(row.have_instances) == 8


def test_underclocked_to_the_exact_rate(game, projection):
    st, made = _clocked(game, projection, "Recipe_IronPlate_C", [2 / 3] * 3)
    sol, req = _solution(made[0]["cls"], "Recipe_IronPlate_C", 2)
    row = build_diff(game, st, sol, req).rows[0]
    assert row.have == 2 and row.build == 0
    sol, req = _solution(made[0]["cls"], "Recipe_IronPlate_C", 3)
    row = build_diff(game, st, sol, req).rows[0]
    assert row.have == 2 and row.build == 1


def test_stages_fill_by_rate():
    key = ("recipe", "Build_ConstructorMk1_C", "Recipe_IronPlate_C")
    row = DiffRow(
        stage=1,
        verb="OK",
        count=0,
        process="Iron Plate",
        building_id=key[1],
        building="Constructor",
        need=4,
        have=4,
        key=key,
        have_instances=[f"m{i}" for i in range(8)],
        have_clocks=[0.5] * 8,
        need_rate=4.0,
        have_rate=4.0,
        plan_clock=1.0,
    )
    waves = [
        Wave(
            index=i,
            rows=[
                Energised("Iron Plate", "recipe", "Constructor", 2, 2 * i, 4, 8.0, 0.0, pid="p1")
            ],
        )
        for i in (1, 2)
    ]
    prepared = NS(
        solution=NS(
            processes=[{"pid": "p1", "kind": "recipe", "building_id": key[1], "recipe": key[2]}]
        )
    )
    report = NS(rows=[row])
    out = track(prepared, NS(ok=True, waves=waves), report, None, None, health={})
    assert [s.built for s in out.stages] == [2, 2]
    assert [len(s.rows[0].instances) for s in out.stages] == [4, 4]
    assert out.current == 0


def test_rate_units_are_whole_machines():
    assert rate_units(2.0001, 1.0) == 2
    assert rate_units(1.9999999, 1.0) == 2
    assert rate_units(1.5, 1.0) == 1
    assert rate_units(1.0, 0.0) == 0

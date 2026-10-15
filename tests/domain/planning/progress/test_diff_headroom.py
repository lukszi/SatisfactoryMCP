"""diff_vs_save prints one headroom, the one its stages use, and warns when the plan does not fit.

It printed the save's nameplate headroom in the header and the measured one under the stages,
with no line saying which counted.
"""

from __future__ import annotations

import re

import pytest

from satisfactory_mcp import server as srv
from satisfactory_mcp.domain.planning.progress.diff_service import build_diff_report
from satisfactory_mcp.domain.world.state import WorldState
from satisfactory_mcp.presenters.text.diff import render_diff

pytestmark = pytest.mark.integration

MOTOR = {"objective": "min_machines", "exports": ["Motor"], "export_minimums": {"Motor": 5}}
BIG_MOTOR = {**MOTOR, "export_minimums": {"Motor": 60}}


@pytest.fixture(autouse=True)
def _reference_world(use_world, projection, game):
    use_world(lambda: WorldState(projection=projection, game=game))


def test_one_headroom_figure_and_it_is_the_stages_one(state):
    srv.plan_factory(**MOTOR, save_as="motor")
    out = srv.diff_vs_save(plan="motor")
    figures = re.findall(r"headroom_MW=([\d.,]+)", out)
    assert len(figures) == 1
    measured = state.power_report()["measured_headroom_mw"]
    assert float(figures[0].replace(",", "")) == pytest.approx(measured, abs=0.01)
    assert "# headroom measured from the save" in out
    assert "stages use" not in out
    assert "fits=yes" in out and "does not fit" not in out


def test_a_plan_over_the_headroom_is_shown_with_a_warning(game, state):
    report = build_diff_report(
        game, state, BIG_MOTOR, objective="min_machines", default="nameplate"
    )
    out = render_diff(state, report, objective="min_machines", limit=5)
    assert report.headroom_mw == pytest.approx(state.power_report()["headroom_mw"])
    assert report.diff is not None and report.diff.deficit_mw > report.headroom_mw
    assert "fits=NO" in out
    assert "! does not fit: building it adds up to" in out
    # A warning, not a refusal: the delta rows are still there.
    assert "\tBUILD\t" in out

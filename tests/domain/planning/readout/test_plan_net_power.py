"""One net power figure for a plan, whichever surface asks, and a recall that keeps its own
objective. Reads the committed fixture world."""

from __future__ import annotations

import pytest

from satisfactory_mcp.domain.planning.readout.summary import solve_summary
from satisfactory_mcp.domain.world.state import WorldState
from satisfactory_mcp.interfaces.mcp.tools import planning

KW = dict(
    objective="min_machines",
    target_item="Computer",
    exports=["Computer"],
    export_minimums={"Computer": 10},
)


@pytest.fixture
def tool(state, monkeypatch):
    fresh = lambda *a, **k: WorldState(projection=state.projection, game=state.game)
    monkeypatch.setattr(planning, "_state", fresh)
    return planning


def _net(text: str) -> float:
    return float(next(x for x in text.split() if x.startswith("net_MW=")).split("=")[1])


def test_chat_and_page_quote_the_same_net_mw(tool, game, state):
    page = solve_summary(game, state, dict(KW))
    chat = tool.plan_factory(**KW)
    assert page["feasible"], page["headline"]
    assert _net(chat) == pytest.approx(page["mw_net"], abs=0.01)


def test_a_recalled_plan_is_headed_by_its_own_objective(tool):
    saved = tool.plan_factory(save_as="computers", **KW)
    assert "saved as" in saved
    recalled = tool.plan_factory(plan="computers")
    assert any(line.startswith("# min_machines over") for line in recalled.splitlines())
    assert "# max_mw over" not in recalled


def test_an_override_saved_in_the_same_call_is_not_called_unsaved(tool):
    tool.plan_factory(save_as="computers", **KW)
    out = tool.plan_factory(
        plan="computers", export_minimums={"Computer": 5}, save_as="computers", base_rev=1
    )
    assert "saved over" in out
    assert "not saved" not in out

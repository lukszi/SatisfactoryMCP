"""A plan from a built factory over the newest save on the machine: the three largest proposed
factories are named, each becomes a plan, and each plan either solves with its differences
listed or says why it does not (docs/planning.md §8.5p)."""

from __future__ import annotations

import pytest

from satisfactory_mcp.domain.planning.progress import as_built
from satisfactory_mcp.domain.planning.stored.planlog import Actor, PlanLog

pytestmark = pytest.mark.integration

CHAT = Actor("chat")


def test_the_largest_factories_of_the_save_become_plans(live):
    proposals = sorted(live.proposals, key=lambda p: -p.size)[:3]
    assert proposals, "the save proposes no factory"
    for n, proposal in enumerate(proposals):
        live.labels.put(f"proposed {n}", proposal.machines)
    log = PlanLog(live.world_id, live.session_name)
    for n in range(len(proposals)):
        made = as_built.plan_from_factory(live, f"proposed {n}", actor=CHAT)
        plan = log.state(made.pushed.key)
        assert (plan.rev, plan.factory) == (1, f"proposed {n}")
        assert plan.siting, "a factory with machines is sited on them"
        if made.solve["feasible"]:
            assert made.solve["machines"] > 0
        else:
            assert made.differences[0].startswith("the plan does not solve")
        assert all(isinstance(line, str) and line for line in made.differences)

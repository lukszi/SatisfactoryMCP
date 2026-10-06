"""The plan log every stored-plan test writes to, and one plan in it."""

from __future__ import annotations

import pytest

from satisfactory_mcp.domain.planning.planlog import PlanLog
from tests.support.plan_log import CHAT


@pytest.fixture
def plans():
    return PlanLog("W")


@pytest.fixture
def plan(plans):
    return plans.create(
        "north hmf",
        {
            "objective": "min_machines",
            "exports": ["Heavy Modular Frame"],
            "export_minimums": {"Heavy Modular Frame": 10},
        },
        actor=CHAT,
    ).key

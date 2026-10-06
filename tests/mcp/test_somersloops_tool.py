"""The sloop inventory, which existed and was not exposed.

`sloop_budget` has been on WorldState since sloops became spendable, and nothing surfaced
it -- so the only way to learn how many you held was to guess a `sloops=` budget and read
the shortfall warning. You had to guess the budget to discover the budget.
"""

from __future__ import annotations

import pytest

from satisfactory_mcp import server as srv
from satisfactory_mcp.interfaces.mcp.tools import progression as progression_tools

pytestmark = pytest.mark.integration


def test_it_reports_free_committed_and_owned(game, live):
    out = srv.somersloops()
    budget = live.sloop_budget()
    assert f"free={budget['free']:.0f}" in out
    assert f"committed={budget['committed']:.0f}" in out
    assert f"owned={budget['owned']:.0f}" in out


def test_owned_is_free_plus_committed(live):
    budget = live.sloop_budget()
    assert budget["owned"] == pytest.approx(budget["free"] + budget["committed"])


def test_only_free_sloops_can_fund_a_plan(game):
    """Committed ones are counted so you know there is something to pull out, never added
    to what is spendable -- the same rule the shard budget follows for slotted shards."""
    out = srv.somersloops()
    assert "only FREE sloops can fund a plan" in out


def test_mercer_spheres_are_reported_apart(game, live):
    out = srv.somersloops()
    assert "mercer_spheres=" in out
    assert "do nothing for production" in out


def test_a_holder_row_names_the_machine_and_its_boost(game, live):
    """So "pull one out" is an instruction rather than a hint."""
    if not live.sloop_budget()["holders"]:
        pytest.skip("nothing slotted in this save")
    out = srv.somersloops()
    assert "building\tinstance\tsloops\tboost" in out


def test_the_boost_the_save_carries_is_printed_beside_the_computed_one(game, live):
    """`boost_in_save` is read for exactly this and was never printed, which left the
    cross-check computed and thrown away every call.

    They come from different places on purpose: `boost` is what the plan model says the
    slots are worth, `boost_in_save` is mPendingProductionBoost off the machine. Agreement
    is evidence the model is right about this building; disagreement is a finding, and a
    finding nobody could see.
    """
    holders = live.sloop_budget()["holders"]
    if not holders:
        pytest.skip("nothing slotted in this save")
    out = srv.somersloops()
    assert "sloops\tboost\tboost_in_save" in out
    assert "boost_in_save is mPendingProductionBoost" in out
    row = next(r for r in out.splitlines() if r.startswith(holders[0]["name"] + "\t"))
    saved = holders[0]["boost_in_save"]
    assert row.split("\t")[-1] == (f"{saved:g}x" if saved else "-")


def test_a_disagreement_between_the_two_is_reported_as_a_finding(game, live, monkeypatch):
    """The cross-check is only worth printing if a mismatch is called one. Constructed,
    because the two agree on every holder of this world -- which is the answer the check
    is supposed to give, and therefore the state that cannot exercise the alarm."""
    budget = live.sloop_budget()
    if not budget["holders"]:
        pytest.skip("nothing slotted in this save")
    budget = {**budget, "holders": [{**h} for h in budget["holders"]]}
    budget["holders"][0]["boost"] = 1.25
    budget["holders"][0]["boost_in_save"] = 2.0
    monkeypatch.setattr(type(live), "sloop_budget", lambda self: budget)
    monkeypatch.setattr(progression_tools, "_state", lambda save=None, world=None, as_of=None: live)
    out = srv.somersloops()
    assert "THEY DISAGREE on 1 building(s)" in out
    assert "computed 1.25x, save says 2x" in out


def test_the_holder_table_says_how_many_there_are(game, live, monkeypatch):
    """It slices to 20 and printed no count, so a world with 30 boosted machines showed 20
    rows and nothing to say the list had been cut. Constructed: this world has one."""
    holder = dict((live.sloop_budget()["holders"] or [{}])[0]) or {
        "name": "Manufacturer",
        "instance": "x",
        "sloops": 4.0,
        "boost": 2.0,
        "boost_in_save": 2.0,
    }
    budget = {**live.sloop_budget(), "holders": [dict(holder) for _ in range(30)]}
    monkeypatch.setattr(type(live), "sloop_budget", lambda self: budget)
    monkeypatch.setattr(progression_tools, "_state", lambda save=None, world=None, as_of=None: live)
    out = srv.somersloops()
    assert "# 30 match(es), showing 20" in out

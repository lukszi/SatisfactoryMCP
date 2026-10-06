"""``/api/ui/focus`` and ``/api/activity``: what the page is looking at, and the feed of
plan commits and journal entries beside it.
"""

from __future__ import annotations

import pytest

fastapi = pytest.importorskip("fastapi")

from satisfactory_mcp.domain.planning import focus, journal
from satisfactory_mcp.domain.planning.planlog import Actor
from tests.support.plan_log import CHAT
from tests.support.reference_world import FIXTURE_WORLD, RIP
from tests.support.web import PAGE_ORIGIN, create_plan, push_ops, put_op


def test_focus_is_written_with_a_heartbeat_for_ui_context_to_read(fresh_state_client):
    reply = fresh_state_client.put(
        "/api/ui/focus",
        json={
            "view": "planner",
            "dash": "planner/0000beef",
            "plan": "0000beef",
            "rev": 3,
            "tab": "workbench",
            "selection": {"kind": "process", "label": "Constructor · Iron Plate", "ref": "R"},
            "follow": "toasts",
            "sav": "sav:000000000000",
        },
        headers=PAGE_ORIGIN,
    )
    assert reply.status_code == 200, reply.text
    stored = focus.read(FIXTURE_WORLD)
    assert stored["heartbeat"] == reply.json()["heartbeat"]
    assert stored["plan"] == "0000beef" and stored["follow"] == "toasts"
    assert focus.is_open(stored)
    bad = fresh_state_client.put("/api/ui/focus", json={"view": "kitchen"}, headers=PAGE_ORIGIN)
    assert bad.status_code == 400


def test_activity_merges_commits_and_journal_entries_by_time(fresh_state_client):
    journal.set_writer("web")
    key = create_plan(fresh_state_client)["key"]
    journal.append(FIXTURE_WORLD, "plan.solve", actor=CHAT, args={"sloops": 2}, text="solved it")
    push_ops(fresh_state_client, key, 1, put_op("export_minimums", RIP, 15))
    body = fresh_state_client.get("/api/activity").json()
    kinds = [(e["source"], e["kind"]) for e in body["entries"]]
    assert kinds == [("plan", "commit"), ("journal", "plan.solve"), ("plan", "commit")]
    solve = body["entries"][1]
    assert solve["actor"]["display"] == "Claude Code" and solve["args"] == {"sloops": 2}
    assert body["entries"][2]["name"] == "rip 5" and body["entries"][2]["rev"] == 2
    since = body["entries"][1]["ts"]
    later = fresh_state_client.get(f"/api/activity?since={since}").json()["entries"]
    assert [e["rev"] for e in later] == [2]
    assert len(fresh_state_client.get("/api/activity?limit=1").json()["entries"]) == 1


def test_repeat_looks_at_one_item_collapse_to_the_newest(fresh_state_client):
    journal.set_writer("web")
    key = create_plan(fresh_state_client)["key"]
    look = {"view": "alternates", "item": "Desc_IronPlateReinforced_C"}
    for n in range(60):
        journal.append(
            FIXTURE_WORLD, "plan.view", actor=CHAT, plan=key, args=look, text=f"look {n}"
        )
    other = {**look, "item": "Desc_IronPlate_C"}
    journal.append(FIXTURE_WORLD, "plan.view", actor=CHAT, plan=key, args=other, text="plate")
    rows = fresh_state_client.get("/api/activity?limit=50").json()["entries"]
    assert [(r["kind"], r["text"]) for r in rows] == [
        ("commit", rows[0]["text"]),
        ("plan.view", "look 59"),
        ("plan.view", "plate"),
    ]
    assert [r["count"] for r in rows] == [1, 60, 1]


def test_a_run_of_finder_calls_is_one_row_with_a_count_and_the_latest_params(fresh_state_client):
    journal.set_writer("web")
    key = create_plan(fresh_state_client)["key"]
    for n in range(70):
        params = {"resource": "Desc_OreCopper_C", "at": "hub", "within_m": str(500 + n)}
        journal.append(
            FIXTURE_WORLD,
            "world.find",
            actor=CHAT,
            args={"view": "rank", "params": params},
            text=f"find {n}",
        )
    journal.append(
        FIXTURE_WORLD,
        "world.find",
        actor=Actor("chat", "other", 7),
        args={"view": ""},
        text="elsewhere",
    )
    rows = fresh_state_client.get("/api/activity?limit=50").json()["entries"]
    assert [(r["kind"], r["text"], r["count"]) for r in rows] == [
        ("commit", rows[0]["text"], 1),
        ("world.find", "find 69", 70),
        ("world.find", "elsewhere", 1),
    ]
    assert rows[1]["args"]["params"]["within_m"] == "569"
    assert rows[0]["plan"] == key

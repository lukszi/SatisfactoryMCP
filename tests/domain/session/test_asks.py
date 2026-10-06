"""The asks store (docs/planner-p4_contract.md §4): numbers, revs, limits, seen and answered."""

from __future__ import annotations

import json
import threading

import pytest

from satisfactory_mcp.core.schema import NewerSchema
from satisfactory_mcp.domain.planning import asks
from satisfactory_mcp.domain.planning.planlog import Actor, PlanLog

WORLD = "W1"
ABOUT = {"kind": "process", "label": "Blender · Diluted Fuel", "ref": "job:recipe|B|R"}


def test_a_missing_file_reads_empty_and_is_not_created():
    assert asks.read(WORLD) == {"schema": 1, "version": 0, "next": 1, "asks": []}
    assert asks.live(WORLD) == []
    assert not asks.path_for(WORLD).exists()


def test_create_numbers_from_one_and_the_row_carries_its_copy():
    row = asks.create(WORLD, "  why a Blender?  ", ABOUT)
    assert row["n"] == 1 and row["id"] == "ask:1" and row["rev"] == 1
    assert row["text"] == "why a Blender?" and row["copy"] == "ask:1 why a Blender?"
    assert row["state"] == "open" and row["seen"] is None and row["answered"] is None
    assert row["about"] == {**ABOUT, "plan": None, "rev": None}
    assert row["plan_name"] is None
    assert asks.read(WORLD)["version"] == 1


def test_numbers_are_never_reused_after_a_delete():
    first = asks.create(WORLD, "one", ABOUT)
    asks.create(WORLD, "two", ABOUT)
    dropped = asks.drop(WORLD, 2, 1)
    assert dropped["rev"] == 2
    assert asks.create(WORLD, "three", ABOUT)["n"] == 3
    assert [r["n"] for r in asks.live(WORLD)] == [first["n"], 3]
    assert asks.read(WORLD)["version"] == 4


def test_a_drop_on_an_old_rev_is_stale_and_writes_nothing():
    asks.create(WORLD, "one", ABOUT)
    asks.mark_seen(WORLD, [1], "Claude Code")
    version = asks.read(WORLD)["version"]
    with pytest.raises(asks.AskStale) as caught:
        asks.drop(WORLD, 1, 1)
    assert str(caught.value) == "ask:1 changed since you read it"
    assert caught.value.ask["rev"] == 2
    assert asks.read(WORLD)["version"] == version


def test_unknown_and_deleted_asks_say_which():
    with pytest.raises(asks.AskMissing, match=r"ask:4 does not exist \(no asks yet\)"):
        asks.drop(WORLD, 4, 1)
    asks.create(WORLD, "one", ABOUT)
    asks.drop(WORLD, 1, 1)
    with pytest.raises(asks.AskMissing, match="ask:1 was deleted"):
        asks.drop(WORLD, 1, 2)
    with pytest.raises(asks.AskMissing, match=r"ask:9 does not exist \(asks run to ask:1\)"):
        asks.drop(WORLD, 9, 1)


@pytest.mark.parametrize(
    "text, message",
    [("", "needs a question"), ("   ", "needs a question"), ("x" * 201, "at most 200")],
)
def test_text_limits(text, message):
    with pytest.raises(asks.AskError, match=message):
        asks.create(WORLD, text, ABOUT)
    assert asks.create(WORLD, "x" * 200, ABOUT)["text"] == "x" * 200


@pytest.mark.parametrize(
    "about, message",
    [
        ({"kind": "machine", "label": "x", "ref": ""}, "about.kind"),
        ({"kind": "plan", "label": " ", "ref": ""}, "about.label cannot be blank"),
        ({"kind": "plan", "label": "x" * 121, "ref": ""}, "at most 120"),
        ({"kind": "plan", "label": "x", "ref": "r" * 201}, "at most 200"),
        ({"kind": "plan", "label": "x", "ref": "", "rev": 0}, "about.rev"),
        ("plan", "about must be an object"),
    ],
)
def test_a_bad_about_is_refused(about, message):
    with pytest.raises(asks.AskError, match=message):
        asks.create(WORLD, "q", about)


def test_about_plan_must_be_a_live_plan_and_resolves_its_name():
    with pytest.raises(asks.AboutMissing, match="no plan “0000beef”"):
        asks.create(WORLD, "q", {**ABOUT, "plan": "0000beef"})
    made = PlanLog(WORLD).create("north hmf", {}, actor=Actor("page"))
    row = asks.create(WORLD, "q", {**ABOUT, "plan": made.key, "rev": 1})
    assert row["about"]["plan"] == made.key and row["plan_name"] == "north hmf"
    PlanLog(WORLD).push(made.key, 1, [{"op": "rename", "name": "south"}], actor=Actor("page"))
    assert asks.live(WORLD)[0]["plan_name"] == "south"
    PlanLog(WORLD).push(made.key, 2, [{"op": "forget"}], actor=Actor("page"))
    with pytest.raises(asks.AboutMissing):
        asks.create(WORLD, "q", {**ABOUT, "plan": made.key})


def test_at_most_200_live_asks(monkeypatch):
    monkeypatch.setattr(asks, "MAX_LIVE", 3)
    for i in range(3):
        asks.create(WORLD, f"q{i}", ABOUT)
    with pytest.raises(asks.AskError, match="already has 3 asks"):
        asks.create(WORLD, "one more", ABOUT)
    asks.drop(WORLD, 2, 1)
    assert asks.create(WORLD, "now fits", ABOUT)["n"] == 4


def test_a_newer_schema_is_refused_and_a_torn_file_reads_empty():
    path = asks.path_for(WORLD)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"schema": 2, "asks": []}), encoding="utf-8")
    with pytest.raises(NewerSchema):
        asks.read(WORLD)
    with pytest.raises(NewerSchema):
        asks.create(WORLD, "q", ABOUT)
    path.write_text('{"schema": 1, "asks": [{"n": 1', encoding="utf-8")
    assert asks.read(WORLD)["asks"] == []


def test_mark_seen_is_idempotent_and_skips_answered_and_deleted():
    for text in ("a", "b", "c"):
        asks.create(WORLD, text, ABOUT)
    asks.drop(WORLD, 3, 1)
    asks.mark_answered(WORLD, [2], "Claude Code")
    assert asks.mark_seen(WORLD, [1, 2, 3], "Claude Code") == [1]
    version = asks.read(WORLD)["version"]
    assert asks.mark_seen(WORLD, [1, 2, 3], "Claude Code") == []
    assert asks.read(WORLD)["version"] == version
    first = asks.live(WORLD)[0]
    assert first["state"] == "seen" and first["seen_by"] == "Claude Code" and first["rev"] == 2
    assert asks.mark_seen(WORLD, [], "x") == []


def test_mark_answered_refuses_unknown_and_deleted_and_writes_nothing():
    asks.create(WORLD, "a", ABOUT)
    asks.create(WORLD, "b", ABOUT)
    asks.drop(WORLD, 2, 1)
    version = asks.read(WORLD)["version"]
    with pytest.raises(asks.AskMissing, match="ask:2 was deleted"):
        asks.mark_answered(WORLD, [1, 2], "Claude Code")
    with pytest.raises(asks.AskMissing, match="ask:7 does not exist"):
        asks.mark_answered(WORLD, [7], "Claude Code")
    assert asks.read(WORLD)["version"] == version
    assert asks.mark_answered(WORLD, [1, 1], "Claude Code") == [1]
    assert asks.mark_answered(WORLD, [1], "Claude Code") == []
    row = asks.live(WORLD)[0]
    assert row["state"] == "answered" and row["answered_by"] == "Claude Code"


def test_an_answer_line_is_kept_and_can_be_replaced():
    asks.create(WORLD, "why a Blender?", ABOUT)
    assert asks.mark_answered(WORLD, [1], "Claude Code", {1: "it makes the fuel"}) == [1]
    row = asks.live(WORLD)[0]
    assert row["state"] == "answered" and row["answer"] == "it makes the fuel"
    answered = row["answered"]
    assert asks.mark_answered(WORLD, [1], "Claude Code", {1: "it makes the fuel"}) == []
    assert asks.mark_answered(WORLD, [1], "Claude Code") == []
    assert asks.mark_answered(WORLD, [1], "Claude Code", {1: "diluted fuel"}) == [1]
    row = asks.live(WORLD)[0]
    assert row["answer"] == "diluted fuel" and row["answered"] == answered and row["rev"] == 3


def test_an_ask_without_an_answer_line_reads_blank():
    asks.create(WORLD, "a", ABOUT)
    asks.mark_answered(WORLD, [1], "Claude Code")
    assert asks.live(WORLD)[0]["answer"] == ""


def test_parse_answer_takes_an_id_and_one_line():
    assert asks.parse_answer("ask:7") == (7, "")
    assert asks.parse_answer(" ASK:7: yes,\n stage 1 ") == (7, "yes, stage 1")
    assert asks.parse_answer("ask:12 - no") == (12, "no")
    assert asks.parse_answer("ask:7x") is None and asks.parse_answer("pin:1 a") is None
    n, line = asks.parse_answer("ask:1 " + "z" * 500)
    assert n == 1 and len(line) == asks.ANSWER_MAX and line.endswith("…")


def test_parse_reads_ask_ids_case_insensitively():
    assert asks.parse("ask:7") == 7 and asks.parse(" ASK:12 ") == 12
    assert asks.parse("pin:7") is None and asks.parse("ask:x") is None and asks.parse(7) is None


def test_two_writers_under_the_lock_lose_nothing():
    errors = []

    def writer(tag):
        try:
            for i in range(10):
                asks.create(WORLD, f"{tag}{i}", ABOUT)
        except Exception as exc:
            errors.append(exc)

    threads = [threading.Thread(target=writer, args=(t,)) for t in "ab"]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert not errors
    data = asks.read(WORLD)
    assert sorted(a["n"] for a in data["asks"]) == list(range(1, 21))
    assert data["version"] == 20 and data["next"] == 21

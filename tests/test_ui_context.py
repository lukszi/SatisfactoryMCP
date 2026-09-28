"""``ui_context`` (contract §10.4): what the page has open, and what changed since this chat looked.

The page's focus file belongs to the web group; here it is stubbed at ``_page_focus``, and read
through the real ``focus`` module once that exists.
"""

from __future__ import annotations

import json
import os
import time

import pytest

from satisfactory_mcp import server as srv
from satisfactory_mcp.domain.planning import asks, journal
from satisfactory_mcp.domain.planning.planlog import Actor, PlanLog
from satisfactory_mcp.interfaces.mcp.tools import planning

PAGE = Actor("page", "", 4242)
OTHER_CHAT = Actor("chat", "claude-code", 999_999)
ME = Actor("chat", "claude-code", os.getpid())
WORLD = "TESTWORLD"


class _World:
    world_id = WORLD
    age_note = "test world"

    def __init__(self) -> None:
        self.header: dict = {"session_name": "Spire"}

    @property
    def plans(self):
        return PlanLog(WORLD).view()


@pytest.fixture
def ctx(tmp_path, monkeypatch):
    from satisfactory_mcp.domain.planning import store as store_mod

    monkeypatch.setattr(store_mod.config, "plans_dir", lambda: tmp_path / "plans")
    monkeypatch.setattr(journal.config, "activity_dir", lambda: tmp_path / "activity")
    monkeypatch.setattr(journal.config, "pins_dir", lambda: tmp_path / "pins")
    monkeypatch.setattr(journal.config, "asks_dir", lambda: tmp_path / "asks")
    monkeypatch.setattr(journal, "_writer", "")
    monkeypatch.setattr(journal, "_seq", {})
    monkeypatch.setattr(planning, "_state", lambda *a, **k: _World())
    monkeypatch.setattr(planning, "_sav", lambda st: "sav:3f2a91c0aa11")
    monkeypatch.setattr(planning, "_cursor", {})
    monkeypatch.setattr(planning, "_page_focus", lambda world_id: (None, False))
    return tmp_path


def _focus(monkeypatch, age_s: float, is_open: bool, **extra) -> None:
    focus = {
        "schema": 1,
        "heartbeat": time.time() - age_s,
        "view": "planner",
        "tab": "workbench",
        "follow": "follow",
        "sav": "sav:3f2a91c0aa11",
        "selection": None,
        **extra,
    }
    monkeypatch.setattr(planning, "_page_focus", lambda world_id: (focus, is_open))


def test_a_page_that_never_opened_says_so(ctx):
    out = srv.ui_context()
    assert out.splitlines()[0] == '# page never opened for this world · world "Spire"'
    assert "(first look this session: last 5): nothing new" in out


def test_an_open_page_names_plan_version_tab_and_selection(ctx, monkeypatch):
    made = PlanLog(WORLD).create("north hmf", {}, actor=PAGE)
    _focus(
        monkeypatch,
        4,
        True,
        dash=f"planner/{made.key}",
        plan=made.key,
        rev=1,
        selection={"kind": "process", "label": "Blender · Diluted Fuel", "ref": "Recipe_X_C"},
    )
    lines = srv.ui_context().splitlines()
    assert lines[0].startswith('# page open (heartbeat 4s ago) · world "Spire"')
    assert lines[0].endswith("page sav:3f2a… = yours")
    assert lines[1] == (
        'focus: planner › "north hmf" v1 › workbench   selected: process "Blender · Diluted Fuel"'
    )
    assert lines[2] == "follow: follow"


def test_a_page_on_another_save_and_behind_the_head_is_flagged(ctx, monkeypatch):
    made = PlanLog(WORLD).create("north hmf", {}, actor=PAGE)
    PlanLog(WORLD).push(made.key, 1, [{"op": "set", "field": "sloops", "value": 1}], actor=PAGE)
    _focus(monkeypatch, 1, True, plan=made.key, rev=1, sav="sav:000000000000")
    out = srv.ui_context()
    assert "page sav:0000… ≠ yours (sav:3f2a…)" in out
    assert '"north hmf" v1 (head v2)' in out


def test_a_stale_heartbeat_reads_as_closed(ctx, monkeypatch):
    _focus(monkeypatch, 3 * 3600, False, view="map", dash="")
    out = srv.ui_context()
    assert out.startswith("# page closed (last heartbeat 3h ago)")
    assert "last focus: map" in out


def test_the_first_look_shows_the_last_five_and_hides_this_process(ctx):
    log = PlanLog(WORLD)
    key = log.create("north hmf", {}, actor=PAGE).key
    for n in range(1, 8):
        log.push(key, n, [{"op": "set", "field": "sloops", "value": n}], actor=PAGE)
    log.push(key, 8, [{"op": "set", "field": "notes", "value": "mine"}], actor=ME)
    out = srv.ui_context()
    assert "(first look this session: last 5)" in out
    assert '"north hmf" v3 -> v9 by page: v4 sloops 2→3' in out
    assert "notes changed" not in out


def test_the_cursor_advances_and_only_news_is_shown(ctx):
    log = PlanLog(WORLD)
    key = log.create("north hmf", {}, actor=PAGE).key
    srv.ui_context()
    assert srv.ui_context().endswith("since you last looked: nothing new")

    log.push(
        key,
        1,
        [{"op": "put", "field": "export_minimums", "item": "Heavy Modular Frame", "value": 15.0}],
        actor=PAGE,
    )
    log.push(
        key,
        2,
        [{"op": "add", "field": "banned", "member": "Recipe_Alternate_BoltedFrame_C"}],
        actor=OTHER_CHAT,
    )
    out = srv.ui_context()
    assert "first look" not in out
    assert (
        '"north hmf" v1 -> v3 by page, Claude Code: v2 +rate Heavy Modular Frame 15/min · '
        "v3 +banned"
    ) in out
    assert srv.ui_context().endswith("nothing new")


def test_journal_entries_from_other_sessions_are_listed_and_own_are_not(ctx):
    journal.set_writer("chat")
    journal.append(WORLD, "plan.solve", actor=OTHER_CHAT, text="solved HMF 15/min (min_machines)")
    journal.append(WORLD, "plan.solve", actor=ME, text="my own solve")
    out = srv.ui_context()
    assert "journal: " in out
    assert "Claude Code (other session) solved HMF 15/min (min_machines)" in out
    assert "my own solve" not in out
    assert srv.ui_context().endswith("nothing new")


def test_a_busy_world_stays_inside_the_budget(ctx):
    log = PlanLog(WORLD)
    for n in range(30):
        key = log.create(f"plan number {n} with a long name", {}, actor=PAGE).key
        for rev in range(1, 10):
            log.push(
                key,
                rev,
                [{"op": "add", "field": "sources", "member": f"region:Somewhere {rev}"}],
                actor=PAGE,
            )
    srv.ui_context()
    for n in range(30):
        key = log.find(f"plan number {n} with a long name").key
        for rev in range(10, 20):
            log.push(
                key,
                rev,
                [{"op": "add", "field": "sources", "member": f"region:Elsewhere {rev}"}],
                actor=PAGE,
            )
    journal.set_writer("chat")
    for n in range(40):
        journal.append(WORLD, "plan.solve", actor=OTHER_CHAT, text="solved " + "x" * 150)
    out = srv.ui_context()
    assert len(out) < 4000
    assert "(+22 more plans" in out
    assert "(+4 more)" in out
    assert "(+32 earlier)" in out


def test_the_real_focus_file_is_read_when_the_web_module_exists(ctx, monkeypatch):
    focus = pytest.importorskip("satisfactory_mcp.domain.planning.focus")
    monkeypatch.undo()
    monkeypatch.setattr(focus.config, "ui_dir", lambda: ctx / "ui")
    (ctx / "ui").mkdir()
    (ctx / "ui" / f"{WORLD}.json").write_text(
        json.dumps(
            {
                "schema": 1,
                "heartbeat": time.time() - 2,
                "view": "planner",
                "dash": "planner",
                "plan": None,
                "rev": None,
                "tab": "list",
                "selection": None,
                "follow": "toasts",
                "sav": "",
            }
        ),
        encoding="utf-8",
    )
    found, is_open = planning._page_focus(WORLD)
    assert is_open and found["follow"] == "toasts"


def _pins(ctx, rows: list[dict]) -> None:
    (ctx / "pins").mkdir(exist_ok=True)
    stored = [
        {"rev": 1, "created": 0.0, "deleted": False, "label": "", "x_m": None, "y_m": None, **row}
        for row in rows
    ]
    body = {"schema": 1, "version": len(rows), "next": len(rows) + 1, "pins": stored}
    (ctx / "pins" / f"{WORLD}.json").write_text(json.dumps(body), encoding="utf-8")


def test_no_pins_says_none(ctx):
    assert "pins: none" in srv.ui_context().splitlines()


def test_pins_are_listed_and_a_pinned_selection_names_its_pin(ctx, monkeypatch):
    made = PlanLog(WORLD).create("north hmf", {}, actor=PAGE)
    _pins(
        ctx,
        [
            {"n": 1, "kind": "point", "ref": {"x_m": 1.0, "y_m": 2.0}, "x_m": 1.0, "y_m": 2.0},
            {"n": 2, "kind": "process", "ref": {"plan": made.key, "recipe": "Recipe_X_C"}},
            {"n": 3, "kind": "factory", "ref": {"factory": "gone factory"}, "label": "old"},
        ],
    )
    _focus(
        monkeypatch,
        2,
        True,
        plan=made.key,
        rev=1,
        tab="graph",
        selection={"kind": "process", "label": "Blender · Diluted Fuel", "ref": "Recipe_X_C"},
    )
    lines = srv.ui_context().splitlines()
    assert lines[1].endswith('selected: process "Blender · Diluted Fuel" (pin:2)')
    assert lines[3] == (
        "pins: pin:1 point 1, 2 · pin:2 process Recipe_X_C in “north hmf” · "
        "pin:3 factory “gone factory” “old” (gone)"
    )


def test_only_the_newest_eight_pins_show_and_the_reply_stays_in_budget(ctx):
    _pins(
        ctx,
        [
            {
                "n": n,
                "kind": "point",
                "ref": {"x_m": float(n), "y_m": 2.0},
                "x_m": float(n),
                "y_m": 2.0,
                "label": "a very long label " * 5,
            }
            for n in range(1, 21)
        ],
    )
    out = srv.ui_context()
    line = next(x for x in out.splitlines() if x.startswith("pins: "))
    assert line.startswith("pins: pin:13 ") and line.endswith(" (+12 more)")
    parts = line.removeprefix("pins: ").removesuffix(" (+12 more)").split(" · ")
    assert len(parts) == 8 and all(len(part) <= 90 for part in parts)
    assert len(out) < planning.CONTEXT_BUDGET


def test_a_plan_created_since_the_last_look_reads_as_new(ctx):
    srv.ui_context()
    PlanLog(WORLD).create("fresh", {}, actor=PAGE)
    assert '"fresh" new -> v1 by page: v1 created' in srv.ui_context()


# ------------------------------------------------------------------ asks (P4)

ABOUT = {"kind": "stage", "label": "stage 1", "ref": "1"}


def _asks_line(out: str) -> str:
    return next(line for line in out.splitlines() if line.startswith("asks"))


def test_no_asks_says_none_waiting_and_prints_no_hint(ctx):
    out = srv.ui_context()
    assert "asks: none waiting" in out.splitlines()
    assert "ui_context(answered=" not in out


def test_asks_are_listed_with_what_they_are_about_and_marked_seen(ctx):
    journal.set_writer("chat")
    made = PlanLog(WORLD).create("north hmf", {}, actor=PAGE)
    about = {
        "kind": "process",
        "label": "Blender · Diluted Fuel",
        "ref": "job:recipe|B|R",
        "plan": made.key,
        "rev": 1,
    }
    asks.create(WORLD, "why does this need a Blender?", about)
    asks.create(WORLD, "is stage 1 safe to switch on?", ABOUT)
    lines = srv.ui_context().splitlines()
    line = next(x for x in lines if x.startswith("asks"))
    assert line == (
        'asks (2 waiting): ask:1 "why does this need a Blender?" about process '
        '"Blender · Diluted Fuel" in "north hmf" v1 · ask:2 "is stage 1 safe to switch on?" '
        'about stage "stage 1"'
    )
    hint = lines[lines.index(line) + 1]
    assert (
        hint
        == 'answer them, then ui_context(answered=["ask:1", "ask:2"]) marks them done on the page'
    )
    rows = asks.live(WORLD)
    assert [r["state"] for r in rows] == ["seen", "seen"]
    assert rows[0]["seen_by"] == "chat"
    [entry] = [e for e in journal.read(WORLD) if e["kind"] == "ask.seen"]
    assert entry["args"] == {"n": [1, 2]} and entry["text"] == "chat saw ask:1, ask:2"
    again = srv.ui_context()
    assert _asks_line(again).endswith('about stage "stage 1" (seen)')
    assert len([e for e in journal.read(WORLD) if e["kind"] == "ask.seen"]) == 1


def test_an_ask_about_a_plan_names_the_plan_once(ctx):
    made = PlanLog(WORLD).create("north hmf", {}, actor=PAGE)
    about = {"kind": "plan", "label": "north hmf", "ref": made.key, "plan": made.key, "rev": 1}
    asks.create(WORLD, "what is left?", about)
    assert _asks_line(srv.ui_context()).endswith('about plan "north hmf" v1')


def test_only_the_newest_six_asks_show(ctx):
    for n in range(9):
        asks.create(WORLD, f"question {n}", ABOUT)
    line = _asks_line(srv.ui_context())
    assert line.startswith("asks (9 waiting): ask:4 ")
    assert line.endswith("(+3 more)") and "ask:3 " not in line
    assert [r["state"] for r in asks.live(WORLD)] == ["open"] * 3 + ["seen"] * 6


def test_answered_marks_them_done_and_says_so_first(ctx):
    journal.set_writer("chat")
    for text in ("a", "b", "c"):
        asks.create(WORLD, text, ABOUT)
    asks.drop(WORLD, 3, 1)
    lines = srv.ui_context(answered=["ask:1", "ASK:9", "ask:3", "nonsense"]).splitlines()
    assert lines[1] == "marked answered: ask:1"
    assert lines[2:5] == [
        "! ask:9 does not exist (asks run to ask:3)",
        "! ask:3 was deleted",
        "! 'nonsense' is not an ask id (ask:N)",
    ]
    assert [r["state"] for r in asks.live(WORLD)] == ["answered", "seen"]
    [entry] = [e for e in journal.read(WORLD) if e["kind"] == "ask.answered"]
    assert entry["args"] == {"n": [1]} and entry["text"] == "chat answered ask:1"
    assert next(x for x in lines if x.startswith("asks")).startswith("asks (1 waiting): ask:2 ")


def test_many_long_asks_stay_inside_the_budget(ctx):
    for n in range(200):
        asks.create(WORLD, f"{n} " + "y" * 190, {**ABOUT, "label": "l" * 120})
    out = srv.ui_context()
    assert len(out) < planning.CONTEXT_BUDGET
    line = _asks_line(out)
    assert line.startswith("asks (200 waiting): ") and line.endswith(" (+194 more)")
    parts = line.removeprefix("asks (200 waiting): ").removesuffix(" (+194 more)").split(" · ")
    assert len(parts) == 6 and all(len(p) <= planning.CONTEXT_ASK_WIDTH for p in parts)

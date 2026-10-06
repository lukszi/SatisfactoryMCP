"""``ui_context``: what the web page has open, and what changed since chat last looked."""

from __future__ import annotations

import json
import os
import time
from datetime import UTC, datetime
from typing import Annotated, NamedTuple

from mcp.server.fastmcp import Context
from pydantic import Field

from .....core.filelock import LockTimeout
from .....core.schema import NewerSchema
from .....domain import advice
from .....domain.advice import store as advice_store
from .....domain.planning.stored.planlog import Actor, Commit, PlanLog, PlanLogError
from .....domain.session import asks, focus, journal, pins
from .....presenters.text import advice as advice_text
from .....presenters.text import primitives as render
from ... import app
from ._plan_log import _age, _ops_text, _world_plan_log

CONTEXT_PLANS = 8
CONTEXT_COMMITS = 6
CONTEXT_JOURNAL = 8
FIRST_LOOK_ENTRIES = 5
CONTEXT_BUDGET = 3800
CONTEXT_PINS = 8
CONTEXT_PIN_WIDTH = 90
CONTEXT_ASKS = 6
CONTEXT_ASK_WIDTH = 200


class LastLook(NamedTuple):
    """How far this session has read: the journal's last entry, and each plan's version."""

    journal_ts: float
    revs: dict[str, int]


_cursor: dict[str, LastLook] = {}


def _page_focus(world_id: str) -> tuple[dict | None, bool]:
    """What the page last said it had open, and whether its heartbeat is fresh."""
    found = focus.read(world_id)
    return found, focus.is_open(found)


def _short_token(token: str) -> str:
    return token[:8] + "…" if len(token) > 8 else token


def _head_line(st, page: dict | None, is_open: bool) -> str:
    """Whether the page is open, which world, and whether it reads the save chat reads."""
    shown_world = st.header.get("session_name") or st.world_id
    if page is None:
        return f'# page never opened for this world · world "{shown_world}"'
    beat = _age(time.time() - float(page.get("heartbeat") or 0))
    if is_open:
        head = f"# page open (heartbeat {beat} ago)"
    else:
        head = f"# page closed (last heartbeat {beat} ago)"
    head += f' · world "{shown_world}"'
    theirs = str(page.get("sav") or "")
    if theirs:
        ours = app.save_token(st)
        same = "= yours" if theirs == ours else f"≠ yours ({_short_token(ours)})"
        head += f" · page {_short_token(theirs)} {same}"
    return head


def _pin_text(pin: dict) -> str:
    text = f"{pin['id']} {pin['text']}"
    if pin["label"]:
        text += f" “{pin['label']}”"
    if pin["gone"]:
        text += " (gone)"
    return render.cut(text, CONTEXT_PIN_WIDTH)


def _pins_line(rows: list[dict]) -> str:
    if not rows:
        return "pins: none"
    shown = sorted(rows, key=lambda p: p["n"])[-CONTEXT_PINS:]
    line = "pins: " + " · ".join(_pin_text(p) for p in shown)
    if len(rows) > CONTEXT_PINS:
        line += f" (+{len(rows) - CONTEXT_PINS} more)"
    return line


def _selected_pin(page: dict, rows: list[dict]) -> str:
    """`` (pin:N)`` when the page's selection is a pinned thing, else ''."""
    picked = page.get("selection")
    if not isinstance(picked, dict) or not picked.get("kind") or not picked.get("label"):
        return ""
    found = pins.match(rows, str(picked["kind"]), str(picked.get("ref") or ""), page.get("plan"))
    return f" ({found['id']})" if found else ""


def _focus_line(page: dict, log: PlanLog) -> str:
    """The page's view, plan and version, tab and selection, as one line."""
    parts = [str(page.get("view") or "?")]
    if page.get("plan"):
        try:
            state = log.find(str(page["plan"]), include_forgotten=True)
        except PlanLogError:
            state = None
        rev = page.get("rev")
        label = f'"{state.name}"' if state is not None else f"plan {page['plan']}"
        label += f" v{rev}" if rev else ""
        if state is not None and rev and rev != state.rev:
            label += f" (head v{state.rev})"
        parts.append(label)
    elif page.get("dash") and page["dash"] != parts[0]:
        parts.append(str(page["dash"]))
    tab = str(page.get("tab") or "")
    if tab and tab != str(page.get("dash") or "").split("/")[0]:
        parts.append(tab)
    line = "focus: " + " › ".join(parts)
    picked = page.get("selection")
    if isinstance(picked, dict) and picked.get("label"):
        line += f'   selected: {picked.get("kind") or "item"} "{picked["label"]}"'
        if picked.get("ref"):
            line += f" ({picked['ref']})"
    return line


def _plan_news(
    log: PlanLog, cursor: LastLook | None, names: dict[str, str], my_pid: int
) -> tuple[list[str], dict]:
    """Plan versions others wrote since ``cursor``, a line per plan; and every plan's head."""
    heads = {s.key: s for s in log.heads(include_forgotten=True)}
    fresh: list[tuple[str, Commit]] = []
    for key in heads:
        after = cursor.revs.get(key, 0) if cursor else 0
        fresh += [(key, c) for c in log.commits(key, since=after) if c.actor.pid != my_pid]
    if cursor is None:
        fresh = sorted(fresh, key=lambda kc: kc[1].ts)[-FIRST_LOOK_ENTRIES:]
    by_plan: dict[str, list[Commit]] = {}
    for key, commit in fresh:
        by_plan.setdefault(key, []).append(commit)
    lines = []
    order = sorted(by_plan, key=lambda k: by_plan[k][-1].ts, reverse=True)
    for key in order[:CONTEXT_PLANS]:
        commits = sorted(by_plan[key], key=lambda c: c.rev)
        who = ", ".join(dict.fromkeys(c.actor.display() for c in commits))
        items = [
            f"v{c.rev} " + (_ops_text(c.ops, names) or c.note or "recorded")
            for c in commits[-CONTEXT_COMMITS:]
        ]
        if len(commits) > CONTEXT_COMMITS:
            items.insert(0, f"(+{len(commits) - CONTEXT_COMMITS} more)")
        state = heads[key]
        gone = " (forgotten)" if state.forgotten else ""
        start = f"v{commits[0].rev - 1}" if commits[0].rev > 1 else "new"
        lines.append(f'"{state.name}"{gone} {start} -> v{state.rev} by {who}: ' + " · ".join(items))
    if len(order) > CONTEXT_PLANS:
        lines.append(f"(+{len(order) - CONTEXT_PLANS} more plans: plan_log name=<plan>)")
    return lines, {key: state.rev for key, state in heads.items()}


def _quoted(value: str) -> str:
    return json.dumps(value, ensure_ascii=False)


def _ask_text(row: dict, adv_ids: dict[str, str] | None = None) -> str:
    about = row["about"]
    text = f"{row['id']} {_quoted(row['text'])} about {about['kind']} {_quoted(about['label'])}"
    if about["kind"] == "advice" and (adv_ids or {}).get(about["ref"]):
        text += f" ({adv_ids[about['ref']]})"
    if row["plan_name"] and about["kind"] != "plan":
        text += f" in {_quoted(row['plan_name'])}"
    if about.get("rev"):
        text += f" v{about['rev']}"
    if row["state"] == "seen":
        text += " (seen)"
    return render.cut(text, CONTEXT_ASK_WIDTH)


def _asks_lines(world_id: str, who: str, chat, adv_ids: dict[str, str] | None = None) -> list[str]:
    """The ``asks`` line and its hint, marking every listed open ask seen."""
    try:
        waiting = [r for r in asks.live(world_id) if r["state"] in ("open", "seen")]
    except (NewerSchema, OSError) as exc:
        return [f"asks: unreadable ({type(exc).__name__})"]
    if not waiting:
        return ["asks: none waiting"]
    shown = waiting[-CONTEXT_ASKS:]
    line = f"asks ({len(waiting)} waiting): " + " · ".join(_ask_text(r, adv_ids) for r in shown)
    if len(waiting) > CONTEXT_ASKS:
        line += f" (+{len(waiting) - CONTEXT_ASKS} more)"
    fresh = [r["n"] for r in shown if r["state"] == "open"]
    try:
        seen = asks.mark_seen(world_id, fresh, who) if fresh else []
    except (LockTimeout, NewerSchema, OSError):
        seen = []
    if seen:
        journal.append(
            world_id,
            "ask.seen",
            actor=chat,
            args={"n": seen},
            text="chat saw " + ", ".join(f"ask:{n}" for n in seen),
        )
    ids = ", ".join(f'"{r["id"]} <answer>"' for r in shown[:2])
    more = ", …" if len(shown) > 2 else ""
    hint = (
        f"answer them, then ui_context(answered=[{ids}{more}]) marks them done on the page "
        "with that one-line answer"
    )
    return [line, hint]


def _mark_answered(world_id: str, answered: list[str], who: str, chat) -> list[str]:
    """Mark ``answered`` asks done; the ``marked answered`` line and one line per refusal."""
    lines, wanted, said = [], [], {}
    try:
        data = asks.read(world_id)
    except NewerSchema as exc:
        return [f"! asks were saved by a newer version (schema {exc.found}); nothing marked"]
    top = data["next"] - 1
    by_n = {a["n"]: a for a in data["asks"]}
    for raw in answered:
        hit = asks.parse_answer(raw)
        n = hit[0] if hit else None
        if n is None:
            lines.append(f"! {raw!r} is not an ask id (ask:N)")
        elif n not in by_n:
            lines.append(f"! {asks.AskMissing(n, top=top)}")
        elif by_n[n].get("deleted"):
            lines.append(f"! {asks.AskMissing(n, deleted=True)}")
        else:
            wanted.append(n)
            if hit[1]:
                said[n] = hit[1]
    try:
        asks.mark_answered(world_id, wanted, who, said)
    except asks.AskMissing as exc:
        return [f"! {exc}; nothing marked", *lines]
    except (LockTimeout, NewerSchema, OSError) as exc:
        return [f"! asks are busy ({type(exc).__name__}); nothing marked", *lines]
    if wanted:
        journal.append(
            world_id,
            "ask.answered",
            actor=chat,
            args={"n": wanted},
            text="chat answered "
            + ", ".join(f"ask:{n}" + (f": {said[n]}" if n in said else "") for n in wanted),
        )
        lines.insert(0, "marked answered: " + ", ".join(f"ask:{n}" for n in wanted))
    return lines


def _hide_advice(st, dismissed: list[str], chat) -> list[str]:
    """Hide the advisories chat was asked to; one line for what was hidden, one per refusal."""
    lines, done = [], []
    try:
        cur = advice.current(st)
    except NewerSchema as exc:
        return [f"! hidden advisories were saved by a newer version (schema {exc.found})"]
    for raw in dismissed:
        hit = advice_text.parse_hide(raw)
        adv = cur.find(hit[0]) if hit else None
        if hit is None:
            lines.append(f'! {raw!r} is not an advisory id ("adv:3f9a" or "adv:3f9a snooze")')
            continue
        if adv is None:
            lines.append(f"! {hit[0]} does not fire on this save")
            continue
        _id, mode, hours = hit
        try:
            advice_store.hide(
                st.world_id,
                adv,
                mode,
                play_s=cur.play_s,
                by=chat.to_dict(),
                hours=hours,
                firing=[a.key for a in cur.items],
            )
        except (advice_store.AdviceError, LockTimeout, NewerSchema, OSError) as exc:
            lines.append(f"! {adv.id} not hidden: {exc}")
            continue
        verb = "dismissed" if mode == "dismiss" else "snoozed"
        what = verb if mode == "dismiss" else f"{verb} for {hours:g} h of play"
        journal.append(
            st.world_id,
            "advice.hide",
            actor=chat,
            args={"id": adv.id, "key": adv.key, "mode": mode},
            text=f"chat {verb} {adv.id} {advice.WORDS[adv.kind]}: {adv.text}",
        )
        done.append(f"{adv.id} ({what})")
    if done:
        lines.insert(0, "hidden on the page: " + ", ".join(done))
    return lines


def _advice_lines(st) -> tuple[list[str], dict[str, str]]:
    """The ``advice`` line and its hint, and every advisory key's id for the asks line."""
    try:
        cur = advice.current(st)
    except Exception as exc:
        return [f"advice: unavailable ({type(exc).__name__})"], {}
    return advice_text.context_lines(cur), {a.key: a.id for a in cur.items}


def _journal_who(raw: dict | None) -> str:
    who = Actor.from_dict(raw)
    return f"{who.display()} (other session)" if who.kind == "chat" else who.display()


def _journal_news(world_id: str, cursor: LastLook | None, my_pid: int) -> tuple[list[str], float]:
    """Journal entries others wrote since ``cursor``, a line each; and the newest timestamp."""
    since = cursor.journal_ts if cursor else 0.0
    entries = journal.read(world_id, since_ts=since, limit=500)
    last = max([since, *(float(e.get("ts") or 0) for e in entries)])
    theirs = [e for e in entries if int((e.get("actor") or {}).get("pid") or 0) != my_pid]
    if cursor is None:
        theirs = theirs[-FIRST_LOOK_ENTRIES:]
    lines = []
    if len(theirs) > CONTEXT_JOURNAL:
        lines.append(f"  · journal: (+{len(theirs) - CONTEXT_JOURNAL} earlier)")
    for entry in theirs[-CONTEXT_JOURNAL:]:
        when = datetime.fromtimestamp(float(entry.get("ts") or 0), UTC).astimezone()
        when = when.strftime("%H:%M")
        text = render.cut(str(entry.get("text") or ""), 160)
        lines.append(f"  · journal: {when} {_journal_who(entry.get('actor'))} {text}")
    return lines, last


def _since_lines(world_id: str, log: PlanLog) -> list[str]:
    """What others changed since this session last looked, moving the cursor past it."""
    my_pid = os.getpid()
    cursor = _cursor.get(world_id)
    try:
        plan_lines, revs = _plan_news(log, cursor, app.recipe_names(), my_pid)
    except PlanLogError as exc:
        plan_lines, revs = (
            [f"! plans could not be read: {exc}"],
            dict(cursor.revs if cursor else {}),
        )
    journal_lines, journal_ts = _journal_news(world_id, cursor, my_pid)
    _cursor[world_id] = LastLook(journal_ts, revs)

    label = "since you last looked"
    if cursor is None:
        label += f" (first look this session: last {FIRST_LOOK_ENTRIES})"
    if not plan_lines and not journal_lines:
        return [f"{label}: nothing new"]
    return [
        f"{label}: " + (plan_lines[0] if plan_lines else ""),
        *[f"  {line}" for line in plan_lines[1:]],
        *journal_lines,
    ]


@app.tool()
def ui_context(
    save: str | None = None,
    world: str | None = None,
    answered: Annotated[
        list[str] | None,
        Field(description='ask:N ids you have answered, each may add a line: "ask:7 <answer>"'),
    ] = None,
    dismissed: Annotated[
        list[str] | None,
        Field(description='adv: ids to hide on the page; "adv:3f9a snooze" hides for 1 h of play'),
    ] = None,
    ctx: Context | None = None,
) -> str:
    """What the web page has open, and what changed in plans since this session last looked.

    Call it first when the user says "this", "here" or "what I have open", or quotes an
    ask: or pin: id: it names the page's view, plan and version, tab and selection, whether
    the page reads the same save as you, the asks queued for you, and every plan version
    and chat solve by someone else since your last look. Asks it lists are marked seen on
    the page; pass ``answered`` once you have answered them. It lists the advisories worth a
    look (adv: ids); ``dismissed`` hides one on the page, only when the user asks.
    """
    st = app.load_world(save, world)
    world_id = st.world_id
    log = _world_plan_log(st)
    page, is_open = _page_focus(world_id)

    lines = [_head_line(st, page, is_open)]
    chat = app.actor(ctx)
    if answered:
        lines += _mark_answered(world_id, list(answered), chat.display(), chat)
    if dismissed:
        lines += _hide_advice(st, list(dismissed), chat)
    try:
        pin_rows = pins.live(st)
        pin_line = _pins_line(pin_rows)
    except Exception as exc:
        pin_rows, pin_line = [], f"pins: unreadable ({type(exc).__name__})"
    if page is not None:
        line = _focus_line(page, log) + _selected_pin(page, pin_rows)
        lines.append(line if is_open else "last " + line)
        lines.append(f"follow: {page.get('follow') or 'follow'}")
    lines.append(pin_line)
    advice_lines, adv_ids = _advice_lines(st)
    lines += _asks_lines(world_id, chat.display(), chat, adv_ids)
    lines += advice_lines
    lines += _since_lines(world_id, log)

    out = "\n".join(lines)
    if len(out) > CONTEXT_BUDGET:
        out = out[: CONTEXT_BUDGET - 40].rsplit("\n", 1)[0] + "\n(+more: plan_log name=<plan>)"
    return out

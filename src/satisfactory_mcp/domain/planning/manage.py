"""Plan management the MCP tools and the web share: status, versions, duplicate, result deltas.

docs/plan_management.md is the specification. Everything here reads or writes through
``planlog.PlanLog``; nothing edits a plan around it.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from ...core.gamedata.model import GameData
from ..world.state import WorldState
from . import provenance as prov
from .planlog import Actor, Commit, InvalidOp, NameTaken, PlanLog, Pushed, Stamp
from .scenario import build_scenario

__all__ = [
    "COPY_TRIES",
    "PlanStatus",
    "duplicate",
    "plan_status",
    "result_delta",
    "undone_by",
    "versions",
]

COPY_TRIES = 50
MINUS = "−"
_EPS = 1e-6


@dataclass
class PlanStatus:
    """What moved under a stored plan. An empty ``flags`` with ``recorded`` False is unchecked."""

    flags: list[str] = field(default_factory=list)
    drift: bool = False
    recorded: bool = True
    broken: bool = False


def plan_status(st: WorldState, plan, g: GameData | None = None) -> PlanStatus:
    """``plan`` is a ``Plan`` or a ``PlanState``: anything with ``kwargs``, ``plan_id``, ``provenance``."""
    out = PlanStatus()
    try:
        g = st.game if g is None else g
        if build_scenario(g, st, **plan.kwargs()).plan_id != plan.plan_id:
            out.flags.append("world moved")
        for drift in prov.compare(g, st, plan):
            out.flags.append(f"field {drift.then}->{drift.now}")
            out.drift = True
        out.recorded = prov.recorded(plan)
    except Exception as exc:
        out.flags.append(f"broken: {type(exc).__name__}")
        out.broken = True
    return out


def undone_by(commits: list[Commit]) -> dict[int, int]:
    """Undone rev -> the first commit that undid it."""
    out: dict[int, int] = {}
    for commit in commits:
        if commit.undoes is not None and commit.undoes not in out:
            out[commit.undoes] = commit.rev
    return out


def versions(log: PlanLog, key: str) -> list[dict]:
    """Every commit of one plan, newest first, with ``undone_by`` and ``restores``."""
    commits = log.commits(key)
    undone = undone_by(commits)
    rows = []
    for commit in reversed(commits):
        restores = None
        if commit.note.startswith("restore v"):
            try:
                restores = int(commit.note[len("restore v") :])
            except ValueError:
                restores = None
        rows.append(
            {
                "commit": commit,
                "undone_by": undone.get(commit.rev),
                "restores": restores,
            }
        )
    return rows


def _copy_name(log: PlanLog, base: str) -> str:
    for n in range(1, COPY_TRIES + 1):
        wanted = f"{base} (copy)" if n == 1 else f"{base} (copy {n})"
        try:
            return log.free_name(wanted)
        except NameTaken:
            continue
    raise InvalidOp(f"no free name for a copy of {base!r}; pass one")


def duplicate(
    log: PlanLog,
    key: str,
    *,
    actor: Actor,
    rev: int | None = None,
    name: str | None = None,
    sav: str = "",
    stamp: Stamp | None = None,
) -> Pushed:
    """A new plan at v1 equal to ``key`` at ``rev`` (the head when None): a ``create``, never a fork."""
    source = log.state(key, rev)
    wanted = log.free_name(name) if name and name.strip() else _copy_name(log, source.name)
    stamped = {"plan_id": source.plan_id, "provenance": source.provenance}
    if stamp is not None:
        try:
            stamped = stamp(source)
        except Exception:
            stamped = {"plan_id": "", "provenance": {}}
    return log.create(
        wanted,
        source.args,
        actor=actor,
        sav=sav,
        notes=source.notes,
        factory=source.factory,
        siting=source.siting or None,
        plan_id=stamped.get("plan_id", ""),
        provenance=stamped.get("provenance") or {},
        created=source.created,
        note=f"duplicated from {source.name!r} v{source.rev}"[:200],
    )


def _num(value: float, dp: int = 1) -> str:
    text = f"{abs(value):,.{dp}f}".rstrip("0").rstrip(".") if dp else f"{abs(value):,.0f}"
    return ("+" if value > 0 else MINUS if value < 0 else "") + text


def _machines(summary: dict) -> dict[str, int]:
    out: dict[str, int] = {}
    for row in summary.get("rows") or ():
        out[row["building"]] = out.get(row["building"], 0) + int(row["machines"])
    return out


def _rates(rows) -> dict[str, float]:
    out: dict[str, float] = {}
    for row in rows or ():
        out[row["item"]] = out.get(row["item"], 0.0) + float(row["per_min"])
    return out


def _changes(before: dict, after: dict, round_to: int) -> list[dict]:
    rows = []
    for name in sorted(set(before) | set(after)):
        was, now = before.get(name, 0), after.get(name, 0)
        diff = round(now - was, round_to)
        if abs(diff) > _EPS:
            rows.append({"name": name, "before": was, "after": now, "delta": diff})
    return sorted(rows, key=lambda r: (-abs(r["delta"]), r["name"]))


def result_delta(before: dict, after: dict) -> dict:
    """How two ``summary.solve_summary`` results differ: machines, MW and raw inputs.

    Facts only. When either side is not solvable, only that is said: the counts of an
    infeasible solve are zero, and a delta against zero would read as a real change.
    """
    both = bool(before.get("feasible")) and bool(after.get("feasible"))
    out = {
        "comparable": both,
        "machines": 0,
        "mw_draw": 0.0,
        "mw_net": 0.0,
        "buildings": [],
        "inputs": [],
        "text": "",
    }
    if not both:
        if before.get("feasible") and not after.get("feasible"):
            out["text"] = "no longer solvable"
        elif after.get("feasible") and not before.get("feasible"):
            out["text"] = "solvable again"
        else:
            out["text"] = "not solvable before or after"
        return out
    out["machines"] = int(after["machines"]) - int(before["machines"])
    out["mw_draw"] = round(float(after["mw_draw"] or 0) - float(before["mw_draw"] or 0), 2)
    out["mw_net"] = round(float(after["mw_net"] or 0) - float(before["mw_net"] or 0), 2)
    out["buildings"] = _changes(_machines(before), _machines(after), 0)
    out["inputs"] = _changes(_rates(before.get("inputs")), _rates(after.get("inputs")), 4)
    parts = [f"{_num(b['delta'], 0)} {b['name']}" for b in out["buildings"][:4]]
    if len(out["buildings"]) > 4:
        parts.append(f"+{len(out['buildings']) - 4} more buildings changed")
    if abs(out["mw_draw"]) >= 0.05:
        parts.append(f"{_num(out['mw_draw'])} MW draw")
    if abs(out["mw_net"] + out["mw_draw"]) >= 0.05:
        parts.append(f"{_num(out['mw_net'])} MW net")
    parts += [f"{i['name']} {_num(i['delta'])}/min" for i in out["inputs"][:3]]
    out["text"] = " · ".join(parts) or "no change in the result"
    return out

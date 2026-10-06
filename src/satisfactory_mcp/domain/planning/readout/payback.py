"""The payback trade-off of one solve: machines, draw and build cost at every horizon.

docs/planner-payback-horizon_contract.md is the specification. The solve is read out once
per stop by ``overclock.payback_curve``; this turns those readouts into what a player weighs.
"""

from __future__ import annotations

from typing import NamedTuple

from ....core import text
from ....core.gamedata.model import GameData
from ..layout.materials import cost_of
from ..solver.model import PAYBACK_STOPS

__all__ = ["no_overclock", "trade_text", "view"]


class _AddedCost(NamedTuple):
    #: ``{item, amount}`` rows, largest first.
    materials: list[dict]
    area_m2: float
    points: float


def _added_build_cost(
    g: GameData, added: dict[str, int], build_points: dict[str, float]
) -> _AddedCost:
    """What the machines a stop adds over the plain build cost: parts, floor and points."""
    amounts: dict[str, float] = {}
    area = spent = 0.0
    for cls, count in added.items():
        building = g.buildings.get(cls)
        if building is None or count <= 0:
            continue
        for item, amount in cost_of(g, cls, count).parts.items():
            amounts[item] = amounts.get(item, 0.0) + amount
        if building.footprint is not None:
            area += building.footprint.area_m2 * count
        spent += build_points.get(cls, 0.0) * count
    rows = [
        {"item": g.item_name(item), "amount": round(amount, 4)}
        for item, amount in sorted(amounts.items(), key=lambda kv: -kv[1])
    ]
    return _AddedCost(rows, round(area, 1), spent)


def _stops(g: GameData, sol, sc, machines: int, draw_mw: float) -> list[dict]:
    curve = [r for r in sol.payback_curve if not r.get("plain")]
    plain = next(r for r in sol.payback_curve if r.get("plain"))
    here = next(r for r in curve if r["hours"] == sc.payback_hours)
    out = []
    for r in curve:
        added = {
            cls: n - plain["buildings"].get(cls, 0)
            for cls, n in r["buildings"].items()
            if n > plain["buildings"].get(cls, 0)
        }
        cost = _added_build_cost(g, added, sc.build_points)
        saved = plain["draw_mw"] - r["draw_mw"]
        average = (
            cost.points / (saved * sc.power_price) if saved > 0.05 and sc.power_price else None
        )
        out.append(
            {
                "hours": r["hours"],
                "machines": round(machines + r["machines"] - here["machines"]),
                "mw_draw": round(draw_mw + r["draw_mw"] - here["draw_mw"], 2),
                "extra_machines": round(r["machines"] - plain["machines"]),
                "saved_mw": round(saved, 2),
                "cost": cost.materials,
                "area_m2": cost.area_m2,
                "points": round(cost.points),
                "average_payback_h": None if average is None else round(average, 2),
                "shards": r["shards"],
            }
        )
    return out


def view(
    g: GameData,
    sol,
    sc,
    machines: int,
    draw_mw: float,
    *,
    inherited: bool,
    default_hours: float,
    price_source: str,
    mix: list[dict],
    overclock_inherited: bool,
    shards: dict | None,
) -> dict:
    """Every stop of ``sol`` beside the horizon it was solved at, and the overclock pick."""
    base = {
        "hours": sc.payback_hours,
        "inherited": inherited,
        "default_hours": default_hours,
        "price": sc.power_price,
        "price_source": price_source,
        "mix": mix,
    }
    oc = dict(sol.overclock) if sol.overclock else no_overclock(sc.overclock_last)
    oc["inherited"] = overclock_inherited
    oc["shards_free"] = (shards or {}).get("free")
    oc["shards_craftable"] = (shards or {}).get("craftable")
    if not sol.payback_curve:
        return {**base, "splits": False, "reason": "", "stops": [], "overclock": oc}
    stops = _stops(g, sol, sc, machines, draw_mw)
    splits = any(s["extra_machines"] for s in stops)
    reason = ""
    if not splits:
        reason = (
            "power costs nothing to run on this grid"
            if sc.power_price <= 0
            else "nothing here to spread: extractors, generators and somersloop rows keep "
            "their count"
        )
    return {**base, "splits": splits, "reason": reason, "stops": stops, "overclock": oc}


def no_overclock(on: bool) -> dict:
    return {
        "on": on,
        "rows": [],
        "shards": 0,
        "machines_saved": 0,
        "extra_mw": 0.0,
        "without": [],
        "unused": [],
        "pinned_last": 0,
        "pinned_spread": 0,
    }


def _price_words(v: dict) -> str:
    if v["price_source"] == "plan":
        return f"{v['price']:,.0f} pts/MWh set on the plan"
    sources = ", ".join(f"{m['source']} {m['mw']:,.0f} MW" for m in v["mix"][:3])
    return f"grid mix {v['price']:,.0f} pts/MWh" + (f": {sources}" if sources else "")


def _signed(value: float, fmt: str) -> str:
    return ("−" if value < 0 else "+") + format(abs(value), fmt)


def _change(a: dict, b: dict) -> str:
    more = b["extra_machines"] - a["extra_machines"]
    saved = b["saved_mw"] - a["saved_mw"]
    return f"{_signed(more, ',')} machines, {_signed(-saved, ',.1f')} MW"


def _next_words(v: dict) -> str:
    stops = v["stops"]
    now = next(s for s in stops if s["hours"] == v["hours"])
    later = [s for s in stops if s["hours"] > v["hours"] and s["hours"] in PAYBACK_STOPS]
    if not later:
        return ""
    nxt = later[0]
    if nxt["extra_machines"] != now["extra_machines"]:
        return f"; {text.hours(nxt['hours'])} would change {_change(now, nxt)}"
    moved = next((s for s in later if s["extra_machines"] != now["extra_machines"]), None)
    if moved is None:
        return f"; {text.hours(nxt['hours'])} would change nothing, nor would any longer horizon"
    return (
        f"; {text.hours(nxt['hours'])} would change nothing, {text.hours(moved['hours'])} "
        f"{_change(now, moved)}"
    )


def stock_text(oc: dict) -> str:
    """`` (19 in hand + 411 craftable)``, or nothing when the save was not read."""
    free = oc.get("shards_free")
    if free is None:
        return ""
    return f" ({free:,.0f} in hand + {oc.get('shards_craftable') or 0:,.0f} craftable)"


def _sums(rows: list[dict]) -> tuple[int, int, float]:
    return (
        sum(r["shards"] for r in rows),
        sum(r["instead"] - r["machines"] for r in rows),
        sum(r["extra_mw"] for r in rows),
    )


def _overclock_words(oc: dict) -> list[str]:
    hand = stock_text(oc)
    lines = []
    rows = len(oc["rows"])
    own = [r for r in oc["rows"] if r.get("applied") and r.get("pinned") == "last"]
    spare = [r for r in oc["rows"] if not r.get("applied", oc["on"])]
    if oc["on"] and rows:
        lines.append(
            f"overclock last machine: {rows} row(s), {oc['shards']} shard(s){hand}: "
            f"−{oc['machines_saved']} machines, +{oc['extra_mw']:,.1f} MW"
        )
    elif own:
        shards, saved, extra = _sums(own)
        names = ", ".join(r["label"] for r in own)
        lines.append(
            f"overclock last set on its row: {names}: {shards} shard(s){hand}, "
            f"−{saved} machines, +{extra:,.1f} MW"
        )
    if not oc["on"] and spare:
        shards, saved, extra = _sums(spare)
        lines.append(
            f"overclock_last=true would save {saved} machine(s) for "
            f"{shards} shard(s){hand}, +{extra:,.1f} MW"
        )
    if oc.get("pinned_spread"):
        lines.append(
            f"one more underclocked machine set on {oc['pinned_spread']} row(s), "
            "whatever overclock_last says"
        )
    if (oc["on"] or oc.get("pinned_last")) and oc["without"]:
        lines.append(f"overclock last machine: {len(oc['without'])} row(s) went without shards")
    if oc["on"] and oc["unused"]:
        names = ", ".join(sorted({u["label"] for u in oc["unused"]}))
        lines.append(
            f"overclock unused on {names}: at this horizon its extra MW costs more than the "
            "machine it saves"
        )
    return lines


def trade_text(v: dict) -> list[str]:
    """Chat lines: this horizon against the plain build, the next stop, and the overclock."""
    lines = _overclock_words(v["overclock"])
    if not v["stops"]:
        return lines
    tag = " (default)" if v["inherited"] else ""
    head = f"payback {text.hours(v['hours'])}{tag} at {_price_words(v)}"
    if not v["splits"]:
        if v["hours"] or not v["inherited"]:
            return [f"{head}: {v['reason']}", *lines]
        return lines
    now = next(s for s in v["stops"] if s["hours"] == v["hours"])
    if now["extra_machines"]:
        more = _signed(now["extra_machines"], ",")
        saved = _signed(-now["saved_mw"], ",.1f")
        head += (
            f": {more} machines, {saved} MW against the plain build; "
            f"the last pays back within {text.hours(v['hours'])}"
        )
        if now["average_payback_h"] is not None:
            head += f", on average {text.hours(now['average_payback_h'])}"
    else:
        head += ": no extra machines"
    return [head + _next_words(v), *lines]

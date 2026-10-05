"""The payback trade-off of one solve: machines, draw and build cost at every horizon.

docs/planner-payback-horizon_contract.md is the specification. The solve is read out once
per stop by ``optimize._payback_curve``; this turns those readouts into what a player weighs.
"""

from __future__ import annotations

from ...core.gamedata.model import GameData
from .optimize import PAYBACK_STOPS

__all__ = ["MAX_HOURS", "STOPS", "hours_text", "no_overclock", "trade_text", "view"]

STOPS = PAYBACK_STOPS
MAX_HOURS = PAYBACK_STOPS[-1]


def hours_text(hours: float) -> str:
    """``"10 h"``, ``"7.5 h"``."""
    return f"{hours:,.1f}".rstrip("0").rstrip(".") + " h"


def _cost(g: GameData, added: dict[str, int], points: dict[str, float]) -> tuple:
    amounts: dict[str, float] = {}
    area = spent = 0.0
    for cls, n in added.items():
        b = g.buildings.get(cls)
        if b is None or n <= 0:
            continue
        for f in b.build_cost:
            amounts[f.item] = amounts.get(f.item, 0.0) + f.amount * n
        if b.footprint is not None:
            area += b.footprint.area_m2 * n
        spent += points.get(cls, 0.0) * n
    rows = [
        {"item": g.item_name(item), "amount": round(amount, 4)}
        for item, amount in sorted(amounts.items(), key=lambda kv: -kv[1])
    ]
    return rows, round(area, 1), spent


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
        cost, area, spent = _cost(g, added, sc.build_points)
        saved = plain["draw_mw"] - r["draw_mw"]
        average = spent / (saved * sc.power_price) if saved > 0.05 and sc.power_price else None
        out.append(
            {
                "hours": r["hours"],
                "machines": round(machines + r["machines"] - here["machines"]),
                "mw_draw": round(draw_mw + r["draw_mw"] - here["draw_mw"], 2),
                "extra_machines": round(r["machines"] - plain["machines"]),
                "saved_mw": round(saved, 2),
                "cost": cost,
                "area_m2": area,
                "points": round(spent),
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
    }


def _price_words(v: dict) -> str:
    if v["price_source"] == "plan":
        return f"{v['price']:,.0f} pts/MWh set on the plan"
    sources = ", ".join(f"{m['source']} {m['mw']:,.0f} MW" for m in v["mix"][:3])
    return f"grid mix {v['price']:,.0f} pts/MWh" + (f": {sources}" if sources else "")


def _change(a: dict, b: dict) -> str:
    more = b["extra_machines"] - a["extra_machines"]
    saved = b["saved_mw"] - a["saved_mw"]
    return f"{more:+,} machines, {-saved:+,.1f} MW"


def _next_words(v: dict) -> str:
    stops = v["stops"]
    now = next(s for s in stops if s["hours"] == v["hours"])
    later = [s for s in stops if s["hours"] > v["hours"] and s["hours"] in STOPS]
    if not later:
        return ""
    nxt = later[0]
    if nxt["extra_machines"] != now["extra_machines"]:
        return f"; {hours_text(nxt['hours'])} would change {_change(now, nxt)}"
    moved = next((s for s in later if s["extra_machines"] != now["extra_machines"]), None)
    if moved is None:
        return f"; {hours_text(nxt['hours'])} would change nothing, nor would any longer horizon"
    return (
        f"; {hours_text(nxt['hours'])} would change nothing, {hours_text(moved['hours'])} "
        f"{_change(now, moved)}"
    )


def _overclock_words(oc: dict) -> list[str]:
    free = oc.get("shards_free")
    hand = "" if free is None else f" ({free:,.0f} in hand"
    if hand and oc.get("shards_craftable"):
        hand += f", {oc['shards_craftable']:,.0f} more from slugs"
    hand += ")" if hand else ""
    lines = []
    rows = len(oc["rows"])
    if oc["on"] and rows:
        lines.append(
            f"overclock last machine: {rows} row(s), {oc['shards']} shard(s){hand}: "
            f"−{oc['machines_saved']} machines, +{oc['extra_mw']:,.1f} MW"
        )
    elif rows:
        lines.append(
            f"overclock_last=true would save {oc['machines_saved']} machine(s) for "
            f"{oc['shards']} shard(s){hand}, +{oc['extra_mw']:,.1f} MW"
        )
    if oc["on"] and oc["without"]:
        lines.append(f"overclock last machine: {len(oc['without'])} row(s) went without shards")
    if oc["on"] and oc["unused"]:
        names = ", ".join(sorted({u["building"] or u["label"] for u in oc["unused"]}))
        lines.append(f"overclock unused on {names}: at this horizon the extra MW costs more")
    return lines


def trade_text(v: dict) -> list[str]:
    """Chat lines: this horizon against 0 h, what the next stop changes, and the overclock."""
    lines = _overclock_words(v["overclock"])
    if not v["stops"]:
        return lines
    tag = " (default)" if v["inherited"] else ""
    head = f"payback {hours_text(v['hours'])}{tag} at {_price_words(v)}"
    if not v["splits"]:
        if v["hours"] or not v["inherited"]:
            return [f"{head}: {v['reason']}", *lines]
        return lines
    now = next(s for s in v["stops"] if s["hours"] == v["hours"])
    if now["extra_machines"]:
        head += (
            f": {now['extra_machines']:+,} machines, −{now['saved_mw']:,.1f} MW against 0 h; "
            f"the last pays back within {hours_text(v['hours'])}"
        )
        if now["average_payback_h"] is not None:
            head += f", on average {hours_text(now['average_payback_h'])}"
    else:
        head += ": no extra machines"
    return [head + _next_words(v), *lines]

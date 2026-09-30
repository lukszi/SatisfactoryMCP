"""The power-priority trade-off of one solve: machines, draw and build cost at every step.

docs/planner-power-priority_contract.md is the specification. The solve is read out once per
step by ``optimize._power_steps``; this turns those readouts into what a player weighs.
"""

from __future__ import annotations

from ...core.gamedata.model import GameData
from .optimize import POWER_PRIORITY_CLOCKS

__all__ = ["STEPS", "ladder", "step_label", "trade_text"]

STEPS = len(POWER_PRIORITY_CLOCKS)


def step_label(step: int) -> str:
    """``"50%"``: the clock cap a step puts on production machines."""
    return f"{POWER_PRIORITY_CLOCKS[step] * 100:.0f}%"


def _cost(g: GameData, added: dict[str, int]) -> tuple[list[dict], int]:
    amounts: dict[str, float] = {}
    foundations = 0
    for cls, n in added.items():
        b = g.buildings.get(cls)
        if b is None or n <= 0:
            continue
        for f in b.build_cost:
            amounts[f.item] = amounts.get(f.item, 0.0) + f.amount * n
        if b.footprint is not None:
            foundations += b.footprint.foundations * n
    rows = [
        {"item": g.item_name(item), "amount": round(amount, 4)}
        for item, amount in sorted(amounts.items(), key=lambda kv: -kv[1])
    ]
    return rows, foundations


def ladder(g: GameData, sol, step: int, machines: int, draw_mw: float) -> dict:
    """Every step of ``sol`` beside the one it was read at.

    ``machines`` and ``draw_mw`` are the plan's own headline figures at ``step``; each other
    step moves them by exactly what its readout differs, so the ladder agrees with the
    headline wherever the headline leaves a row out. ``cost`` and ``foundations`` are what
    the machines a step adds over step 0 take to build and to stand on.
    """
    readouts = sol.power_steps
    if len(readouts) != STEPS:
        return {"step": step, "splits": False, "steps": []}
    here, plain = readouts[step], readouts[0]
    out = []
    for r in readouts:
        added = {
            cls: n - plain["buildings"].get(cls, 0)
            for cls, n in r["buildings"].items()
            if n > plain["buildings"].get(cls, 0)
        }
        cost, foundations = _cost(g, added)
        out.append(
            {
                "step": r["step"],
                "max_clock": r["max_clock"],
                "machines": round(machines + r["machines"] - here["machines"]),
                "mw_draw": round(draw_mw + r["draw_mw"] - here["draw_mw"], 2),
                "extra_machines": round(r["machines"] - plain["machines"]),
                "saved_mw": round(plain["draw_mw"] - r["draw_mw"], 2),
                "cost": cost,
                "foundations": foundations,
            }
        )
    splits = any(r["extra_machines"] for r in out)
    return {"step": step, "splits": splits, "steps": out}


def trade_text(view: dict) -> str:
    """One line for chat: this step against the plain build, and where the next step goes."""
    steps = view["steps"]
    if not steps:
        return ""
    step = view["step"]
    if not view["splits"]:
        if not step:
            return ""
        return (
            "power priority: no production row here can be split further "
            "(already underclocked, somersloops, extractors or generators)"
        )
    now = steps[step]
    line = f"power priority {step} (machines at most {step_label(step)}): "
    if step:
        line += (
            f"{now['saved_mw']:,.1f} MW saved for {now['extra_machines']:,} extra machines "
            f"(+{now['foundations']:,} foundations)"
        )
    else:
        line += "the plain build"
    if step + 1 < STEPS:
        nxt = steps[step + 1]
        line += (
            f"; step {step + 1} ({step_label(step + 1)}) would save "
            f"{nxt['saved_mw'] - now['saved_mw']:,.1f} MW more for "
            f"{nxt['extra_machines'] - now['extra_machines']:,} more machines"
        )
    return line

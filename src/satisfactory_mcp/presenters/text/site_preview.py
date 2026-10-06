"""A site preview in chat's words: what a stored plan would meet at a candidate pad.

The preview itself is ``domain.planning.siting.preview``'s, shared with the page's drag;
docs/planner-p5_contract.md §4 is the specification. This only says it, one section a line.
"""

from __future__ import annotations

from ...core.text import plural
from ...domain.planning.siting.preview import WATER_SEARCH_M

__all__ = ["render_site_preview"]


def _terrain_lines(out: dict) -> list[str]:
    t = out["terrain"]
    if t is None:
        return [f"terrain: {out['terrain_note']}"]
    if t["z_min_m"] is None:
        return ["terrain: no data under the pad"]
    lines = [
        (
            f"terrain: ground {t['z_min_m']:g}…{t['z_max_m']:g} m · "
            f"slope {t['slope_mean_deg'] or 0:g}° (p90 {t['slope_p90_deg'] or 0:g}°) · "
            f"rough {t['roughness_m'] or 0:g} m · {t['submerged_pct']:g}% under water"
        )
    ]
    if t["cave_pct"]:
        lines.append(f"cave: one lies under {t['cave_pct']:g}% of the pad; heights are the surface")
    if t["water_m"] is None:
        lines.append(f"water: none within {WATER_SEARCH_M:.0f} m")
    elif t["water_m"] == 0:
        lines.append("water: on the pad")
    else:
        drop = "" if t["water_below_m"] is None else f", {t['water_below_m']:g} m below"
        lines.append(f"water: {t['water_m']:,.0f} m away{drop}")
    return lines


def _plan_lines(out: dict) -> list[str]:
    on_pad = f"{out['on_pad']} {plural('machine', out['on_pad'])}"
    lines = [f"on the pad now: {on_pad} (plan: {out['planned']}), by class only"]
    if out["trunks"]:
        run = sum(t["run_m"] for t in out["trunks"])
        leg = sum(t["to_site_m"] for t in out["trunks"])
        pumps = sum(1 for t in out["trunks"] if t["pumps"])
        lines.append(
            f"trunks: {len(out['trunks'])} · {run:,.0f} m node to node + {leg:,.0f} m to the pad"
            f" (straight lines, lower bounds) · {pumps} need pumps"
        )
    if out["placeless"]:
        lines.append("no node, no geometry: " + ", ".join(out["placeless"]))
    built, now = out["built"], out["now"]
    lines.append(f"built here: {built['where']} · {built['figure']}")
    lines.append(f"built now (stored site): {now['where']} · {now['figure']}")
    if out["basis"]:
        lines.append(out["basis"])
    if built["stage_text"]:
        lines.append(f"stage here: {built['stage_text']}")
    if out["loses"]:
        lines.append("loses progress: " + out["loses"]["text"])
    for fit in out["fits"]:
        lines.append(f"fit pad to “{fit['name']}”: {fit['machines']} machines outside the pad")
    if out["overlaps"]:
        lines.append("overlaps the pad of " + ", ".join(f"“{n}”" for n in out["overlaps"]))
    return lines


def render_site_preview(out: dict) -> str:
    """The preview ``site_preview.preview`` returned, one section a line."""
    lines = [f"where: {out['region'] or 'off any named region'}"]
    if not out["in_map"]:
        return "\n".join([*lines, "outside the map: a site here is refused"])
    if not out["in_content"]:
        lines.append("off the playable ground (allowed; check the spot in game)")
    lines.append(f"height: {out['z_m']:g} m" if out["z_m"] is not None else out["z_note"])
    lines += _terrain_lines(out)
    lines.append("floors: " + ("; ".join(out["slabs"]) or "bare ground"))
    if out["failure"]:
        return "\n".join([*lines, f"plan: {out['failure']}"])
    return "\n".join(lines + _plan_lines(out))

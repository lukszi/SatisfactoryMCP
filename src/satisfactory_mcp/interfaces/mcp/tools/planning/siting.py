"""``site_plan``: record, preview or clear where a stored plan stands."""

from __future__ import annotations

import dataclasses
from typing import Annotated

from mcp.server.fastmcp import Context
from pydantic import Field

from .....domain.planning import siting as siting_mod
from .....domain.planning.siting import preview as site_preview
from .....domain.planning.stored.planlog import PlanLog, Pushed
from .....domain.spatial import heightfield, maplink, regions
from .....presenters.text import primitives as render
from ... import app
from ...params import AsOf, BaseRev
from ._plan_log import _journal_view, _needs_base, _world_plan_log, _write
from ._requests import _find_stored_plan


@app.tool()
def site_plan(
    plan: str,
    at: Annotated[
        str,
        Field(
            description="site origin (the footprint's CENTRE): 'x,y[,z]' in metres, "
            "'me', a factory name, 'slab:<n>' or a run id. Blank keeps the stored origin"
        ),
    ] = "",
    yaw_deg: Annotated[
        float | None,
        Field(description="degrees about world Z, positive +X towards +Y; omit to keep"),
    ] = None,
    footprint: Annotated[
        str,
        Field(
            description="'WxD' in metres ('96' = square). Blank keeps the stored one, "
            "or derives the layout's own square if none is stored"
        ),
    ] = "",
    clear: bool = False,
    preview: Annotated[
        bool, Field(description="show what the pad would meet there; writes nothing")
    ] = False,
    base_rev: BaseRev = None,
    save: str | None = None,
    world: str | None = None,
    as_of: AsOf = None,
    ctx: Context | None = None,
) -> str:
    """Record, update or clear WHERE a stored plan stands. Nothing is re-solved.

    A plan stores what to build; this stores where -- origin (x, y and optionally z, in
    metres, at the footprint's centre), orientation (yaw about world Z, the same
    convention the save stores machine facing with), and footprint (width x depth,
    metres). The footprint defaults to the square plan_layout budgets for the plan's
    largest floor, and the record keeps track of whether it was measured or derived.

    Once sited: plan recalls print the siting; ``diff_vs_save plan=<name>`` adds an
    approximate what-stands-on-the-pad census; ``show_on_map at='plan:<name>'``
    centres a map link on the origin.

    The siting is a RECORD of your decision, not a constraint on the solve -- re-running
    the plan neither reads nor moves it, and ``save_as`` over the same name keeps it.
    """
    g = app.game()
    st = app.load_world(save, world, as_of)
    stored = _find_stored_plan(st, plan)

    def push(value: dict | None, nothing: str) -> tuple[Pushed | None, str]:
        return _write(
            stored.name,
            nothing,
            lambda: _world_plan_log(st).push(
                stored.key,
                base_rev,
                [{"op": "site", "value": value}],
                actor=app.actor(ctx),
                sav=app.save_token(st),
            ),
        )

    if clear:
        if not stored.siting:
            return f"plan {stored.name!r} carries no siting; nothing to clear"
        if base_rev is None:
            return _needs_base(stored.name, stored.rev, "nothing cleared")
        pushed, text = push(None, "nothing cleared")
        if pushed is None:
            return text
        return f"cleared the siting of plan {stored.name!r}. The plan itself is untouched\n{text}"

    existing = siting_mod.parse(stored)
    if preview:
        return _site_preview(g, st, stored, existing, at, yaw_deg, footprint, ctx)
    if not at and existing is None:
        return (
            f"! plan {stored.name!r} has no siting yet, so there is no origin to keep -- "
            "pass at='x,y[,z]' in metres, 'me', a factory name, 'slab:<n>' or a run id"
        )
    if base_rev is None:
        return _needs_base(stored.name, stored.rev, "not sited")

    when = str(st.header.get("save_datetime") or st.header.get("filename") or "")
    try:
        if at:
            sit = _siting_at(g, st, stored, existing, at, yaw_deg, footprint, when=when)
        else:
            width, depth, source = existing.width_m, existing.depth_m, existing.source
            if footprint:
                width, depth = siting_mod.parse_footprint(footprint)
                source = "given"
            sit = siting_mod.Siting(
                x_m=existing.x_m,
                y_m=existing.y_m,
                yaw_deg=yaw_deg if yaw_deg is not None else existing.yaw_deg,
                width_m=width,
                depth_m=depth,
                source=source,
                origin_label=existing.origin_label,
                when=when,
            )
            # The stored z stays; a missing one is read from the terrain under the pad.
            sit = siting_mod.settle_z(st, sit, existing.z_m, "stored", siting_mod.LOAD_FIELD)
    except ValueError as exc:
        return f"! {exc}"

    sit = _snapped(sit)
    if sit.z_source == "terrain":
        # Snapping moved the pad, so its terrain z is read again where it now stands.
        sit = siting_mod.settle_z(
            st, dataclasses.replace(sit, z_m=None), None, "", siting_mod.LOAD_FIELD
        )
    pushed, text = push(sit.to_dict(), "not sited")
    if pushed is None:
        return text
    path = PlanLog.dir_for(st.world_id)
    label = regions.load_regions().label_for(sit.x_m * 100, sit.y_m * 100)
    verb = "re-sited" if existing else "sited"
    return render.envelope(
        f"# {verb} plan {stored.name!r}: {sit.describe()}\n"
        f"# region: {label.describe()}\n"
        + (f"# {sit.terrain_line()}\n" if sit.terrain_line() else "")
        + f"stored in {path}\n{text}",
        "map: "
        + maplink.local_map_url(sit.x_m, sit.y_m, world=st.plans.world_id)
        + "\n"
        + maplink.map_url(sit.x_m * 100, sit.y_m * 100),
        [
            (
                f"diff_vs_save plan={stored.name!r} now reports what stands inside this "
                f"footprint; show_on_map at='plan:{stored.name}' centres on it"
            ),
            (
                "the siting is a record, not a constraint: re-solving the plan neither "
                "reads nor moves it"
            ),
        ],
    )


def _siting_at(g, st, stored, existing, at: str, yaw_deg, footprint: str, **extra):
    """A siting at ``at``, keeping the stored yaw and footprint where the call left them out."""
    keep = existing is not None and existing.has_footprint
    return siting_mod.build_siting(
        g,
        st,
        at=at,
        yaw_deg=yaw_deg if yaw_deg is not None else (existing.yaw_deg if existing else 0.0),
        footprint=footprint or (f"{existing.width_m:g}x{existing.depth_m:g}" if keep else ""),
        plan_kwargs=stored.kwargs(),
        **extra,
    )


def _snapped(sit: siting_mod.Siting) -> siting_mod.Siting:
    """``sit`` on the shared ``site_snap`` lattice, as the page's drag places pads."""
    mode, _note = app.shared_setting("site_snap")
    x, y, yaw = siting_mod.snap(sit.x_m, sit.y_m, sit.yaw_deg, sit.width_m, sit.depth_m, mode)
    return dataclasses.replace(sit, x_m=x, y_m=y, yaw_deg=yaw)


def _site_preview(g, st, stored, existing, at: str, yaw_deg, footprint: str, ctx) -> str:
    """``site_plan(preview=True)``: the page's preview in words, and a ghost pad there."""
    state = _world_plan_log(st).state(stored.key)
    biomass, _unread = app.shared_setting("biomass")
    headroom, _unread = app.shared_setting("stage_headroom")
    sess = site_preview.open_session(g, st, state, biomass=biomass, default=headroom)
    try:
        if at:
            solution = sess.prepared.solution if not sess.failure else None
            sit = _siting_at(g, st, stored, existing, at, yaw_deg, footprint, solution=solution)
        else:
            base = existing or site_preview.start_siting(g, st, sess)
            width, depth = base.width_m, base.depth_m
            if footprint:
                width, depth = siting_mod.parse_footprint(footprint)
            sit = siting_mod.Siting(
                x_m=base.x_m,
                y_m=base.y_m,
                z_m=None,
                yaw_deg=yaw_deg if yaw_deg is not None else base.yaw_deg,
                width_m=width,
                depth_m=depth,
                source=base.source,
            )
    except ValueError as exc:
        return f"! {exc}"
    sit = _snapped(sit)
    try:
        field = heightfield.load_field()
    except MemoryError:
        field = None
    out = site_preview.preview(g, st, sess, sit, terrain=field)
    args = {
        "view": "site",
        "x_m": sit.x_m,
        "y_m": sit.y_m,
        "yaw_deg": sit.yaw_deg,
        "w_m": sit.width_m,
        "d_m": sit.depth_m,
    }
    _journal_view(st, stored.name, "site_plan", ctx, args)
    head = (
        f"# preview of plan {stored.name!r} v{stored.rev} at {sit.x_m:,.0f}, {sit.y_m:,.0f}, "
        f"yaw {sit.yaw_deg:g}°, {sit.width_m:g}×{sit.depth_m:g} m -- nothing written"
    )
    nxt = (
        f"to keep it: site_plan plan={stored.name!r} at='{sit.x_m:g},{sit.y_m:g}' "
        f"base_rev={stored.rev}, or [use it] on the page"
    )
    return "\n".join([head, *site_preview.preview_lines(out), nxt])

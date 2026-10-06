"""Turning tool arguments into a siting: the origin, the footprint and the settled z."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from ....core.gamedata.model import GameData
from ...spatial.places import PLAYER_WORDS, resolve_place
from .ground import LOAD_FIELD, settle_z
from .record import Siting, parse

if TYPE_CHECKING:  # pragma: no cover - import cycle only matters for type checkers
    from ...world.state import WorldState


def parse_footprint(text: str) -> tuple[float, float]:
    """``"96x64"`` -> (96, 64) metres; a single number is a square.

    Raises ``ValueError`` with the fix in the message, because this arrives straight
    from a tool argument.
    """
    cleaned = text.strip().casefold().replace(" ", "").replace("m", "")
    parts = cleaned.split("x")
    try:
        if len(parts) == 1:
            side = float(parts[0])
            w, d = side, side
        elif len(parts) == 2:
            w, d = float(parts[0]), float(parts[1])
        else:
            raise ValueError
    except ValueError:
        raise ValueError(
            f"footprint {text!r} is not 'WxD' in metres (e.g. '96x64', or '96' for a square)"
        ) from None
    if w <= 0 or d <= 0:
        raise ValueError(f"footprint {text!r} must be positive in both directions")
    return w, d


def resolve_site_origin(st: WorldState, at: str) -> tuple[float, float, float | None, str]:
    """Resolve a site origin: any place, plus the 'x,y,z' form only a site can use.

    Returns (x_m, y_m, z_m-or-None, label). Two forms are handled here rather than in the
    shared resolver: the three-part coordinate, because ``resolve_place`` deliberately
    answers with pairs, and 'me', because the player pawn is the one place that DOES carry
    a height worth keeping. Everything else is the one place vocabulary.
    """
    text = at.strip()
    if "," in text:
        parts = text.split(",")
        if len(parts) not in (2, 3):
            raise ValueError(f"{at!r} is not 'x,y' or 'x,y,z' in metres")
        try:
            values = [float(v) for v in parts]
        except ValueError as exc:
            raise ValueError(f"{at!r} is not 'x,y' or 'x,y,z' in metres") from exc
        z = values[2] if len(values) == 3 else None
        return values[0], values[1], z, f"{values[0]:g},{values[1]:g}"
    if text.casefold() in PLAYER_WORDS:
        here = st.player_position()
        if here is None:
            raise ValueError("this save has no player pawn, so 'me' cannot be resolved")
        return here[0] / 100.0, here[1] / 100.0, here[2] / 100.0, "you"
    origin_cm, label = resolve_place(st, text)  # raises ValueError with the known names
    return origin_cm[0] / 100.0, origin_cm[1] / 100.0, None, label


def plan_site_args(st: WorldState, plan: str | None, at: str, footprint: str) -> tuple[str, str]:
    """The site a planning call should MEASURE at: this call's, else the recalled plan's.

    A stored plan that was sited measures its own ground on every recall without being told
    again, which is most of the reason to have stored the siting at all.
    """
    if at:
        return at, footprint
    stored = st.plans.find(plan) if plan else None
    sit = parse(stored) if stored is not None else None
    if sit is None:
        return "", footprint
    if not footprint and sit.has_footprint:
        footprint = f"{sit.width_m:g}x{sit.depth_m:g}"
    return f"{sit.x_m:g},{sit.y_m:g}", footprint


def resolve_plan_site(
    st: WorldState,
    at: str,
    footprint: str = "",
    when: str = "",
    *,
    terrain_field: Any = LOAD_FIELD,
) -> Siting:
    """A site for a plan being BUILT, resolved before there is a solution to size it from.

    ``build_siting`` is the other half of this and derives a blank footprint from the
    layout, which costs a solve; this runs while the scenario is still being assembled, so
    a blank footprint is ``SITE_PAD_M`` and ``source`` says "default" rather than claiming
    the square was measured. Raises ``ValueError`` with a caller-facing message.
    """
    from ...world.water import SITE_PAD_M

    x_m, y_m, z_m, label = resolve_site_origin(st, at)
    if footprint.strip():
        width, depth = parse_footprint(footprint)
        source = "given"
    else:
        width = depth = SITE_PAD_M
        source = "default"
    sit = Siting(
        x_m=round(x_m, 2),
        y_m=round(y_m, 2),
        width_m=width,
        depth_m=depth,
        source=source,
        origin_label=label,
        when=when,
    )
    return settle_z(st, sit, z_m, label, terrain_field)


def build_siting(
    game: GameData,
    st: WorldState,
    *,
    at: str,
    yaw_deg: float = 0.0,
    footprint: str = "",
    solution=None,
    plan_kwargs: dict | None = None,
    when: str = "",
    terrain_field: Any = LOAD_FIELD,
) -> Siting:
    """Turn tool arguments into a Siting, deriving the footprint when none was given.

    A blank ``footprint`` means "the square plan_layout would budget": the plan is solved
    (or the caller's already-solved ``solution`` reused) and ``Layout.site_side_m`` gives
    the side. That square is an ESTIMATE -- the record says so via ``source`` -- but it is
    the same estimate the layout tool already stands behind, not a new one.

    Raises ``ValueError`` with a caller-facing message on anything unresolvable.
    """
    x_m, y_m, z_m, label = resolve_site_origin(st, at)

    if footprint.strip():
        width, depth = parse_footprint(footprint)
        source = "given"
    else:
        sol = solution
        if sol is None:
            from ..solver.prepare import prepare

            prepared = prepare(game, st, dict(plan_kwargs or {}), diagnose=False)
            if prepared.failure is not None:
                raise ValueError(
                    f"cannot derive a footprint: the plan does not solve "
                    f"({prepared.failure.headline}). Pass footprint='WxD' in metres instead"
                )
            sol = prepared.solution
        if not getattr(sol, "processes", None):
            raise ValueError(
                "cannot derive a footprint from an empty plan -- pass footprint='WxD' in metres"
            )
        from ..layout.schematic import build_layout
        from ..solver.carrier import resolve_tiers

        tiers = resolve_tiers(game, st, "", "")
        kwargs = plan_kwargs or {}
        lay = build_layout(
            game,
            sol,
            belt_ipm=kwargs.get("belt_ipm") or tiers.belt_ipm,
            pipe_m3min=kwargs.get("pipe_m3min") or tiers.pipe_m3min,
        )
        side = lay.site_side_m()
        if side <= 0:
            raise ValueError(
                "the layout budgets no floor for this plan (no known machine footprints) "
                "-- pass footprint='WxD' in metres"
            )
        width = depth = side
        source = "layout"

    sit = Siting(
        x_m=round(x_m, 2),
        y_m=round(y_m, 2),
        yaw_deg=float(yaw_deg or 0.0),
        width_m=width,
        depth_m=depth,
        source=source,
        origin_label=label,
        when=when,
    )
    return settle_z(st, sit, z_m, label, terrain_field)

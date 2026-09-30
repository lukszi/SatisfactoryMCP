"""Where a stored plan STANDS: an origin, an orientation and a footprint.

A plan stores the REQUEST and re-solves on recall (see ``store``), which answers what to
build -- and, until now, nothing about where. Every question after the solve ("how big,
which direction, does what stands here match it") was answered outside the tool with hand
geometry, because the plan had no coordinates to anchor those questions to.

So a plan may now RECORD its siting. Record, not constrain: nothing here feeds the LP,
moves a machine, or claims the site is level. The siting is the player's own statement of
where the plan goes, kept beside the player's own statement of what the plan is, and the
tools that already answer spatial questions (``diff_vs_save``, ``show_on_map``) read it.

Conventions, chosen to match what already exists rather than invented:

* **Origin is the footprint's CENTRE**, in metres, on save axes (+X east, +Y south) --
  the same axes every tool here quotes and the web map plots.
* **Yaw is degrees about world Z, positive turning +X towards +Y** -- exactly the
  convention the save stores machine facing with and ``footprintCorners`` in the web
  frontend draws with, so a siting's rectangle and a machine's rectangle rotate the same
  way on the same map.
* **Footprint is width x depth in metres**, width along the site's own X before yaw.
  Either given by the caller, or the square ``plan_layout`` already computes
  (``Layout.site_side_m``: the side that fits the largest floor) -- and the record says
  WHICH, because a layout-computed square is an estimate and a measured pad is not.

Stored as a defaulted dict on ``Plan``, the same discipline ``provenance`` used: an old
plan file without the key still loads, and an empty dict means "not sited", which every
reader treats as the feature simply being absent.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import TYPE_CHECKING

from ...core.gamedata.model import GameData
from ..spatial.origin import PLAYER_WORDS, resolve_origin

if TYPE_CHECKING:  # pragma: no cover - import cycle only matters for type checkers
    from ..world.state import WorldState
    from .store import Plan

__all__ = [
    "SITING_SCHEMA",
    "SiteSurvey",
    "Siting",
    "SurveyRow",
    "build_siting",
    "parse",
    "parse_footprint",
    "plan_site_args",
    "resolve_plan_site",
    "resolve_site_origin",
    "survey",
]

#: Shape of the recorded block, so a later reader can tell this record's vintage apart
#: from a future one rather than guessing from which keys happen to be present.
SITING_SCHEMA = 1


@dataclass(frozen=True)
class Siting:
    """One plan's recorded site. Immutable; editing means writing a new one."""

    x_m: float
    y_m: float
    #: Optional because most ways of naming a spot carry no height: a factory centroid
    #: has none and a bare "x,y" has none. Never guessed -- ``describe`` says "z unset".
    z_m: float | None = None
    yaw_deg: float = 0.0
    width_m: float = 0.0
    depth_m: float = 0.0
    #: "layout" when the footprint is the plan_layout-computed square, "given" when the
    #: caller measured it. The difference is whether the box is an estimate.
    source: str = ""
    #: What the origin argument resolved FROM ("you", a factory name, the raw pair), so
    #: a later reader knows whether the coordinate was typed or derived.
    origin_label: str = ""
    #: Save timestamp when this was recorded, same field ``Plan.created`` uses.
    when: str = ""

    @property
    def has_footprint(self) -> bool:
        return self.width_m > 0 and self.depth_m > 0

    def stored_label(self) -> str:
        if self.origin_label.startswith("pin:") and " = " in self.origin_label:
            return self.origin_label.split(" = ", 1)[1]
        return self.origin_label

    def to_dict(self) -> dict:
        return {
            "schema": SITING_SCHEMA,
            "origin_m": [self.x_m, self.y_m, self.z_m],
            "yaw_deg": self.yaw_deg,
            "footprint_m": [self.width_m, self.depth_m],
            "footprint_source": self.source,
            "origin_label": self.stored_label(),
            "when": self.when,
        }

    def describe(self) -> str:
        z = f",{self.z_m:g}" if self.z_m is not None else ""
        fp = (
            f"{self.width_m:g}x{self.depth_m:g}m ({self.source or 'unrecorded'})"
            if self.has_footprint
            else "none recorded"
        )
        via = f" (from {self.origin_label})" if self.origin_label else ""
        return f"origin {self.x_m:g},{self.y_m:g}{z}m{via}, yaw {self.yaw_deg:g}deg, footprint {fp}"

    def contains_cm(self, x_cm: float, y_cm: float) -> bool:
        """Whether a save coordinate falls inside the sited rectangle.

        The inverse of the rotation the web map's ``footprintCorners`` applies: a local
        offset (dx, dy) lands at (x + dx cos - dy sin, y + dx sin + dy cos), so a world
        offset comes back through the transpose. No axis-aligned shortcut -- an AABB of
        the turned rectangle would count machines standing off the pad's corners.
        """
        if not self.has_footprint:
            return False
        a = math.radians(self.yaw_deg)
        dx = x_cm / 100.0 - self.x_m
        dy = y_cm / 100.0 - self.y_m
        local_x = dx * math.cos(a) + dy * math.sin(a)
        local_y = -dx * math.sin(a) + dy * math.cos(a)
        return abs(local_x) <= self.width_m / 2 + 1e-9 and abs(local_y) <= self.depth_m / 2 + 1e-9


def parse(plan: Plan) -> Siting | None:
    """The siting a stored plan carries, or None -- absence is ordinary, not an error."""
    raw = getattr(plan, "siting", None)
    if not isinstance(raw, dict) or not raw.get("origin_m"):
        return None
    origin = list(raw["origin_m"]) + [None, None, None]
    fp = list(raw.get("footprint_m") or ()) + [0.0, 0.0]
    try:
        return Siting(
            x_m=float(origin[0]),
            y_m=float(origin[1]),
            z_m=None if origin[2] is None else float(origin[2]),
            yaw_deg=float(raw.get("yaw_deg") or 0.0),
            width_m=float(fp[0] or 0.0),
            depth_m=float(fp[1] or 0.0),
            source=str(raw.get("footprint_source") or ""),
            origin_label=str(raw.get("origin_label") or ""),
            when=str(raw.get("when") or ""),
        )
    except (TypeError, ValueError):
        # A hand-edited record that no longer parses reads as "not sited" rather than as
        # a crash inside every planning tool that recalls the plan.
        return None


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
    shared resolver: the three-part coordinate, because ``resolve_origin`` deliberately
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
    origin_cm, label = resolve_origin(st, text)  # raises ValueError with the known names
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


def resolve_plan_site(st: WorldState, at: str, footprint: str = "", when: str = "") -> Siting:
    """A site for a plan being BUILT, resolved before there is a solution to size it from.

    ``build_siting`` is the other half of this and derives a blank footprint from the
    layout, which costs a solve; this runs while the scenario is still being assembled, so
    a blank footprint is ``SITE_PAD_M`` and ``source`` says "default" rather than claiming
    the square was measured. Raises ``ValueError`` with a caller-facing message.
    """
    from ..world.water import SITE_PAD_M

    x_m, y_m, z_m, label = resolve_site_origin(st, at)
    if footprint.strip():
        width, depth = parse_footprint(footprint)
        source = "given"
    else:
        width = depth = SITE_PAD_M
        source = "default"
    return Siting(
        x_m=round(x_m, 2),
        y_m=round(y_m, 2),
        z_m=None if z_m is None else round(z_m, 2),
        width_m=width,
        depth_m=depth,
        source=source,
        origin_label=label,
        when=when,
    )


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
            from .prepare import prepare

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
        from .carrier import resolve_tiers
        from .layout import build_layout

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

    return Siting(
        x_m=round(x_m, 2),
        y_m=round(y_m, 2),
        z_m=None if z_m is None else round(z_m, 2),
        yaw_deg=float(yaw_deg or 0.0),
        width_m=width,
        depth_m=depth,
        source=source,
        origin_label=label,
        when=when,
    )


# ------------------------------------------------------------------- the survey


@dataclass(frozen=True)
class SurveyRow:
    """One building class: how many the plan wants vs how many stand on the site."""

    cls: str
    name: str
    planned: int
    standing: int


@dataclass(frozen=True)
class SiteSurvey:
    """Counts by building class inside the sited footprint, against the plan's bill.

    Deliberately APPROXIMATE, and named so wherever it prints: a machine is counted by
    its class alone -- not its recipe, not its clock, not whether it is wired to anything.
    The identity-matched truth is ``build_diff``'s job; this answers the narrower question
    a siting makes askable at all: "is what stands on THIS pad the right shape".
    """

    rows: list[SurveyRow]
    planned_total: int
    standing_total: int


def survey(game: GameData, st: WorldState, sit: Siting, processes: list[dict]) -> SiteSurvey | None:
    """Count what stands inside the footprint, per building class, against the plan.

    ``None`` when the siting has no footprint: an origin alone marks a spot but bounds
    nothing, and a survey over an unbounded area would just be the whole save again.
    """
    if not sit.has_footprint:
        return None
    planned: dict[str, int] = {}
    for p in processes:
        cls = p.get("building_id") or ""
        if cls:
            planned[cls] = planned.get(cls, 0) + int(p.get("machines") or 0)
    standing: dict[str, int] = {}
    # The same records factory_map and describe_location read: machines, extractors and
    # generators, each with its save position. Belts and foundations are not in the
    # projection's census and are deliberately out of scope here.
    for record in st._all_records():
        pos = record.get("pos")
        if pos and sit.contains_cm(pos[0], pos[1]):
            cls = record.get("cls") or ""
            standing[cls] = standing.get(cls, 0) + 1
    classes = sorted(
        set(planned) | set(standing),
        key=lambda c: (-planned.get(c, 0), -standing.get(c, 0), c),
    )
    rows = [
        SurveyRow(
            cls=c,
            name=game.buildings[c].name if c in game.buildings else c,
            planned=planned.get(c, 0),
            standing=standing.get(c, 0),
        )
        for c in classes
    ]
    return SiteSurvey(
        rows=rows,
        planned_total=sum(planned.values()),
        standing_total=sum(standing.values()),
    )

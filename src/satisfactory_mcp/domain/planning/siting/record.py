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
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol, TypeGuard

from ....core.jsontypes import JsonObject
from ...spatial import geo
from ...spatial.heightfield import cave_masks
from .views import SiteValue, TerrainZ

if TYPE_CHECKING:  # pragma: no cover - import cycle only matters for type checkers
    from ..stored.store import StoredPlan

#: Shape of the recorded block, so a later reader can tell this record's vintage apart
#: from a future one rather than guessing from which keys happen to be present.
SITING_SCHEMA = 1

FOOTPRINT_MIN_M = 8.0

FOOTPRINT_MAX_M = 2000.0

SOURCES = ("given", "layout", "default", "")

LABEL_MAX = 80

WHEN_MAX = 40

#: Fit to built: the margin added round a candidate's machines, metres.
FIT_MARGIN_M = 16.0


class GroundZ(Protocol):
    """Ground height under a pad, metres, or None when unknown (docs/planner-p5_contract.md §6)."""

    def __call__(
        self, x_m: float, y_m: float, yaw_deg: float, width_m: float, depth_m: float
    ) -> float | None: ...


ground_provider: list[GroundZ | None] = [None]


def set_ground_z(provider: GroundZ | None) -> None:
    """Install the terrain height lookup every siting write and preview uses."""
    ground_provider[0] = provider


def ground_z(
    x_m: float, y_m: float, yaw_deg: float, width_m: float, depth_m: float
) -> float | None:
    provider = ground_provider[0]
    if provider is None:
        return None
    try:
        z = provider(x_m, y_m, yaw_deg, width_m, depth_m)
    except Exception:
        return None
    return round(float(z), 2) if z is not None and math.isfinite(z) else None


def _finite(name: str, raw: object) -> float:
    if isinstance(raw, bool) or not isinstance(raw, int | float) or not math.isfinite(raw):
        raise ValueError(f"site {name} must be a finite number, not {raw!r}")
    return float(raw)


def _float(raw: object) -> float:
    """``float(raw)`` for what a stored record may hold; ``TypeError`` for anything else."""
    if not isinstance(raw, int | float | str):
        raise TypeError(f"not a number: {raw!r}")
    return float(raw)


def footprint_box_cm(
    x_m: float, y_m: float, width_m: float, depth_m: float, yaw_deg: float
) -> tuple[float, float, float, float]:
    """The axis-aligned box round a turned footprint, in save centimetres (x0, y0, x1, y1)."""
    a = math.radians(yaw_deg)
    ex = abs(width_m / 2 * math.cos(a)) + abs(depth_m / 2 * math.sin(a))
    ey = abs(width_m / 2 * math.sin(a)) + abs(depth_m / 2 * math.cos(a))
    return (x_m - ex) * 100, (y_m - ey) * 100, (x_m + ex) * 100, (y_m + ey) * 100


def _is_record(raw: object) -> TypeGuard[Mapping[str, object]]:
    return isinstance(raw, dict)


def _is_list(raw: object) -> TypeGuard[Sequence[object]]:
    return isinstance(raw, list | tuple)


def _listed(raw: object) -> Sequence[object]:
    """``raw`` when it is a list or tuple, else empty."""
    return raw if _is_list(raw) else ()


def normalise_record(value: object) -> SiteValue:
    """``value`` as a ``site`` op stores it; ``ValueError`` in words when it cannot be.

    The map square is the one hard edge; a missing height is filled from ``ground_z``.
    """
    if not _is_record(value):
        raise ValueError(f"site takes a siting object or null, not {value!r}")
    schema = value.get("schema", SITING_SCHEMA)
    if isinstance(schema, bool) or not isinstance(schema, int) or schema < 1:
        raise ValueError(f"site schema must be a positive whole number, not {schema!r}")
    if schema > SITING_SCHEMA:
        raise ValueError(
            f"this siting was written by a newer version (schema {schema}; this one reads "
            f"up to {SITING_SCHEMA})"
        )
    origin = _listed(value.get("origin_m"))
    if len(origin) not in (2, 3):
        raise ValueError("site origin_m must be [x, y] or [x, y, z] in metres")
    x, y = _finite("x", origin[0]), _finite("y", origin[1])
    z = origin[2] if len(origin) == 3 else None
    z = None if z is None else _finite("z", z)
    x0, y0, x1, y1 = geo.MAP_SQUARE_M
    if not x0 <= x <= x1:
        raise ValueError(f"the site is outside the map (x must be {x0:,.0f}…{x1:,.0f} m)")
    if not y0 <= y <= y1:
        raise ValueError(f"the site is outside the map (y must be {y0:,.0f}…{y1:,.0f} m)")
    yaw = round(_finite("yaw_deg", value.get("yaw_deg", 0.0)) % 360.0, 6) % 360.0
    fp = _listed(value.get("footprint_m") or [0.0, 0.0])
    if len(fp) != 2:
        raise ValueError("site footprint_m must be [width, depth] in metres")
    w, d = _finite("width", fp[0]), _finite("depth", fp[1])
    if (w, d) != (0.0, 0.0) and not all(FOOTPRINT_MIN_M <= v <= FOOTPRINT_MAX_M for v in (w, d)):
        raise ValueError(
            f"site footprint must be {FOOTPRINT_MIN_M:g}…{FOOTPRINT_MAX_M:,.0f} m each way, "
            f"not {w:g} × {d:g}"
        )
    source = value.get("footprint_source", "")
    if not isinstance(source, str) or source not in SOURCES:
        raise ValueError(f"site footprint_source is one of given, layout, default, not {source!r}")
    label, when = value.get("origin_label", ""), value.get("when", "")
    if not isinstance(label, str) or len(label) > LABEL_MAX:
        raise ValueError(f"site origin_label is text of at most {LABEL_MAX} characters")
    if not isinstance(when, str) or len(when) > WHEN_MAX:
        raise ValueError(f"site when is text of at most {WHEN_MAX} characters")
    if z is None:
        z = ground_z(x, y, yaw, w, d)
    return {
        "schema": SITING_SCHEMA,
        "origin_m": [x, y, z],
        "yaw_deg": yaw,
        "footprint_m": [w, d],
        "footprint_source": source,
        "origin_label": label,
        "when": when,
    }


GRID_M = 8.0

YAW_STEP_DEG = {"fine": 15.0, "grid8": 90.0}


def yaw_step(mode: str) -> float:
    """Degrees a snapped turn moves by under ``site_snap`` ``mode``."""
    return YAW_STEP_DEG.get(mode, YAW_STEP_DEG["fine"])


def snap(
    x_m: float, y_m: float, yaw_deg: float, width_m: float, depth_m: float, mode: str = "fine"
) -> tuple[float, float, float]:
    """The ``site_snap`` rule the page's drag also applies (``pad-drag.ts`` ``snap``).

    ``fine`` rounds the centre to 1 m and yaw to 15°; ``grid8`` turns yaw to 90° and puts
    the pad's west and north edges on the 8 m world grid (a quarter turn swaps W and D).
    """
    step = yaw_step(mode)
    yaw = (round(yaw_deg / step) * step) % 360.0
    if mode == "grid8":
        across, along = (depth_m, width_m) if yaw % 180.0 == 90.0 else (width_m, depth_m)
        x = round((x_m - across / 2) / GRID_M) * GRID_M + across / 2
        y = round((y_m - along / 2) / GRID_M) * GRID_M + along / 2
    else:
        x, y = float(round(x_m)), float(round(y_m))
    return x, y, yaw


def fit_to_bbox(bbox_m: list[float], name: str, when: str = "") -> SiteValue:
    """The siting that covers a candidate's machines: its box plus ``FIT_MARGIN_M``, unturned."""
    x0, y0, x1, y1 = (float(v) for v in bbox_m)
    w = max(FOOTPRINT_MIN_M, float(math.ceil(x1 - x0 + FIT_MARGIN_M)))
    d = max(FOOTPRINT_MIN_M, float(math.ceil(y1 - y0 + FIT_MARGIN_M)))
    return normalise_record(
        {
            "origin_m": [round((x0 + x1) / 2, 2), round((y0 + y1) / 2, 2), None],
            "yaw_deg": 0.0,
            "footprint_m": [min(w, FOOTPRINT_MAX_M), min(d, FOOTPRINT_MAX_M)],
            "footprint_source": "given",
            "origin_label": f"built “{name}”"[:LABEL_MAX],
            "when": when[:WHEN_MAX],
        }
    )


def _turn(a: float, b: float) -> float:
    return (b - a + 180.0) % 360.0 - 180.0


def move_words(was: object, now: object) -> str:
    """``moved 1,503 m west, turned 30°`` and the like; "" when nothing readable moved."""
    old = Siting.from_record(was) if was else None
    new = Siting.from_record(now) if now else None
    if old is None or new is None:
        return ""
    parts: list[str] = []
    dist = math.hypot(new.x_m - old.x_m, new.y_m - old.y_m)
    if dist >= 0.5:
        way = geo.direction_of(new.x_m, new.y_m, old.x_m, old.y_m).replace("th", "th-", 1)
        parts.append(f"moved {dist:,.0f} m {way.rstrip('-')}")
    turn = _turn(old.yaw_deg, new.yaw_deg)
    if abs(turn) >= 0.5:
        parts.append(f"turned {abs(turn):.0f}°")
    if (round(old.width_m), round(old.depth_m)) != (round(new.width_m), round(new.depth_m)):
        parts.append(f"resized to {new.width_m:.0f}×{new.depth_m:.0f} m")
    return ", ".join(parts)


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
    #: Where ``z_m`` came from: "given" (typed x,y,z), "you" (the player pawn), "stored"
    #: (kept from the record), "terrain" (the heightfield under the footprint), or "". Not
    #: stored: ``normalise_record`` owns the record, and this and ``terrain`` live for one reply.
    z_source: str = ""
    #: The heightfield's reading of the site, beside whatever z won; see ``terrain_z``.
    terrain: TerrainZ | None = None

    @classmethod
    def from_record(cls, raw: object) -> Siting | None:
        """The siting a stored record describes, or None -- absence is ordinary, not an error."""
        if not _is_record(raw):
            return None
        origin_m = raw.get("origin_m")
        if not origin_m:
            return None
        origin = [*_listed(origin_m), None, None, None]
        fp = [*_listed(raw.get("footprint_m") or ()), 0.0, 0.0]
        try:
            return cls(
                x_m=_float(origin[0]),
                y_m=_float(origin[1]),
                z_m=None if origin[2] is None else _float(origin[2]),
                yaw_deg=_float(raw.get("yaw_deg") or 0.0),
                width_m=_float(fp[0] or 0.0),
                depth_m=_float(fp[1] or 0.0),
                source=str(raw.get("footprint_source") or ""),
                origin_label=str(raw.get("origin_label") or ""),
                when=str(raw.get("when") or ""),
            )
        except (TypeError, ValueError):
            # A hand-edited record that no longer parses reads as "not sited" rather than as
            # a crash inside every planning tool that recalls the plan.
            return None

    @property
    def has_footprint(self) -> bool:
        return self.width_m > 0 and self.depth_m > 0

    def bbox_cm(self) -> tuple[float, float, float, float]:
        """The axis-aligned box round the turned pad, in save centimetres."""
        return footprint_box_cm(self.x_m, self.y_m, self.width_m, self.depth_m, self.yaw_deg)

    def corners_m(self) -> list[tuple[float, float]]:
        """The pad's four corners in metres, turned by its yaw."""
        a = math.radians(self.yaw_deg)
        c, s = math.cos(a), math.sin(a)
        hw, hd = self.width_m / 2, self.depth_m / 2
        return [
            (self.x_m + dx * c - dy * s, self.y_m + dx * s + dy * c)
            for dx, dy in ((-hw, -hd), (hw, -hd), (hw, hd), (-hw, hd))
        ]

    def overlaps(self, other: Siting) -> bool:
        """Whether the two turned pads meet, by separating axes."""
        mine, theirs = self.corners_m(), other.corners_m()
        for poly in (mine, theirs):
            for i in range(4):
                x1, y1 = poly[i]
                x2, y2 = poly[(i + 1) % 4]
                nx, ny = y2 - y1, x1 - x2
                along_mine = [nx * x + ny * y for x, y in mine]
                along_theirs = [nx * x + ny * y for x, y in theirs]
                if max(along_mine) < min(along_theirs) or max(along_theirs) < min(along_mine):
                    return False
        return True

    def stored_label(self) -> str:
        if self.origin_label.startswith("pin:") and " = " in self.origin_label:
            return self.origin_label.split(" = ", 1)[1]
        return self.origin_label

    def to_dict(self) -> JsonObject:
        """The record ``normalise_record`` would store, as JSON."""
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
        tag = ""
        if self.z_source == "terrain" and self.terrain:
            tag = f" (terrain z, {self.terrain.get('surface', 'ground')})"
        fp = (
            f"{self.width_m:g}x{self.depth_m:g}m ({self.source or 'unrecorded'})"
            if self.has_footprint
            else "none recorded"
        )
        via = f" (from {self.origin_label})" if self.origin_label else ""
        return f"origin {self.x_m:g},{self.y_m:g}{z}m{tag}{via}, yaw {self.yaw_deg:g}deg, footprint {fp}"

    def terrain_line(self) -> str | None:
        """The heightfield's reading of the site as one line, or ``None`` if none was taken."""
        t = self.terrain
        if not t:
            return None
        z = t.get("z_m")
        if z is None:
            return f"terrain z: none -- {t.get('reason') or 'no data'}"
        cave_pct = t.get("cave_pct")
        acc = t.get("accuracy_m")
        parts = [
            f"terrain z {z:g}m ({t.get('surface', 'ground')}, {t.get('provenance')}"
            + (f", +-{acc:g}m" if isinstance(acc, (int, float)) else "")
            + ")"
        ]
        z_min = t.get("z_min_m")
        if z_min is not None:
            parts.append(f"pad {z_min:g}..{t.get('z_max_m'):g}m")
        if t.get("ambiguous"):
            bare = t.get("bare_m")
            parts.append(
                "may be a rock top"
                + (f" (bare ground {bare:g}m)" if isinstance(bare, (int, float)) else "")
            )
        if t.get("cave_floor"):
            parts.append("in a cave: z is the rock collision just under the hint")
        elif t.get("cave") == cave_masks.BELOW:
            parts.append(cave_masks.note(cave_masks.BELOW, None) or "")
        elif cave_pct:
            parts.append(f"a cave lies under {cave_pct:g}% of the pad: z is the surface above it")
        if t.get("coarse"):
            parts.append("coarse fill layer, metre-level")
        water = t.get("water_level_m")
        if water is not None:
            parts.append(f"water surface {water:g}m")
        hint_from = t.get("hint_from")
        if hint_from:
            parts.append(f"surface picked by the {hint_from} z {t.get('hint_m'):g}m")
        if self.z_source and self.z_source != "terrain":
            parts.append(f"z kept from {self.z_source}")
        return "; ".join(parts)

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


def parse(plan: StoredPlan) -> Siting | None:
    """The siting a stored plan carries, or None -- absence is ordinary, not an error."""
    return Siting.from_record(getattr(plan, "siting", None))

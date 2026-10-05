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
import statistics
from dataclasses import dataclass, replace
from typing import TYPE_CHECKING, Any, Protocol

from ...core.gamedata.model import GameData
from ..spatial import geo, heightfield
from ..spatial.origin import PLAYER_WORDS, resolve_origin

if TYPE_CHECKING:  # pragma: no cover - import cycle only matters for type checkers
    from ..world.state import WorldState
    from .store import Plan

__all__ = [
    "FOOTPRINT_MAX_M",
    "FOOTPRINT_MIN_M",
    "SITING_SCHEMA",
    "GroundZ",
    "SiteSurvey",
    "Siting",
    "SurveyRow",
    "TerrainGround",
    "build_siting",
    "check",
    "fit_to_bbox",
    "ground_z",
    "move_words",
    "parse",
    "parse_footprint",
    "plan_site_args",
    "resolve_plan_site",
    "resolve_site_origin",
    "set_ground_z",
    "settle_z",
    "snap",
    "survey",
    "terrain_provider",
    "terrain_z",
]

#: ``terrain_field`` left at this means "the installed provider's field"; ``None``, "none".
LOAD_FIELD = object()

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


_ground: list[GroundZ | None] = [None]


def set_ground_z(provider: GroundZ | None) -> None:
    """Install the terrain height lookup every siting write and preview uses."""
    _ground[0] = provider


def ground_z(
    x_m: float, y_m: float, yaw_deg: float, width_m: float, depth_m: float
) -> float | None:
    provider = _ground[0]
    if provider is None:
        return None
    try:
        z = provider(x_m, y_m, yaw_deg, width_m, depth_m)
    except Exception:
        return None
    return round(float(z), 2) if z is not None and math.isfinite(z) else None


def _finite(name: str, raw) -> float:
    if isinstance(raw, bool) or not isinstance(raw, int | float) or not math.isfinite(raw):
        raise ValueError(f"site {name} must be a finite number, not {raw!r}")
    return float(raw)


def check(value) -> dict:
    """``value`` as a ``site`` op stores it; ``ValueError`` in words when it cannot be.

    The map square is the one hard edge; a missing height is filled from ``ground_z``.
    """
    if not isinstance(value, dict):
        raise ValueError(f"site takes a siting object or null, not {value!r}")  # noqa: TRY004
    schema = value.get("schema", SITING_SCHEMA)
    if isinstance(schema, bool) or not isinstance(schema, int) or schema < 1:
        raise ValueError(f"site schema must be a positive whole number, not {schema!r}")
    if schema > SITING_SCHEMA:
        raise ValueError(
            f"this siting was written by a newer version (schema {schema}; this one reads "
            f"up to {SITING_SCHEMA})"
        )
    origin = value.get("origin_m")
    if not isinstance(origin, list | tuple) or len(origin) not in (2, 3):
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
    fp = value.get("footprint_m") or [0.0, 0.0]
    if not isinstance(fp, list | tuple) or len(fp) != 2:
        raise ValueError("site footprint_m must be [width, depth] in metres")
    w, d = _finite("width", fp[0]), _finite("depth", fp[1])
    if (w, d) != (0.0, 0.0) and not all(FOOTPRINT_MIN_M <= v <= FOOTPRINT_MAX_M for v in (w, d)):
        raise ValueError(
            f"site footprint must be {FOOTPRINT_MIN_M:g}…{FOOTPRINT_MAX_M:,.0f} m each way, "
            f"not {w:g} × {d:g}"
        )
    source = value.get("footprint_source", "")
    if source not in SOURCES:
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


SNAP_MODES = ("fine", "grid8")
GRID_M = 8.0
YAW_STEP_DEG = {"fine": 15.0, "grid8": 90.0}


def yaw_step(mode: str) -> float:
    """Degrees a snapped turn moves by under ``site_snap`` ``mode``."""
    return YAW_STEP_DEG.get(mode, YAW_STEP_DEG["fine"])


def snap(
    x_m: float, y_m: float, yaw_deg: float, width_m: float, depth_m: float, mode: str = "fine"
) -> tuple[float, float, float]:
    """The ``site_snap`` rule the page's drag also applies (``sitedrag.ts`` ``snap``).

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


def fit_to_bbox(bbox_m: list[float], name: str, when: str = "") -> dict:
    """The siting that covers a candidate's machines: its box plus ``FIT_MARGIN_M``, unturned."""
    x0, y0, x1, y1 = (float(v) for v in bbox_m)
    w = max(FOOTPRINT_MIN_M, float(math.ceil(x1 - x0 + FIT_MARGIN_M)))
    d = max(FOOTPRINT_MIN_M, float(math.ceil(y1 - y0 + FIT_MARGIN_M)))
    return check(
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


def move_words(was: dict | None, now: dict | None) -> str:
    """``moved 1,503 m west, turned 30°`` and the like; "" when nothing readable moved."""
    old = parse(_Raw(was)) if was else None
    new = parse(_Raw(now)) if now else None
    if old is None or new is None:
        return ""
    parts = []
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
class _Raw:
    siting: dict | None


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
    #: (kept from the record), "terrain" (the heightfield under the footprint), or "". Not stored: the
    #: canonical record is ``check``'s, and this and ``terrain`` live for one reply.
    z_source: str = ""
    #: The heightfield's reading of the site, beside whatever z won; see ``terrain_z``.
    terrain: dict | None = None

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
        if t.get("z_m") is None:
            return f"terrain z: none -- {t.get('reason') or 'no data'}"
        acc = t.get("accuracy_m")
        parts = [
            f"terrain z {t['z_m']:g}m ({t.get('surface', 'ground')}, {t.get('provenance')}"
            + (f", +-{acc:g}m" if isinstance(acc, (int, float)) else "")
            + ")"
        ]
        if t.get("z_min_m") is not None:
            parts.append(f"pad {t['z_min_m']:g}..{t['z_max_m']:g}m")
        if t.get("ambiguous"):
            bare = t.get("bare_m")
            parts.append(
                "may be a rock top"
                + (f" (bare ground {bare:g}m)" if isinstance(bare, (int, float)) else "")
            )
        if t.get("coarse"):
            parts.append("coarse fill layer, metre-level")
        if t.get("water_level_m") is not None:
            parts.append(f"water surface {t['water_level_m']:g}m")
        if t.get("hint_from"):
            parts.append(f"surface picked by the {t['hint_from']} z {t['hint_m']:g}m")
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


#: A pad is flagged ambiguous when at least this share of it may be a roof over the floor.
AMBIGUOUS_PAD_PCT = 10.0

NO_FIELD = "no terrain field on this machine -- run tools/gen_world_heightmap.py"


def _footprint_box_cm(
    x_m: float, y_m: float, width_m: float, depth_m: float, yaw_deg: float
) -> tuple[float, float, float, float]:
    a = math.radians(yaw_deg)
    ex = abs(width_m / 2 * math.cos(a)) + abs(depth_m / 2 * math.sin(a))
    ey = abs(width_m / 2 * math.sin(a)) + abs(depth_m / 2 * math.cos(a))
    return (x_m - ex) * 100, (y_m - ey) * 100, (x_m + ex) * 100, (y_m + ey) * 100


def _built_hint_m(st: WorldState | None, probe: Siting) -> float | None:
    """Median z of what already stands on the footprint, in metres."""
    if st is None or not probe.has_footprint:
        return None
    zs = [
        record["pos"][2] / 100.0
        for record in st._all_records()
        if record.get("pos")
        and len(record["pos"]) > 2
        and probe.contains_cm(record["pos"][0], record["pos"][1])
    ]
    return statistics.median(zs) if zs else None


def terrain_z(
    field: heightfield.Field | None,
    x_m: float,
    y_m: float,
    *,
    width_m: float = 0.0,
    depth_m: float = 0.0,
    yaw_deg: float = 0.0,
    hint_m: float | None = None,
    hint_from: str = "",
) -> dict[str, Any]:
    """The ground under a site from the heightfield, with what that number is worth.

    Over a footprint the answer is the pad's median on one surface -- ground unless a hint
    picks another -- with its min and max for foundation planning; without one, a bilinear
    point read. ``z_m`` is ``None`` with a ``reason`` where the field has no data; nothing
    here falls back to interpolating save objects.
    """
    if field is None:
        return {"z_m": None, "reason": NO_FIELD}
    out: dict[str, Any] = {
        "hint_m": None if hint_m is None else round(hint_m, 2),
        "hint_from": hint_from or None,
        "build": field.build,
    }
    if width_m <= 0 or depth_m <= 0:
        reading = field.z(x_m * 100, y_m * 100, hint_z_cm=None if hint_m is None else hint_m * 100)
        if reading is None:
            return {**out, "z_m": None, "reason": _silence(field, x_m, y_m)}
        return {
            **out,
            "z_m": round(reading.z_m, 2),
            "surface": reading.surface,
            "bare_m": None if reading.terrain_z_m is None else round(reading.terrain_z_m, 2),
            "ambiguous": reading.ambiguous,
            "provenance": reading.source,
            "accuracy_m": reading.accuracy_m,
            "coarse": reading.provenance == heightfield.PROV_FILL,
            "water_level_m": reading.water_m if reading.submerged else None,
        }
    box = _footprint_box_cm(x_m, y_m, width_m, depth_m, yaw_deg)
    areas = {"ground": field.window(*box)}
    if field.has_terrain:
        areas["terrain"] = field.window(*box, surface="terrain", shape=False)
    if field.has_top:
        areas["top"] = field.window(*box, surface="top", shape=False)
    ground = areas["ground"]
    if ground.z_median_m is None:
        return {**out, "z_m": None, "reason": _silence(field, x_m, y_m)}
    surface = "ground"
    if hint_m is not None:
        medians = {name: area.z_median_m for name, area in areas.items()}
        picked = heightfield.Surfaces(
            ground_m=medians["ground"],
            terrain_m=medians.get("terrain"),
            top_m=medians.get("top"),
            provenance=heightfield.PROV_NODATA,
        ).pick(hint_m)
        surface = picked[0] if picked else "ground"
    area = areas[surface]
    dominant = max(ground.provenance_pct.items(), key=lambda kv: kv[1])[0]
    terrain_area = areas.get("terrain")
    return {
        **out,
        "z_m": area.z_median_m,
        "surface": surface,
        "z_min_m": area.z_min_m,
        "z_max_m": area.z_max_m,
        "bare_m": None if terrain_area is None else terrain_area.z_median_m,
        "ambiguous": ground.ambiguous_pct >= AMBIGUOUS_PAD_PCT,
        "ambiguous_pct": ground.ambiguous_pct,
        "provenance": heightfield.PROV_NAMES.get(dominant, f"layer {dominant}"),
        "accuracy_m": field.accuracy_m(dominant),
        "coarse": ground.coarse_pct > 0,
        "coarse_pct": ground.coarse_pct,
        "nodata_pct": ground.nodata_pct,
        "submerged_pct": ground.submerged_pct,
        "water_level_m": ground.water_level_m,
        "slope_mean_deg": ground.slope_mean_deg,
        "roughness_m": ground.roughness_m,
    }


def _silence(field: heightfield.Field, x_m: float, y_m: float) -> str:
    if field.texel(x_m * 100, y_m * 100) is None:
        return "outside the map"
    return "the terrain field has no data here -- open ocean, or a cave mouth"


def settle_z(
    st: WorldState | None,
    sit: Siting,
    at_z_m: float | None,
    label: str,
    terrain_field: Any,
) -> Siting:
    """``sit`` with its z settled: a typed or player z wins, else the terrain's."""
    field = _installed_field() if terrain_field is LOAD_FIELD else terrain_field
    if at_z_m is not None:
        z_source = label if label in ("you", "stored") else "given"
        hint_m, hint_from = at_z_m, z_source
    else:
        z_source = ""
        hint_m = _built_hint_m(st, sit)
        hint_from = "built" if hint_m is not None else ""
    reading = terrain_z(
        field,
        sit.x_m,
        sit.y_m,
        width_m=sit.width_m,
        depth_m=sit.depth_m,
        yaw_deg=sit.yaw_deg,
        hint_m=hint_m,
        hint_from=hint_from,
    )
    z_m = at_z_m
    if z_m is None and reading.get("z_m") is not None:
        z_m, z_source = float(reading["z_m"]), "terrain"
    return replace(
        sit,
        z_m=None if z_m is None else round(z_m, 2),
        z_source=z_source,
        terrain=reading,
    )


class TerrainGround:
    """The ``GroundZ`` the servers install: the pad's median on ``ground``, from ``load()``.

    ``load`` is also what ``settle_z`` reads its fuller answer from, so a chat write and a
    dragged pad go through one installed source.
    """

    def __init__(self, load) -> None:
        self.load = load

    def __call__(
        self, x_m: float, y_m: float, yaw_deg: float, width_m: float, depth_m: float
    ) -> float | None:
        reading = terrain_z(
            self.load(), x_m, y_m, width_m=width_m, depth_m=depth_m, yaw_deg=yaw_deg
        )
        return reading.get("z_m")


def terrain_provider(load=heightfield.load_field) -> TerrainGround:
    return TerrainGround(load)


def _installed_field():
    """The field behind the installed provider, or ``None`` with no terrain provider."""
    load = getattr(_ground[0], "load", None)
    return load() if load is not None else None


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
    from ..world.water import SITE_PAD_M

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

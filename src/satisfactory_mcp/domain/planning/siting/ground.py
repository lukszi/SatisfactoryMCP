"""The ground under a site: the heightfield's reading of a pad and the z a siting keeps."""

from __future__ import annotations

import math
import statistics
from dataclasses import replace
from typing import TYPE_CHECKING, Any

from ...spatial import caves, heightfield
from .record import Siting, _ground

if TYPE_CHECKING:  # pragma: no cover - import cycle only matters for type checkers
    from ...world.state import WorldState


#: ``terrain_field`` left at this means "the installed provider's field"; ``None``, "none".
LOAD_FIELD = object()

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
        if not reading.height_known:
            return {
                **out,
                "z_m": None,
                "cave": caves.INSIDE,
                "reason": reading.cave_note,
            }
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
            "cave": reading.cave,
            "cave_floor": reading.cave_floor,
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
    if hint_m is not None and _pad_in_cave(field, x_m, y_m, hint_m, ground.cave_pct, areas):
        surface_m = min(a.z_median_m for a in areas.values() if a.z_median_m is not None)
        return {
            **out,
            "z_m": None,
            "cave": caves.INSIDE,
            "reason": caves.note(caves.INSIDE, surface_m),
        }
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
        "cave_pct": ground.cave_pct,
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


def _pad_in_cave(
    field: heightfield.Field,
    x_m: float,
    y_m: float,
    hint_m: float,
    cave_pct: float,
    areas: dict[str, heightfield.Area],
) -> bool:
    """Whether the hint puts the pad inside a cave: in a sound volume at its centre, or
    deeper than ``caves.INSIDE_DEPTH_M`` under every surface median over flagged ground."""
    lowest = min((a.z_median_m for a in areas.values() if a.z_median_m is not None), default=None)
    if field.cave_at(x_m * 100, y_m * 100, hint_m * 100, lowest) == caves.INSIDE:
        return True
    return bool(cave_pct) and lowest is not None and hint_m < lowest - caves.INSIDE_DEPTH_M


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

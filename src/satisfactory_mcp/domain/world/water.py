"""Which bodies of water are already being drawn from, where sea level is, and what the
terrain says about the water at one spot.

Two sources, joined here rather than by each caller: the save knows which volumes are
pumped and how high the pumps stand, the terrain field knows where water lies on the
ground. Neither knows how many extractors a body holds -- that is level geometry -- so
nothing in this module ever returns a count.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from ...core.saveio.records import instance_leaf
from ..spatial import heightfield as hf

__all__ = [
    "SITE_PAD_M",
    "WATER_SEARCH_M",
    "SiteWater",
    "site_water",
    "water_volumes",
]

#: The square measured around a site that states no footprint of its own, in metres. Big
#: enough to hold the pad a large plan asks for, small enough to read at full 1 m stride.
SITE_PAD_M = 200.0

#: How far out from the pad to look for water when none stands on it, in metres. A
#: half-width, not a circle: the search is over the field's own rows and columns.
WATER_SEARCH_M = 500.0

#: How far a measured surface may sit from the pump-measured sea level and still be called
#: sea. The save's own pumps span 0.24 m; a metre is that plus the field's 1 m sampling.
SEA_TOLERANCE_M = 1.0


def water_volumes(projection: dict) -> dict:
    """Water Extractors grouped by the body of water they draw from, plus sea level.

    A pump's ``node`` names its ``FGWaterVolume``: the volume's shape is not in the save,
    its identity is, and the pumps' own heights measure sea level (save-projection.md §6.10).
    """
    groups: dict[str, list[dict]] = {}
    heights_m: list[float] = []
    for extractor in projection.get("extractors", ()):
        if extractor["cls"] != "Build_WaterPump_C":
            continue
        groups.setdefault(extractor.get("node") or "(unresolved)", []).append(extractor)
        if extractor.get("pos"):
            heights_m.append(extractor["pos"][2] / 100.0)
    return {
        "volumes": {
            instance_leaf(volume): len(pumps)
            for volume, pumps in sorted(groups.items(), key=lambda kv: -len(kv[1]))
        },
        "pumps": sum(len(v) for v in groups.values()),
        "sea_level_m": (sum(heights_m) / len(heights_m)) if heights_m else None,
        "sea_level_span_m": (max(heights_m) - min(heights_m)) if heights_m else None,
    }


@dataclass(frozen=True)
class SiteWater:
    """What the terrain measures about the water at one site. Never a placement claim.

    ``pad`` is the rectangle the plan would stand on and ``near`` is the search outwards,
    run only when nothing stands on the pad itself. A count of extractors is deliberately
    absent and must not be derived from ``pad.submerged_pct``: how many pumps a body holds
    depends on shoreline geometry, clearance and overlap, none of which are in this field.
    """

    x_m: float
    y_m: float
    pad: hf.Area
    #: ``None`` when water stands on the pad already, and when the field carries no water
    #: plane at all -- ``pad.submerged_pct`` distinguishes those two.
    near: hf.NearWater | None
    #: Sea level as this save's own pumps measure it, which is an independent instrument
    #: from the field and so is worth quoting beside it.
    sea_level_m: float | None
    build: str | None

    @property
    def level_m(self) -> float | None:
        """The water surface this site would draw from, wherever it was found."""
        if self.pad.water_level_m is not None:
            return self.pad.water_level_m
        return self.near.level_m if self.near else None

    @property
    def distance_m(self) -> float | None:
        """Metres to that surface: zero when it is on the pad, ``None`` when none was found."""
        if self.pad.water_level_m is not None:
            return 0.0
        return self.near.distance_m if self.near else None

    @property
    def below_ground_m(self) -> float | None:
        """How far the water stands below the pad's dry ground, positive meaning downhill.

        ``None`` on a pad with no dry ground left to measure against. The pad MEDIAN is not
        a fallback there: on an all-submerged pad the median is the sea bed, and it comes
        out claiming the water is tens of metres above the ground.
        """
        if self.pad.water_level_m is not None:
            return self.pad.water_below_ground_m
        if self.level_m is None or self.pad.z_median_m is None:
            return None
        return round(self.pad.z_median_m - self.level_m, 1)

    @property
    def at_sea_level(self) -> bool:
        """Whether the water found is the ocean the save's existing pumps already sit in."""
        if self.level_m is None or self.sea_level_m is None:
            return False
        return abs(self.level_m - self.sea_level_m) <= SEA_TOLERANCE_M


def site_water(
    projection: dict,
    x_m: float,
    y_m: float,
    width_m: float = SITE_PAD_M,
    depth_m: float = SITE_PAD_M,
    search_m: float = WATER_SEARCH_M,
    local_dir: Path | None = None,
) -> SiteWater | None:
    """Measure the water at one site, or ``None`` where this machine has no terrain field.

    ``None`` is the ordinary case on a clone: the field is gitignored and every caller
    carries on without one, saying it did not measure rather than saying there is no water.
    """
    field = hf.load_field(local_dir)
    if field is None:
        return None
    x_cm, y_cm = x_m * 100.0, y_m * 100.0
    half_w, half_d = width_m * 50.0, depth_m * 50.0
    pad = field.area(x_cm - half_w, y_cm - half_d, x_cm + half_w, y_cm + half_d)
    # Searched only when the pad is dry: on a wet pad the answer is zero metres away, and
    # the search costs a full pass over a box 25x the pad's area.
    near = (
        None if pad.water_level_m is not None else field.nearest_water(x_cm, y_cm, search_m * 100)
    )
    return SiteWater(
        x_m=x_m,
        y_m=y_m,
        pad=pad,
        near=near,
        sea_level_m=water_volumes(projection)["sea_level_m"],
        build=field.build,
    )

"""The schematic's parts: blocks of machines, the buses between them, and the floors."""

from __future__ import annotations

import math
from dataclasses import dataclass, field

from ....core.gamedata.footprint import FOUNDATION_M, Packed

__all__ = ["LOGISTICS_FLOOR_M", "Block", "Bus", "Floor", "Layout"]

#: Height reserved for a logistics deck: belts, pipes and a walkway between them.
LOGISTICS_FLOOR_M = 4.0

#: Vertical headroom above the tallest machine on a production floor.
FLOOR_HEADROOM_M = 1.0

#: Foundations are 1, 2 or 4 m thick; floor heights round up to this.
FLOOR_STEP_M = 2.0

#: A manifold longer than this stops being sensible to build or feed evenly.
MAX_MACHINES_PER_BLOCK = 24


@dataclass
class Block:
    """One buildable module: a row of identical machines on one manifold."""

    key: str
    label: str
    building_id: str
    building: str
    recipe: str | None
    machines: int
    clock: float
    part: int  # 1-based index within a split group
    parts: int  # how many blocks the process was split into
    inputs: dict[str, float] = field(default_factory=dict[str, float])
    outputs: dict[str, float] = field(default_factory=dict[str, float])
    stage: int = 0
    #: Per-MACHINE dimensions, which is what the build table prints as "each(m)".
    width_m: float = 0.0
    depth_m: float = 0.0
    height_m: float = 0.0
    #: The whole block laid out on foundations. The single source of "how much floor do
    #: N of these need" -- see Footprint.pack. None only when the building has no
    #: clearance data at all, which `build_layout` reports rather than treating as free.
    packed: Packed | None = None

    @property
    def foundations(self) -> int:
        return self.packed.foundations if self.packed else 0

    @property
    def block_width_m(self) -> float:
        return self.packed.width_m if self.packed else 0.0

    @property
    def block_depth_m(self) -> float:
        return self.packed.depth_m if self.packed else 0.0

    @property
    def name(self) -> str:
        return f"{self.label} ({self.part}/{self.parts})" if self.parts > 1 else self.label


@dataclass
class Bus:
    """All movement of one item, pooled. Producers feed it, consumers draw from it."""

    item: str
    name: str
    rate: float
    carrier: str  # belt | pipe
    unit: str
    lines: int
    producers: list[str] = field(default_factory=list[str])
    consumers: list[str] = field(default_factory=list[str])
    from_stage: int = 0
    to_stage: int = 0
    external: bool = False  # enters or leaves the site


@dataclass
class Floor:
    index: int
    kind: str  # production | logistics
    stage: int | None
    height_m: float
    blocks: list[Block] = field(default_factory=list[Block])
    buses: list[Bus] = field(default_factory=list[Bus])
    #: Which declared site this floor belongs to, set when floors are stacked per site;
    #: empty outside a site partition.
    site: str = ""

    @property
    def foundations(self) -> int:
        return sum(b.foundations for b in self.blocks)

    @property
    def machines(self) -> int:
        return sum(b.machines for b in self.blocks)


@dataclass
class Layout:
    blocks: list[Block]
    buses: list[Bus]
    floors: list[Floor]
    warnings: list[str] = field(default_factory=list[str])

    @property
    def foundations(self) -> int:
        """Peak footprint: floors stack, so the site is sized by its largest floor."""
        return max((f.foundations for f in self.floors), default=0)

    @property
    def total_foundations(self) -> int:
        return sum(f.foundations for f in self.floors)

    @property
    def machines(self) -> int:
        return sum(b.machines for b in self.blocks)

    @property
    def height_m(self) -> float:
        return sum(f.height_m for f in self.floors)

    def site_side_m(self) -> float:
        """Side of a square site that fits the largest floor."""
        return math.ceil(math.sqrt(max(self.foundations, 1))) * FOUNDATION_M

"""Advisory region names -- layer 2 of the spatial design.

Layer 1 (``geo``) is the exact geometry every calculation uses; this module only attaches
human-readable names to coordinates, and **a region name must never feed a computation.**
``data/region_names.json`` is derived from the game's own ``FGMapAreaTexture``, whose
boundaries are exact polygon edges at 1.83 m, so a lookup's confidence is a statement about
this table's own resolution and nothing else. The file publishes a 256 m grid, which
``/api/regions`` serves and the map paints, and carries a 64 m one that lookups here prefer
-- a majority downsample at 256 m mislabels 14.1% of known world objects against 5.3% at
64 m -- while a table with no fine grid still loads and answers at its own resolution.
"""

from __future__ import annotations

import json
from dataclasses import dataclass

from ... import config
from . import geo

__all__ = ["OFF_MAP", "Label", "RegionMap", "load_regions", "region_rows"]

VOID = "."

#: What a coordinate the map names nothing at is called, in every response on both
#: interfaces. It was spelled three ways, and a reader cannot tell three wordings for one
#: fact from three different facts.
OFF_MAP = "off-map or ocean"

#: Confidence codes, worst to best. ``void`` is off the grid, or the game names nothing here
#: and no known static object stands within a kilometre. ``unnamed`` is the game's own
#: ``No Man's Land`` for the outer coast and ocean, and is a real answer. ``boundary`` means
#: an exact region edge crosses this cell, so a point inside it can be on either side.
#: ``interior`` means the whole cell is one region.
CONFIDENCE = {
    ".": "void",
    "u": "unnamed",
    "b": "boundary",
    "l": "interior",
}


@dataclass(frozen=True)
class Label:
    """The result of a coordinate -> name lookup."""

    name: str | None
    confidence: str  # void | unnamed | boundary | interior
    accuracy_m: int

    @property
    def certain(self) -> bool:
        return self.confidence == "interior"

    def describe(self) -> str:
        if self.name is None:
            return OFF_MAP
        if self.confidence == "interior":
            return self.name
        return f"{self.name} (~{self.accuracy_m}m accuracy, {self.confidence})"


@dataclass
class RegionMap:
    grid: list[str]
    confidence: list[str]
    legend: dict[str, str]
    regions: dict[str, dict]
    meta: dict
    x0: float
    y0: float
    cell: float
    nx: int
    ny: int
    #: The finer pair, when the table carries one. Same origin, same legend, smaller cell.
    fine: list[str] | None = None
    fine_confidence: list[str] | None = None
    fine_cell: float = 0.0
    fine_nx: int = 0
    fine_ny: int = 0

    # ---- coordinate -> name -------------------------------------------

    def cell_of(self, x: float, y: float) -> tuple[int, int] | None:
        """The PUBLISHED grid's cell at a point, or ``None`` off it.

        Public because ``/api/regions`` places its label anchors on this grid: asking
        ``label_for`` instead answers at the finer grid's resolution, which can put a label
        on a cell the payload paints as another region's.
        """
        i = int((x - self.x0) // self.cell)
        j = int((y - self.y0) // self.cell)
        if 0 <= i < self.nx and 0 <= j < self.ny:
            return i, j
        return None

    def _lookup(self, x: float, y: float) -> tuple[str, str, int] | None:
        """The raw ``(letter, confidence letter, accuracy)`` at a point, or ``None``.

        Prefers the fine pair and falls back to the published one: one origin, one legend,
        and a caller that never has to know which answered.
        """
        if self.fine and self.fine_confidence:
            i = int((x - self.x0) // self.fine_cell)
            j = int((y - self.y0) // self.fine_cell)
            if 0 <= i < self.fine_nx and 0 <= j < self.fine_ny:
                return self.fine[j][i], self.fine_confidence[j][i], int(self.fine_cell / 100)
            return None
        at = self.cell_of(x, y)
        if at is None:
            return None
        i, j = at
        return self.grid[j][i], self.confidence[j][i], int(self.cell / 100)

    def label_for(self, x: float, y: float) -> Label:
        """Name the region containing a point.

        Returns ``name=None`` for ocean and off-map coordinates rather than
        fabricating the nearest land label.
        """
        found = self._lookup(x, y)
        if found is None:
            return Label(None, "void", self.accuracy_m)
        letter, code, accuracy = found
        if letter == VOID:
            return Label(None, "void", accuracy)
        return Label(self.legend.get(letter), CONFIDENCE.get(code, "boundary"), accuracy)

    def label_for_node(self, node: dict) -> Label:
        """Name a resource node, which is to say: name where it stands.

        A position lookup and nothing else -- no per-node override table, which a map update
        renaming instances would silently strand anyway.
        """
        return self.label_for(node["x"], node["y"])

    @property
    def accuracy_m(self) -> int:
        """How far a name is trustworthy in metres: the finer grid's cell, if there is one."""
        default = int((self.fine_cell or self.cell) / 100)
        return int(self.meta.get("accuracy_m", default))

    # ---- name -> nodes -------------------------------------------------

    def names(self) -> list[str]:
        return sorted(self.regions)

    def resolve(self, name: str) -> str | None:
        """Resolve a region name case-insensitively, allowing unique prefixes."""
        q = name.strip().casefold()
        for known in self.regions:
            if known.casefold() == q:
                return known
        hits = [k for k in self.regions if k.casefold().startswith(q)]
        if len(hits) == 1:
            return hits[0]
        hits = [k for k in self.regions if q in k.casefold()]
        return hits[0] if len(hits) == 1 else None

    def filter_nodes(self, nodes: list[dict], name: str) -> list[dict]:
        """Nodes whose label matches ``name``."""
        resolved = self.resolve(name)
        if resolved is None:
            return []
        return [n for n in nodes if self.label_for_node(n).name == resolved]

    def summary(self, name: str) -> dict | None:
        resolved = self.resolve(name)
        if resolved is None:
            return None
        entry = dict(self.regions[resolved])
        cx, cy = entry["centroid"]
        entry["name"] = resolved
        entry["grid"] = geo.grid_cell(cx, cy)
        entry["direction"] = geo.direction_of(cx, cy)
        entry["anchor"] = self.label_anchor(resolved)
        return entry

    def label_anchor(self, name: str) -> tuple[float, float] | None:
        """A point inside a region that provably belongs to it, in centimetres.

        The coordinate to hand anyone who asks where a region IS. A centroid is a mean, and
        the mean of a concave region lands on its neighbour's ground -- Titan Forest's sits
        in the Swamp -- so the centroid is used only when its own cell carries the region's
        letter, and otherwise the anchor moves to the centre of the nearest cell that does.
        ``None`` for a name that resolves to no region.

        Measured against the PUBLISHED grid, never against ``label_for``: that reads the
        finer grid, and answering at that resolution puts the anchor on a cell the map
        paints as somebody else's.
        """
        resolved = self.resolve(name)
        if resolved is None:
            return None
        cx, cy = self.regions[resolved]["centroid"]
        letter = next((ch for ch, known in self.legend.items() if known == resolved), None)
        at = self.cell_of(cx, cy)
        if letter is None or (at is not None and self.grid[at[1]][at[0]] == letter):
            return cx, cy
        best: tuple[float, float, float] | None = None
        for j, row in enumerate(self.grid):
            for i, cell in enumerate(row):
                if cell != letter:
                    continue
                px = self.x0 + (i + 0.5) * self.cell
                py = self.y0 + (j + 0.5) * self.cell
                d = (px - cx) ** 2 + (py - cy) ** 2
                if best is None or d < best[0]:
                    best = (d, px, py)
        return (cx, cy) if best is None else (best[1], best[2])


def region_rows(table, rid: str | None, rows: list[dict] | None = None) -> list[dict]:
    """One row per named region, busiest first: anchor (cm), direction, grid, area, nodes.

    ``rows`` is the node pool counted, the whole table by default; with ``rid`` only that
    resource counts and a region holding none of it is left out.
    """
    rm = load_regions()
    pool = rows if rows is not None else table.nodes
    if rid:
        pool = [n for n in pool if n["resource"] == rid]
    out = []
    for name in rm.names():
        info = rm.summary(name)
        hits = rm.filter_nodes(pool, name)
        if rid and not hits:
            continue
        ax, ay = info["anchor"] or info["centroid"]
        out.append(
            {
                "name": name,
                "direction": geo.direction_of(ax, ay),
                "grid": geo.grid_cell(ax, ay),
                "anchor": (ax, ay),
                "area_km2": info["area_km2"],
                "nodes": len(hits),
            }
        )
    out.sort(key=lambda r: -r["nodes"])
    return out


#: Keyed by the file and its mtime, so a regenerated raster is picked up without a restart.
_MAP: dict[tuple[str, int], RegionMap] = {}


def load_regions() -> RegionMap:
    path = config.data_dir() / "region_names.json"
    if not path.is_file():
        raise FileNotFoundError(f"{path} missing -- run: uv run python tools/gen_region_names.py")
    key = (str(path), path.stat().st_mtime_ns)
    hit = _MAP.get(key)
    if hit is not None:
        return hit
    payload = json.loads(path.read_text(encoding="utf-8"))
    gm = payload["grid_meta"]
    region_map = RegionMap(
        grid=payload["region_grid"],
        confidence=payload["confidence_grid"],
        legend=payload["legend"],
        regions=payload["regions"],
        meta=payload.get("_meta", {}),
        x0=gm["x0"],
        y0=gm["y0"],
        cell=gm["cell"],
        nx=gm["nx"],
        ny=gm["ny"],
        fine=payload.get("fine_grid"),
        fine_confidence=payload.get("fine_confidence"),
        fine_cell=gm.get("fine_cell", 0.0),
        fine_nx=gm.get("fine_nx", 0),
        fine_ny=gm.get("fine_ny", 0),
    )
    _MAP.clear()
    _MAP[key] = region_map
    return region_map

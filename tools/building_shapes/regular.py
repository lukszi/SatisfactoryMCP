"""Regular outlines: each traced ring is offered as a regular polygon (round parts) and with
straight walls, and the regular form is kept only while it still covers the raster about as
well as the plain simplification does. docs/building-shapes.md, "Regular outlines"."""

from __future__ import annotations

import math
from typing import NamedTuple

from satisfactory_mcp.core.arrays import BoolMask
from tools.building_shapes.mirror import Axis, mirror_axes, mirror_score, symmetrise
from tools.building_shapes.polygons import (
    Grid,
    Point,
    Ring,
    covered,
    iou,
    nearest_on,
    ring_area,
)
from tools.building_shapes.rounds import fit_circle, regular_polygon
from tools.building_shapes.straighten import Line, StraightOptions, straighten

__all__ = ["RegularOptions", "Regulariser", "RingLog"]


class RegularOptions(NamedTuple):
    straight: StraightOptions = StraightOptions()
    #: Snapping angles to 0 and 90 degrees tried in turn; the widest that still covers the
    #: raster well enough wins.
    snap_levels: tuple[float, ...] = (12.0, 20.0, 30.0)
    #: A ring is round when its trace sits this close to one circle: a share of the radius,
    #: and never stricter than ``circle_floor`` cells (a staircase's own wobble).
    circle_rms: float = 0.02
    circle_floor: float = 0.3
    #: Sides of a round part's polygon, at most; fewer under a tight vertex budget.
    sides: int = 24
    #: A regular form may cover the raster (IoU) at most this much worse than the plain
    #: simplification.
    max_loss: float = 0.03
    #: A raster matching its mirror image this well (IoU) about an axis is made symmetric.
    mirror: float = 0.93
    #: A port within ``port_reach`` of the plain footprint may end at most ``port_drift``
    #: further from the regular one (world units, cm).
    port_reach: float = 100.0
    port_drift: float = 30.0


class RingLog(NamedTuple):
    """What became of one ring: ``kind`` is ``round``, ``straight`` or ``plain``."""

    kind: str
    plain_iou: float
    iou: float


#: A band's regular ring may cover this many more cells outside the level below than its plain
#: ring does, or this share of its own cells if more.
_SPILL_CELLS = 16
_SPILL_SHARE = 0.01


def _sides(budget: int, most: int) -> int:
    return max(8, min(most, budget) // 4 * 4)


class Regulariser:
    """Regularises the rings of one class: the footprint first, which sets the mirror axes and
    the walls the bands above it may share."""

    def __init__(self, options: RegularOptions, ports: list[Point] | None = None) -> None:
        self.options = options
        #: The class's port positions, which the footprint must keep sitting on.
        self.ports = ports or []
        self.axes: list[Axis] = []
        self.guides: list[Line] = []
        self.log: list[RingLog] = []
        #: The rings of the level below, which a band is kept inside.
        self.parent: list[Ring] | None = None
        #: The parent's cells on the grid being traced, None for the footprint.
        self.inside: BoolMask | None = None
        self._allowed = 0
        self._port_gaps: list[float] = []

    def find_axes(self, grid: Grid) -> None:
        self.axes = mirror_axes(grid, self.options.mirror)

    def ring(self, trace: list[Point], blob: Grid, budget: int, plain: Ring) -> Ring:
        """The regular form of one blob's ``trace`` (world units), or ``plain`` when none
        covers ``blob`` well enough or one would spill out of the level below."""
        plain_iou = iou(covered([plain], blob), blob.mask)
        self._allowed = self._spill(plain, blob)
        self._port_gaps = [math.dist(port, nearest_on(port, plain)) for port in self.ports]
        floor = plain_iou - self.options.max_loss
        best: tuple[str, float, Ring, list[Line]] = ("plain", plain_iou, plain, [])
        for level in self.options.snap_levels:
            straight = self._straight(trace, blob.cell, budget, level)
            if straight is not None:
                score = self._score(straight[0], blob)
                if score >= floor:
                    best = ("straight", score, *straight)
        circle = fit_circle(trace, blob.cell / 2)
        if circle is not None and circle.rms <= max(
            self.options.circle_rms * circle.r, self.options.circle_floor * blob.cell
        ):
            for axis in self.axes:
                if abs(circle[axis.dim] - axis.at) <= blob.cell:
                    circle = circle._replace(**{"xy"[axis.dim]: axis.at})
            ring = regular_polygon(circle, _sides(budget, self.options.sides))
            score = self._score(ring, blob)
            if score >= floor and score >= best[1] - self.options.max_loss:
                best = ("round", score, ring, [])
        kind, score, ring, lines = best
        self.guides.extend(lines)
        self.log.append(RingLog(kind, plain_iou, score))
        return ring

    def _score(self, ring: Ring, blob: Grid) -> float:
        """IoU of ``ring`` with ``blob``, or -1 where it spills out of ``inside`` or moves
        off a port the plain ring had on it."""
        if self.inside is None and not self._keeps_ports(ring):
            return -1.0
        cells = covered([ring], blob)
        if self._spill(ring, blob) > self._allowed + max(_SPILL_CELLS, _SPILL_SHARE * cells.sum()):
            return -1.0
        return iou(cells, blob.mask)

    def _keeps_ports(self, ring: Ring) -> bool:
        limit = self.options.port_reach
        for port, gap in zip(self.ports, self._port_gaps, strict=True):
            if (
                gap <= limit
                and math.dist(port, nearest_on(port, ring)) > gap + self.options.port_drift
            ):
                return False
        return True

    def _spill(self, ring: Ring, blob: Grid) -> int:
        if self.inside is None:
            return 0
        return int((covered([ring], blob) & ~self.inside).sum())

    def _straight(
        self, trace: list[Point], cell: float, budget: int, level: float
    ) -> tuple[Ring, list[Line]] | None:
        options = self.options.straight._replace(snap_angle=level)
        for _ in range(12):
            ring, lines = straighten(trace, cell, options, self.guides)
            if len(ring) >= 3 and ring_area(ring) > 0 and len(ring) <= budget:
                return ring, lines
            options = options._replace(fine=options.fine * 1.25, arc_step=options.arc_step * 1.25)
        return None

    def _worse(self, old: Ring, new: Ring, grid: Grid) -> bool:
        """Whether ``new`` spills or leaves a port where ``old`` did not."""
        self._port_gaps = [math.dist(port, nearest_on(port, old)) for port in self.ports]
        return self._score(new, grid) < 0

    def symmetric(self, rings: list[Ring], grid: Grid) -> list[Ring]:
        """``rings`` made symmetric about each class axis this grid also nearly has."""
        min_edge = self.options.straight.min_edge * grid.cell
        if not rings:
            return rings
        self._allowed = sum(self._spill(ring, grid) for ring in rings)
        for axis in self.axes:
            if mirror_score(grid, axis) < self.options.mirror:
                continue
            before = iou(covered(rings, grid), grid.mask)
            mirrored = symmetrise(rings, grid, axis, min_edge)
            if any(self._worse(old, new, grid) for old, new in zip(rings, mirrored, strict=True)):
                continue
            if iou(covered(mirrored, grid), grid.mask) >= before - self.options.max_loss:
                rings = mirrored
        return rings

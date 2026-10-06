"""Belt and pipe runs as queryable things, not just drawable ones.

A run is a belt CHAIN or a single pipeline PIECE, measured along the line the map draws, and
what stands at each end is a NEAREST-PORT guess labelled as one; coordinates stay in the
save's centimetres, every threshold in the metres it is compared in. The EXACT answer to
"what is on the end of this" is ``logistics``, which shares the ``chain:<n>``/``pipe:<row>``
idents. docs/mcp-surface.md §10.1f and docs/save-projection.md §6.15 have the measurements.
"""

from __future__ import annotations

import itertools
import math
import re
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import NamedTuple

from ...core.gamedata.model import GameData
from ...core.saveio import ports
from ...core.saveio import rows as saverows
from ...core.saveio.schema import (
    BuildableRecord,
    PipeNetwork,
    Projection,
    SplineSpan,
    StorageRecord,
)
from ..spatial import geo
from .flow import BASIS_NONE, PipeFlow

__all__ = [
    "JOINT_M",
    "NEAR_RADIUS_M",
    "PORT_REACH_M",
    "ConduitRun",
    "End",
    "build_runs",
    "near_counts",
]

#: How far from a place a run counts as near it: every finder, tool and popup uses this.
NEAR_RADIUS_M = 250.0

#: Two piece endpoints within this are one joint; a real discontinuity is metres off.
JOINT_M = 1.5

#: How far outside a building's half-footprint an endpoint may sit and still plug into it.
PORT_REACH_M = 2.5

#: Half-footprint of a placement the dump has no clearance for: a 4x4 m splitter or merger.
_HALF_DEFAULT_M = 2.0

#: An endpoint a whole storey above a small building passes over it rather than feeding it.
_PORT_Z_M = 12.0

#: The plug lookup's grid pitch, cm: above the largest reach (~53 m on a Nuclear Power Plant)
#: so the 3x3 scan cannot miss one.
_PLUG_CELL_CM = 6400.0

#: A conveyor lift, by the native class ``carriers`` and the belts endpoint classify on.
_LIFT_NATIVE = "FGBuildableConveyorLift"

_MK = re.compile(r"mk\.?\s*(\d)", re.IGNORECASE)


@dataclass
class End:
    """One end of a run: where it is (cm) and what stands there, if anything known."""

    x: float
    y: float
    z: float
    plugs: str | None = None


@dataclass
class ConduitRun:
    """One conduit run: a belt chain or a single pipeline piece.

    ``a``/``b`` are in travel order for belts (input to output -- the projection stores
    points that way) and in FLOW order for a pipe whose network resolves a direction;
    ``directed`` says whether that ordering means anything. ``via`` lists things the
    material graph says the run touches that have no position to hang an end on --
    pumps, valves and junctions, pipes only.
    """

    kind: str  # belt | lift | pipe
    ident: str  # chain:<n> | pipe:<row>
    label: str  # "belt mk3", "lift mk4", "pipe mk2" -- mixed chains show a span
    pieces: int
    length_m: float
    a: End
    b: End
    z_min_m: float
    z_max_m: float
    directed: bool
    rate: float | None  # slowest tier's items_per_min, or the pipe class's flow_m3_min
    fluid: str | None = None  # item id, pipes only
    #: What ``directed`` was inferred from, pipes only: ``flow``'s basis. ``None`` on a belt,
    #: whose order is its pieces' own.
    basis: str | None = None
    #: The game's own FGPipeNetwork id, pipes only: two areas touching one network ARE
    #: joined even when no single piece passes near both.
    network: int | None = None
    via: list[str] = field(default_factory=list[str])
    #: The polylines (cm) the distance query runs over; one per piece.
    _lines: list[list[list[float]]] = field(default_factory=list[list[list[float]]])

    def midpoint(self) -> tuple[float, float]:
        """The point half way along the drawn line, in centimetres; the mean of the two ends
        sits off the belt when a chain doubles back around a platform."""
        spans = [
            (p, q, geo.distance_3d_m(p, q))
            for line in self._lines
            for p, q in itertools.pairwise(line)
        ]
        total = sum(d for _p, _q, d in spans)
        if not total:
            return self.a.x, self.a.y
        walked, half = 0.0, total / 2.0
        for p, q, d in spans:
            if walked + d >= half:
                t = (half - walked) / d
                return p[0] + t * (q[0] - p[0]), p[1] + t * (q[1] - p[1])
            walked += d
        return self.b.x, self.b.y

    @property
    def lines(self) -> list[list[list[float]]]:
        return self._lines

    def dist_m(self, x: float, y: float) -> float:
        """Closest 2D approach of the run to a point (cm in, metres out), measured to the
        spans: a 500 m straight belt has two stored points and its middle is on neither."""
        best = math.inf
        for line in self._lines:
            for p, q in itertools.pairwise(line):
                best = min(best, _seg_dist_m(x, y, p, q))
            if len(line) == 1:
                best = min(best, geo.distance_m((x, y), (line[0][0], line[0][1])))
        return best


class PlugTarget(NamedTuple):
    """A placed thing an endpoint could plug into, in cm, with its reach and height in m."""

    x: float
    y: float
    z: float
    reach_m: float
    height_m: float
    name: str


def _seg_dist_m(x: float, y: float, p: list[float], q: list[float]) -> float:
    """Point-to-span distance in metres, everything else in cm. The projection onto the
    span is unit-free arithmetic; the one actual distance routes through ``geo``."""
    px, py, qx, qy = p[0], p[1], q[0], q[1]
    dx, dy = qx - px, qy - py
    if dx == 0 and dy == 0:
        return geo.distance_m((x, y), (px, py))
    t = ((x - px) * dx + (y - py) * dy) / (dx * dx + dy * dy)
    t = max(0.0, min(1.0, t))
    return geo.distance_m((x, y), (px + t * dx, py + t * dy))


#: Eight-point Gauss-Legendre on ``[0, 1]`` as ``(t, weight)``: well under a millimetre on
#: the sharpest elbow the game builds.
_GAUSS = tuple(
    (0.5 + 0.5 * x, 0.5 * w)
    for x, w in (
        (-0.9602898564975363, 0.1012285362903763),
        (-0.7966664774136267, 0.2223810344533745),
        (-0.5255324099163290, 0.3137066458778873),
        (-0.1834346424956498, 0.3626837833783620),
        (0.1834346424956498, 0.3626837833783620),
        (0.5255324099163290, 0.3137066458778873),
        (0.7966664774136267, 0.2223810344533745),
        (0.9602898564975363, 0.1012285362903763),
    )
)


def _arc_cm(p0: list[float], p1: list[float], m0: list[float], m1: list[float]) -> float:
    """Arc length of one cubic Hermite span, in the centimetres its inputs are in.

    ``Q(t) = h00 p0 + h10 m0 + h01 p1 + h11 m1``, integrated as ``|Q'(t)|`` -- the same
    curve ``/api/belts`` tessellates and the frontend draws, so the two halves report one
    length for one belt.
    """
    total = 0.0
    for t, weight in _GAUSS:
        a = 6.0 * t * t - 6.0 * t  # h00', and h01' is its negation
        b = 3.0 * t * t - 4.0 * t + 1.0  # h10'
        c = 3.0 * t * t - 2.0 * t  # h11'
        dx = a * (p0[0] - p1[0]) + b * m0[0] + c * m1[0]
        dy = a * (p0[1] - p1[1]) + b * m0[1] + c * m1[1]
        dz = a * (p0[2] - p1[2]) + b * m0[2] + c * m1[2]
        total += weight * math.sqrt(dx * dx + dy * dy + dz * dz)
    return total


def _tangents(
    spans: Sequence[SplineSpan] | None, index: int
) -> tuple[list[float], list[float]] | None:
    """One span's ``[leave, arrive]`` pair, or ``None`` where it is straight.

    Read guarded on the same terms as the points beside it -- schema 15 emits the column
    only for a route that bends, stores ``0`` for a flat span inside a bent one, and a row
    that will not decode costs its curve rather than the run.
    """
    if not isinstance(spans, (list, tuple)) or index >= len(spans):
        return None
    entry = spans[index]
    if not isinstance(entry, (list, tuple)) or len(entry) != 6:
        return None
    try:
        vals = [float(v) for v in entry]
    except (TypeError, ValueError):
        return None
    return vals[:3], vals[3:]


def _length_m(line: list[list[float]], spans: Sequence[SplineSpan] | None = None) -> float:
    """3D drawn length in metres, a riser's vertical leg included: a span with recorded
    tangents is integrated along its spline, one without is its chord."""
    total = 0.0
    for i, (p, q) in enumerate(itertools.pairwise(line)):
        curve = _tangents(spans, i)
        if curve is None:
            total += geo.distance_3d_m(p, q)
        else:
            total += _arc_cm(p, q, *curve) / geo.CM_PER_M
    return total


def _mk_label(kind: str, classes: set[str]) -> str:
    """ "belt mk3", or "belt mk1-mk3" for a mixed chain: the tier off the class id."""
    mks: set[int] = set()
    for cls in classes:
        m = _MK.search(cls or "")
        mks.add(int(m.group(1)) if m else 1)
    if not mks:
        return kind
    lo, hi = min(mks), max(mks)
    return f"{kind} mk{lo}" if lo == hi else f"{kind} mk{lo}-mk{hi}"


def _open_ends(pieces: Sequence[saverows.BeltSegment]) -> tuple[End, End, bool]:
    """A chain's two extremities, oriented input->output where the joints prove it.

    An extremity is an endpoint no other piece's opposite endpoint sits on (within
    ``JOINT_M``). Exactly one open input and one open output make the run directed; any other
    shape falls back to the table order's outer corners, undirected, which costs the arrow
    and never the run.
    """
    starts = [(p.points[0], i) for i, p in enumerate(pieces)]
    ends = [(p.points[-1], i) for i, p in enumerate(pieces)]

    def _open(
        candidates: list[tuple[list[float], int]], others: list[tuple[list[float], int]]
    ) -> list[list[float]]:
        out: list[list[float]] = []
        for point, i in candidates:
            if not any(
                j != i and geo.distance_3d_m(point, other) <= JOINT_M for other, j in others
            ):
                out.append(point)
        return out

    open_in = _open(starts, ends)
    open_out = _open(ends, starts)
    if len(open_in) == 1 and len(open_out) == 1:
        (ax, ay, az), (bx, by, bz) = open_in[0], open_out[0]
        return End(ax, ay, az), End(bx, by, bz), True
    first, last = pieces[0].points[0], pieces[-1].points[-1]
    return _end(first), _end(last), False


def _end(point: list[float]) -> End:
    """An end at a polyline point, nothing yet known to stand there."""
    return End(point[0], point[1], point[2])


def _placements(projection: Projection, game: GameData) -> list[PlugTarget]:
    """Everything an endpoint could plug into."""
    out: list[PlugTarget] = []
    for key in ("machines", "extractors", "generators", "storage", "attachments"):
        records: Sequence[BuildableRecord | StorageRecord] = projection.get(key) or ()
        for record in records:
            if not isinstance(record, dict):
                continue
            pos = record.get("pos")
            if not pos or len(pos) < 3:
                continue
            cls = record.get("cls") or ""
            building = game.buildings.get(cls)
            footprint = getattr(building, "footprint", None) if building else None
            half = max(footprint.width_m, footprint.depth_m) / 2.0 if footprint else _HALF_DEFAULT_M
            height = footprint.height_m if footprint else _PORT_Z_M
            name = (game.building_name(cls) or cls) if cls else "?"
            out.append(
                PlugTarget(
                    float(pos[0]),
                    float(pos[1]),
                    float(pos[2]),
                    half + PORT_REACH_M,
                    max(height, _PORT_Z_M),
                    name,
                )
            )
    return out


def _plug(
    end: End, targets: list[PlugTarget], cells: dict[tuple[int, int], list[int]]
) -> str | None:
    """The nearest placement whose reach covers the endpoint, or None: a geometric guess."""
    best, best_score = None, math.inf
    cx, cy = int(end.x // _PLUG_CELL_CM), int(end.y // _PLUG_CELL_CM)
    for dx in (-1, 0, 1):
        for dy in (-1, 0, 1):
            for i in cells.get((cx + dx, cy + dy), ()):
                target = targets[i]
                if not (-_PORT_Z_M <= (end.z - target.z) / geo.CM_PER_M <= target.height_m):
                    continue
                d = geo.distance_m((end.x, end.y), (target.x, target.y))
                if d <= target.reach_m and d - target.reach_m < best_score:
                    best, best_score = target.name, d - target.reach_m
    return best


def _belt_runs(projection: Projection, game: GameData) -> list[ConduitRun]:
    """One run per belt chain, its pieces grouped and its open ends oriented."""
    runs: list[ConduitRun] = []
    by_chain: dict[int, list[saverows.BeltSegment]] = {}
    for seg in saverows.iter_belt_segments(projection):
        by_chain.setdefault(seg.chain, []).append(seg)
    for chain, pieces in by_chain.items():
        classes = {p.cls for p in pieces if p.cls}
        natives = {(game.buildings[c].native if c in game.buildings else None) for c in classes}
        kind = "lift" if natives == {_LIFT_NATIVE} else "belt"
        a, b, directed = _open_ends(pieces)
        zs = [pt[2] for p in pieces for pt in p.points]
        rates = [
            game.buildings[c].items_per_min
            for c in classes
            if c in game.buildings and game.buildings[c].items_per_min
        ]
        runs.append(
            ConduitRun(
                kind=kind,
                ident=f"chain:{chain}",
                label=_mk_label(kind, classes),
                pieces=len(pieces),
                length_m=sum(_length_m(p.points, p.spans) for p in pieces),
                a=a,
                b=b,
                z_min_m=min(zs) / geo.CM_PER_M,
                z_max_m=max(zs) / geo.CM_PER_M,
                directed=directed,
                rate=min(rates) if rates else None,
                _lines=[p.points for p in pieces],
            )
        )
    return runs


def _material_adjacency(projection: Projection) -> dict[int, set[int]]:
    """Actor index to the actor indices it shares a material coupling with; no hypertubes."""
    graph = projection.get("graph") or {}
    roles = graph.get("roles") or []

    def role(index: object) -> str:
        return roles[index] if isinstance(index, int) and 0 <= index < len(roles) else ""

    adjacency: dict[int, set[int]] = {}
    for edge in graph.get("material") or ():
        if isinstance(edge, (list, tuple)) and len(edge) >= 2:
            if len(edge) >= 4 and ports.is_hypertube_edge(role(edge[2]), role(edge[3])):
                continue
            try:
                ai, bi = int(edge[0]), int(edge[1])
            except (TypeError, ValueError):
                continue
            adjacency.setdefault(ai, set()).add(bi)
            adjacency.setdefault(bi, set()).add(ai)
    return adjacency


def _pipe_runs(
    projection: Projection, game: GameData, pipe_flow: list[PipeFlow] | None
) -> list[ConduitRun]:
    """One run per pipeline piece, ends in flow order where the network resolves one."""
    runs: list[ConduitRun] = []
    networks: list[PipeNetwork] = list((projection.get("pipes") or {}).get("networks") or ())
    actors = (projection.get("graph") or {}).get("actors") or []
    adjacency = _material_adjacency(projection)
    # Conduit geometry classes: a pipe's graph neighbour of one is plumbing continuing.
    internal = set((projection.get("pipes") or {}).get("classes") or ()) | set(
        (projection.get("belts") or {}).get("classes") or ()
    )
    for seg in saverows.iter_pipe_segments(projection):
        entry = networks[seg.network_index] if 0 <= seg.network_index < len(networks) else None
        fluid = entry.get("fluid") if isinstance(entry, dict) else None
        flow: PipeFlow | dict[str, str] = (
            pipe_flow[seg.index]
            if pipe_flow
            and 0 <= seg.index < len(pipe_flow)
            and isinstance(pipe_flow[seg.index], dict)
            else {}
        )
        direction = flow.get("direction", "unknown")
        points = seg.points if direction != "reverse" else list(reversed(seg.points))
        a, b = _end(points[0]), _end(points[-1])
        via: list[str] = []
        for neighbour in sorted(adjacency.get(seg.actor_index, ())) if seg.actor_index >= 0 else ():
            leaf = actors[neighbour] if 0 <= neighbour < len(actors) else ""
            cls = str(leaf).rsplit("_", 1)[0]
            if not cls.startswith("Build_") or cls in internal:
                continue
            name = game.building_name(cls) or cls
            if name not in via:
                via.append(name)
        building = game.buildings.get(seg.cls) if seg.cls else None
        zs = [pt[2] for pt in seg.points]
        runs.append(
            ConduitRun(
                kind="pipe",
                ident=f"pipe:{seg.index}",
                label=_mk_label("pipe", {seg.cls} if seg.cls else set()),
                pieces=1,
                length_m=_length_m(seg.points, seg.spans),
                a=a,
                b=b,
                z_min_m=min(zs) / geo.CM_PER_M,
                z_max_m=max(zs) / geo.CM_PER_M,
                directed=direction in ("forward", "reverse"),
                rate=(building.flow_m3_min or None) if building else None,
                fluid=fluid,
                basis=flow.get("basis", BASIS_NONE),
                network=entry.get("id") if isinstance(entry, dict) else None,
                via=via,
                _lines=[seg.points],
            )
        )
    return runs


def _plug_ends(runs: list[ConduitRun], projection: Projection, game: GameData) -> None:
    """Name what stands at each end, and drop a ``via`` an end already names."""
    targets = _placements(projection, game)
    cells: dict[tuple[int, int], list[int]] = {}
    for i, target in enumerate(targets):
        cells.setdefault(
            (int(target.x // _PLUG_CELL_CM), int(target.y // _PLUG_CELL_CM)), []
        ).append(i)
    for run in runs:
        run.a.plugs = _plug(run.a, targets, cells)
        run.b.plugs = _plug(run.b, targets, cells)
        run.via = [v for v in run.via if v not in (run.a.plugs, run.b.plugs)]


def _joint_key(end: End) -> tuple[int, int, int]:
    """The joint grid cell of an end; the grid is keyed in the coordinates' own cm."""
    cell = JOINT_M * geo.CM_PER_M
    return (int(end.x // cell), int(end.y // cell), int(end.z // cell))


def _nearest_joint(
    end: End,
    run: ConduitRun,
    joints: dict[tuple[int, int, int], list[End]],
    owner: dict[int, ConduitRun],
) -> str | None:
    """The ident of the nearest other run with an end on this one's joint, if any."""
    kx, ky, kz = _joint_key(end)
    best: str | None = None
    best_d = JOINT_M
    for dx in (-1, 0, 1):
        for dy in (-1, 0, 1):
            for dz in (-1, 0, 1):
                for other in joints.get((kx + dx, ky + dy, kz + dz), ()):
                    if owner[id(other)] is run:
                        continue
                    d = geo.distance_3d_m((end.x, end.y, end.z), (other.x, other.y, other.z))
                    if d <= best_d:
                        best, best_d = owner[id(other)].ident, d
    return best


def _link_joints(runs: list[ConduitRun]) -> None:
    """Name an end still unplugged by the run it continues into, so a route can be followed
    piece to piece instead of dead-ending at every joint."""
    joints: dict[tuple[int, int, int], list[End]] = {}
    owner: dict[int, ConduitRun] = {}
    for run in runs:
        for end in (run.a, run.b):
            joints.setdefault(_joint_key(end), []).append(end)
            owner[id(end)] = run
    for run in runs:
        for end in (run.a, run.b):
            if end.plugs is not None:
                continue
            best = _nearest_joint(end, run, joints, owner)
            if best is not None:
                end.plugs = best


def build_runs(
    projection: Projection, game: GameData, pipe_flow: list[PipeFlow] | None = None
) -> list[ConduitRun]:
    """Every conduit run in a projection, belts grouped by chain, pipes one per piece.

    ``pipe_flow`` is ``WorldState.pipe_flow``, positional over the raw pipe table, and orients
    a pipe's ends where the network resolves a direction; without it every pipe is undirected.
    """
    runs = _belt_runs(projection, game) + _pipe_runs(projection, game, pipe_flow)
    _plug_ends(runs, projection, game)
    _link_joints(runs)
    return runs


def near_counts(runs: list[ConduitRun], x: float, y: float, radius_m: float) -> dict[str, int]:
    """How many runs pass within ``radius_m`` of a point (cm in): ``belt`` (lifts counted in)
    and ``pipe``, both ALWAYS present, because the zero is the answer this exists for."""
    out = {"belt": 0, "pipe": 0}
    for run in runs:
        if run.dist_m(x, y) <= radius_m:
            out["pipe" if run.kind == "pipe" else "belt"] += 1
    return out

"""Belt and pipe runs as queryable things, not just drawable ones.

The projection has carried every conveyor and pipeline polyline since schemas 12 and 13,
and until this module the only reader was the web map. Every TEXT surface either walked
through them silently (``trace_upstream`` traverses logistics and reports machines) or
never looked (``describe_location`` sampled foundations and buildings), so "is there a
pipe between these extractors and that platform" had no answer at all -- the assistant
twice told the player a build did not exist when the tools simply could not see it.

A **run** here is the unit a player talks about:

* for belts, the CHAIN -- the game's own grouping of consecutive conveyor pieces, split
  wherever a splitter, merger or machine interrupts the line. 1,909 chains over 3,085
  pieces on the reference world, so most runs are one piece and the long haulers are the
  group-by this module exists for.
* for pipes, the PIECE -- one placed pipeline actor, which is already one player-built
  stretch between two joints. Pipes have no chain; their network is a whole plumbing
  system (19 networks claim all 503 pipes here) and "the run" at network granularity
  would be a map-wide blob with no endpoints.

Lengths follow the drawn line: a span whose tangents schema 15 records is integrated along
its own spline, and a span with none is its chord. The projection stores tangents exactly
where a chord would be out by a centimetre or more, so both branches measure the curve the
map draws -- a chord across one bend was out by 16.4 m.

Endpoint attachment here is a NEAREST-PORT guess, labelled as one: what stands at an end is
answered geometrically, by the closest placed thing whose footprint (plus a port's reach)
covers the endpoint. ``None`` where nothing known stands there -- a pipe ending at a junction
reports the junction only when the material graph names it, because junctions, pumps and
valves are not placements in any projection table. Coordinates stay in the save's
centimetres; every distance routes through ``spatial.geo`` and every threshold is stated in
the metres it is compared in.

The EXACT answer to "what is on the end of this" is ``domain.world.logistics``, which
contracts the same conduit out of the save's own connection records; since schema 20 both
tables carry an actor index, so a run there and a run here share the ``chain:<n>`` and
``pipe:<row>`` ident and the two views can be read against each other.
"""

from __future__ import annotations

import itertools
import math
import re
from dataclasses import dataclass, field

from ...core.saveio import ports
from ...core.saveio import rows as saverows
from ..spatial import geo
from ..spatial.origin import resolve_origin
from .flow import BASIS_NONE

__all__ = [
    "JOINT_M",
    "KINDS",
    "PORT_REACH_M",
    "ConduitSearch",
    "End",
    "NetworkView",
    "Run",
    "build_runs",
    "near_counts",
    "networks",
    "search",
]

#: Two piece endpoints within this of each other are the same joint. Measured over the
#: reference world's 254 multi-piece chains: the median end-to-start gap is 0.0 cm and a
#: real discontinuity (a lift rising out of a belt's plane) is metres, not centimetres.
JOINT_M = 1.5

#: How far outside a building's own half-footprint an endpoint may sit and still count
#: as plugged into it. Ports sit ON the hull; positions are centres; a belt's last point
#: is at the connector, so the slack only has to cover the port's own depth.
PORT_REACH_M = 2.5

#: Fallback half-footprint for a placement the dump has no clearance for -- a splitter
#: or merger is 4x4 m, so half of that plus the port reach covers it.
_HALF_DEFAULT_M = 2.0

#: Vertical gate for "plugged into": a lift can meet a machine's elevated port, but an
#: endpoint a whole storey above a small building is passing over it, not feeding it.
_PORT_Z_M = 12.0

#: The docs dump's native class for a conveyor lift -- the same field
#: ``domain.world.carriers`` and the belts endpoint classify by, because a substring
#: match on an engine id is not a classification.
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
class Run:
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
    #: What ``directed`` was inferred FROM, pipes only -- ``domain.world.flow``'s basis, the
    #: same evidence ``/api/pipes`` publishes. ``None`` on a belt, whose order is the
    #: pieces' own and is not inferred at all.
    basis: str | None = None
    #: The game's own FGPipeNetwork id, pipes only. The fact that matters for "is there
    #: a pipe from A to B": every piece of one network is one connected plumbing system,
    #: so two areas touching the same network ARE joined even when no single piece
    #: passes near both.
    network: int | None = None
    via: list[str] = field(default_factory=list)
    #: The polylines (cm) the distance query runs over; one per piece.
    _lines: list[list[list[float]]] = field(default_factory=list)

    def midpoint(self) -> tuple[float, float]:
        """The point half way along the drawn line, in centimetres.

        Where to centre a search on a run named by its ident. The mean of the two ends is
        not it: a chain that doubles back around a platform has a mean sitting off the
        belt entirely.
        """
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
        """Closest 2D approach of the run to a point (cm in, metres out). Segment
        distance, not point distance: a 500 m straight belt has exactly two stored
        points, so measuring to the points alone would miss every mid-span crossing --
        the exact blindness this module exists to remove."""
        best = math.inf
        for line in self._lines:
            for p, q in itertools.pairwise(line):
                best = min(best, _seg_dist_m(x, y, p, q))
            if len(line) == 1:
                best = min(best, geo.distance_m((x, y), (line[0][0], line[0][1])))
        return best


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


#: Eight-point Gauss-Legendre quadrature, already mapped onto ``[0, 1]`` as ``(t, weight)``.
#: The integrand is the norm of a quadratic, so eight nodes hold the arc of the sharpest
#: elbow the game builds to well under a millimetre -- far below the whole-centimetre
#: resolution the control points themselves are stored at.
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


def _arc_cm(p0: list[float], p1: list[float], m0, m1) -> float:
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


def _tangents(spans, index: int) -> tuple[list[float], list[float]] | None:
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


def _length_m(line: list[list[float]], spans=None) -> float:
    """3D drawn length in metres: the vertical leg of a lift or a downcomer is real
    conveyor and real pipe, so a plan-view length would sell every riser short.

    A span whose tangents the save records is INTEGRATED along its spline rather than cut
    across its chord: a chord is out by up to 16.4 m on a single piece, which is the map's
    own measurement, and a text answer that disagreed with the drawn line by that much is
    a different belt."""
    total = 0.0
    for i, (p, q) in enumerate(itertools.pairwise(line)):
        curve = _tangents(spans, i)
        if curve is None:
            total += geo.distance_3d_m(p, q)
        else:
            total += _arc_cm(p, q, *curve) / geo.CM_PER_M
    return total


def _mk_label(kind: str, classes: set[str], game) -> str:
    """ "belt mk3", or "belt mk1-mk3" for a mixed chain: the tier off the class id."""
    mks = set()
    for cls in classes:
        m = _MK.search(cls or "")
        mks.add(int(m.group(1)) if m else 1)
    if not mks:
        return kind
    lo, hi = min(mks), max(mks)
    return f"{kind} mk{lo}" if lo == hi else f"{kind} mk{lo}-mk{hi}"


def _open_ends(pieces: list) -> tuple[End, End, bool]:
    """A chain's two extremities, oriented input->output where the joints prove it.

    Every piece's first point is its input and its last its output. An extremity is an
    endpoint no other piece's opposite endpoint sits on (within ``JOINT_M``). With
    exactly one open input and one open output the run is directed; any other shape --
    a torn chain, pieces out of order past the tolerance -- falls back to the table
    order's outer corners, undirected, which costs the arrow and never the run.
    """
    starts = [(p.points[0], i) for i, p in enumerate(pieces)]
    ends = [(p.points[-1], i) for i, p in enumerate(pieces)]

    def _open(candidates, others):
        out = []
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
    return End(*first[:3]), End(*last[:3]), False


def _placements(projection: dict, game) -> list[tuple[float, float, float, float, float, str]]:
    """Everything an endpoint could plug into: (x, y, z in cm, reach_m, height_m, name)."""
    out = []
    for key in ("machines", "extractors", "generators", "storage", "attachments"):
        for record in projection.get(key) or ():
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
                (
                    float(pos[0]),
                    float(pos[1]),
                    float(pos[2]),
                    half + PORT_REACH_M,
                    max(height, _PORT_Z_M),
                    name,
                )
            )
    return out


def _plug(end: End, placements, cells: dict) -> str | None:
    """The nearest placement whose reach covers the endpoint, or None. A guess by
    geometry, and the caller says so -- see the module docstring."""
    best, best_score = None, math.inf
    cx, cy = int(end.x // _CELL), int(end.y // _CELL)
    for dx in (-1, 0, 1):
        for dy in (-1, 0, 1):
            for i in cells.get((cx + dx, cy + dy), ()):
                px, py, pz, reach, height, name = placements[i]
                if not (-_PORT_Z_M <= (end.z - pz) / geo.CM_PER_M <= height):
                    continue
                d = geo.distance_m((end.x, end.y), (px, py))
                if d <= reach and d - reach < best_score:
                    best, best_score = name, d - reach
    return best


#: Placement lookup grid pitch, cm. Must exceed the largest reach a placement can have
#: or the 3x3 neighbourhood scan misses it -- the Nuclear Power Plant is 101 m long, so
#: its half-footprint-plus-port reach is ~53 m and the pitch sits above that.
_CELL = 6400.0


def build_runs(projection: dict, game, pipe_flow: list[dict] | None = None) -> list[Run]:
    """Every conduit run in a projection, belts grouped by chain, pipes one per piece.

    ``pipe_flow`` is ``WorldState.pipe_flow`` -- positional over the raw pipe table --
    and orients a pipe's ends by flow where the network resolves one. Omitted, every
    pipe is undirected, which is a lost arrow and nothing else.
    """
    placements = _placements(projection, game)
    cells: dict[tuple[int, int], list[int]] = {}
    for i, p in enumerate(placements):
        cells.setdefault((int(p[0] // _CELL), int(p[1] // _CELL)), []).append(i)

    runs: list[Run] = []

    by_chain: dict[int, list] = {}
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
            Run(
                kind=kind,
                ident=f"chain:{chain}",
                label=_mk_label(kind, classes, game),
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

    networks = list((projection.get("pipes") or {}).get("networks") or ())
    graph = projection.get("graph") or {}
    actors = graph.get("actors") or []
    roles = graph.get("roles") or []

    def _role(index: int) -> str:
        return roles[index] if isinstance(index, int) and 0 <= index < len(roles) else ""

    adjacency: dict[int, set[int]] = {}
    for edge in graph.get("material") or ():
        if isinstance(edge, (list, tuple)) and len(edge) >= 2:
            if len(edge) >= 4 and ports.is_hypertube_edge(_role(edge[2]), _role(edge[3])):
                continue
            try:
                ai, bi = int(edge[0]), int(edge[1])
            except (TypeError, ValueError):
                continue
            adjacency.setdefault(ai, set()).add(bi)
            adjacency.setdefault(bi, set()).add(ai)
    # Classes that are themselves conduit geometry: a pipe's graph neighbour of one of
    # these is plumbing continuing, not a thing the run connects TO.
    internal = set((projection.get("pipes") or {}).get("classes") or ()) | set(
        (projection.get("belts") or {}).get("classes") or ()
    )

    for seg in saverows.iter_pipe_segments(projection):
        entry = networks[seg.network_index] if 0 <= seg.network_index < len(networks) else {}
        fluid = entry.get("fluid") if isinstance(entry, dict) else None
        flow = (
            pipe_flow[seg.index]
            if pipe_flow
            and 0 <= seg.index < len(pipe_flow)
            and isinstance(pipe_flow[seg.index], dict)
            else {}
        )
        direction = flow.get("direction", "unknown")
        points = seg.points if direction != "reverse" else list(reversed(seg.points))
        a, b = End(*points[0][:3]), End(*points[-1][:3])
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
            Run(
                kind="pipe",
                ident=f"pipe:{seg.index}",
                label=_mk_label("pipe", {seg.cls} if seg.cls else set(), game),
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

    for run in runs:
        run.a.plugs = _plug(run.a, placements, cells)
        run.b.plugs = _plug(run.b, placements, cells)
        # A graph neighbour already named at an end is not also "via" it.
        run.via = [v for v in run.via if v not in (run.a.plugs, run.b.plugs)]

    # An end still unplugged may simply continue into the next piece of plumbing --
    # pipes are one run per PIECE, so a mid-network joint is the ordinary case, not an
    # unknown. Named by the neighbouring run's ident, so a route can be followed piece
    # to piece instead of dead-ending at every joint.
    joint_cell = JOINT_M * geo.CM_PER_M  # the grid is keyed in the coordinates' own cm

    def _joint_key(end: End) -> tuple[int, int, int]:
        return (int(end.x // joint_cell), int(end.y // joint_cell), int(end.z // joint_cell))

    joints: dict[tuple[int, int, int], list[End]] = {}
    owner: dict[int, Run] = {}
    for run in runs:
        for end in (run.a, run.b):
            joints.setdefault(_joint_key(end), []).append(end)
            owner[id(end)] = run
    for run in runs:
        for end in (run.a, run.b):
            if end.plugs is not None:
                continue
            kx, ky, kz = _joint_key(end)
            best, best_d = None, JOINT_M
            for dx in (-1, 0, 1):
                for dy in (-1, 0, 1):
                    for dz in (-1, 0, 1):
                        for other in joints.get((kx + dx, ky + dy, kz + dz), ()):
                            if owner[id(other)] is run:
                                continue
                            d = geo.distance_3d_m(
                                (end.x, end.y, end.z), (other.x, other.y, other.z)
                            )
                            if d <= best_d:
                                best, best_d = owner[id(other)].ident, d
            if best is not None:
                end.plugs = best
    return runs


def near_counts(runs: list[Run], x: float, y: float, radius_m: float) -> dict[str, int]:
    """How many runs pass within ``radius_m`` of a point (cm in, metres for the radius
    like every caller-facing distance). Keys are ``belt`` (lifts counted in) and
    ``pipe``; both are ALWAYS present, because the zero is the answer this exists to
    make trustworthy."""
    out = {"belt": 0, "pipe": 0}
    for run in runs:
        if run.dist_m(x, y) <= radius_m:
            out["pipe" if run.kind == "pipe" else "belt"] += 1
    return out


KINDS = ("belt", "pipe")


@dataclass
class ConduitSearch:
    """The runs near one place, or between two, as ``search_conduits`` and the page list them."""

    origin: tuple[float, float] | None = None
    where: str = ""
    second: tuple[float, float] | None = None
    where_to: str = ""
    radius_m: float = 0.0
    to_radius_m: float | None = None
    kind: str | None = None
    network: int | None = None
    run: str | None = None
    hits: list[Run] = field(default_factory=list)
    bridged: list[str] = field(default_factory=list)
    error: str | None = None

    @property
    def belts(self) -> list[Run]:
        return [r for r in self.hits if r.kind != "pipe"]

    @property
    def pipes(self) -> list[Run]:
        return [r for r in self.hits if r.kind == "pipe"]


def search(
    st,
    near: str,
    radius_m: float = 250.0,
    to: str | None = None,
    to_radius_m: float | None = None,
    kind: str | None = None,
    network: int | None = None,
    run: str | None = None,
) -> ConduitSearch:
    """Runs within ``radius_m`` of ``near`` (and of ``to``), longest first.

    ``kind`` is ``belt``, ``pipe`` or ``None`` for both. ``network`` lists every pipe of one
    fluid network and ``run`` one run by its ident; both ignore the radii, and distance is
    still measured from ``near``.
    """
    out = ConduitSearch(radius_m=radius_m, kind=kind, network=network, run=run)
    try:
        out.origin, out.where = resolve_origin(st, near)
        if to is not None:
            out.second, out.where_to = resolve_origin(st, to)
    except ValueError as exc:
        out.error = f"! {exc}"
        return out
    if out.second is not None:
        out.to_radius_m = to_radius_m if to_radius_m is not None else radius_m
    runs = st.conduit_runs
    if run is not None:
        want = run.strip().casefold()
        out.hits = [r for r in runs if r.ident.casefold() == want]
        if not out.hits:
            out.error = f"! no conduit run called {run!r}; search_conduits lists the ids it takes"
        return out
    if network is not None:
        out.hits = sorted(
            (r for r in runs if r.kind == "pipe" and r.network == network),
            key=lambda r: -r.length_m,
        )
        return out

    bridged: dict[int, dict] = {}
    direct_nets: set[int] = set()
    for r in runs:
        if kind is not None and (r.kind == "pipe") != (kind == "pipe"):
            continue
        near_a = r.dist_m(*out.origin) <= radius_m
        if out.second is None:
            if near_a:
                out.hits.append(r)
            continue
        near_b = r.dist_m(*out.second) <= out.to_radius_m
        if near_a and near_b:
            out.hits.append(r)
            if r.network is not None:
                direct_nets.add(r.network)
        elif r.network is not None and (near_a or near_b):
            entry = bridged.setdefault(r.network, {"fluid": r.fluid, "a": 0, "b": 0})
            entry["a"] += near_a
            entry["b"] += near_b
    out.hits.sort(key=lambda r: -r.length_m)

    g = st.game
    out.bridged = [
        f"pipe network {net} ({g.item_name(entry['fluid']) if entry['fluid'] else '?'}) "
        f"touches BOTH areas -- one connected plumbing system, {entry['a']} piece(s) near "
        f"{out.where} and {entry['b']} near {out.where_to}, though no single piece spans both"
        for net, entry in sorted(bridged.items())
        if entry["a"] and entry["b"] and net not in direct_nets
    ]
    if out.second is not None and kind != "pipe" and (out.hits or out.bridged):
        out.bridged.append(
            "a belt route through a splitter is several chains, so a chain near only one "
            "end may still continue to the other -- follow its connects column, or "
            "trace_upstream from the machine it feeds"
        )
    return out


@dataclass
class NetworkView:
    """One fluid network: what it carries, how much pipe, where, and what it ends on."""

    network: int | None
    fluid: str | None
    runs: list[Run]
    length_m: float
    centre: tuple[float, float]
    z_min_m: float
    z_max_m: float
    distance_m: float
    touches: list[str]

    @property
    def pieces(self) -> int:
        return len(self.runs)


def networks(st, origin: tuple[float, float]) -> list[NetworkView]:
    """Every fluid network in the world, most pipe first, placed relative to ``origin``."""
    grouped: dict[object, list[Run]] = {}
    for r in st.conduit_runs:
        if r.kind == "pipe":
            grouped.setdefault(r.network, []).append(r)
    out = []
    for net, runs in sorted(grouped.items(), key=lambda kv: -sum(r.length_m for r in kv[1])):
        ends = [e for r in runs for e in (r.a, r.b)]
        touches: list[str] = []
        for r in runs:
            for name in (r.a.plugs, r.b.plugs, *r.via):
                if name and not name.startswith(("pipe:", "chain:")) and name not in touches:
                    touches.append(name)
        out.append(
            NetworkView(
                network=net,
                fluid=next((r.fluid for r in runs if r.fluid), None),
                runs=runs,
                length_m=sum(r.length_m for r in runs),
                centre=(sum(e.x for e in ends) / len(ends), sum(e.y for e in ends) / len(ends)),
                z_min_m=min(r.z_min_m for r in runs),
                z_max_m=max(r.z_max_m for r in runs),
                distance_m=min(r.dist_m(*origin) for r in runs),
                touches=touches,
            )
        )
    return out

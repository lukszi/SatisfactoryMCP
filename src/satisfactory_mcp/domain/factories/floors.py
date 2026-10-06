"""Floors: the storeys of a factory, recovered from geometry rather than read off the save.

Nothing in a ``.sav`` says "floor". What it does say is where every foundation piece sits,
and players build storeys at discrete repeated heights, so a floor is recoverable from
geometry alone. The unit is a PLATFORM -- a 4-connected flood fill of occupied 8 m cells --
and pointedly not a ``structure.py`` slab, which welds foundations through ramps and so
spans 287 m of Z where a ramp chain climbs a tower; slabs and the player's labels are still
read, but only to NAME the result. Within a platform the top surfaces cluster by single
linkage at ``CLUSTER_TOL_CM`` with no assumed storey pitch, because this world's module is
12 m mixed with 1-2 m half-steps that have to stay separate bands, and every band carries
its own cell area so a six-cell mezzanine reads as minor rather than as a storey. The
premise holds empirically: 99.87% of foundation pieces on platforms of 20 cells or more sit
within ``BAND_EPS_CM`` of a detected band.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass, field

from ...core.saveio import rows as saverows
from .select import resolve_factory

__all__ = [
    "BAND_EPS_CM",
    "BELT_HEIGHT_CM",
    "CELL_CM",
    "CLUSTER_TOL_CM",
    "DECK_SLACK_CM",
    "EXEMPT_NATIVES",
    "GROUPS",
    "LIFT_NATIVE",
    "MEMBERSHIPS",
    "MINOR_SHARE",
    "MIN_BAND_PIECES",
    "RISER_CM",
    "RUN_SLACK_CM",
    "TERRAIN_TOL_M",
    "TOO_OLD_NOTE",
    "Band",
    "Deck",
    "FloorReport",
    "Placement",
    "Platform",
    "Run",
    "floor_decomposition",
]

#: One foundation tile, centimetres. The grid every platform is flood-filled over.
CELL_CM = 800.0

#: Single-linkage gap, centimetres: tops further apart than this start a new band. Anything
#: from 10 to 50 gives the identical decomposition; only past 100 does the count collapse.
CLUSTER_TOL_CM = 50.0

#: How close a piece has to be to a band's level to be one of its members, centimetres. The
#: bands are exact, so 5 and 50 report the same pieces as banded.
BAND_EPS_CM = 25.0

#: Pieces before a cluster is a band. Below three every stray piece becomes its own storey;
#: above three, real half-steps start disappearing.
MIN_BAND_PIECES = 3

#: How far ABOVE a thing its deck may be found, centimetres. A production building's pivot
#: is its base and its base is the deck top, so this is float slop and nothing else: at 0
#: dozens of buildings fall out to the orphan group, and everything from 5 to 450 agrees.
DECK_SLACK_CM = 25.0

#: The same slack for a belt or pipe endpoint, which sits a metre up rather than on the
#: deck. The top of this range is NOT free: widen it towards 450 and a deck several storeys
#: above the run starts winning, which flatters every same-deck statistic. 50 cm absorbs a
#: run stepping over a kerb and nothing more.
RUN_SLACK_CM = 50.0

#: Belt centre-line height above the deck, centimetres. The median attachment sits at
#: +100.2 cm, and the legal set of endpoint heights is 100/300/500.
BELT_HEIGHT_CM = 100.0

#: A chain that climbs this far is a floor connector. Below it, a lift is a belt-height jog
#: -- 24% of lift chains are, which is why "a lift joins two floors" is not a usable rule.
RISER_CM = 600.0

#: How close to the extracted heightfield a no-band thing has to be to be called "on
#: terrain", metres. 89% of orphans are inside it; 4% of band-assigned things are.
TERRAIN_TOL_M = 2.0

#: A band holding less than this share of its platform's largest band's cells is minor --
#: a mezzanine, a walkway ledge, a machine plinth. Reported, never merged and never dropped.
MINOR_SHARE = 0.25

#: How many cells either side of its own a thing may look for the deck it stands on. One,
#: so a machine on the very edge of a deck still finds it, and no further.
NEIGHBOUR_CELLS = 1

#: What counts as a floor piece, the same two hints ``structure.py`` matches on: lightweight
#: buildables carry no docs entry to resolve a native class from.
FOUNDATION_HINTS = ("Foundation", "Platform")

#: Thickness in centimetres by the size token in the class name: ``8x1`` is an 8 m square
#: 1 m thick.
THICKNESS_CM = {
    "8x1": 100.0,
    "8x2": 200.0,
    "8x4": 400.0,
    "4x1": 100.0,
    "4x2": 200.0,
    "4x4": 400.0,
}

#: What a foundation family with no size token in its name is taken to be. Every family
#: seen so far carries one, so this is a floor under a case that has not happened.
DEFAULT_THICKNESS_CM = 100.0

#: The dump's own native classes for the two things that do not stand on a deck: a miner
#: stands on a resource node and a water extractor stands on water. Natives rather than
#: substrings, because a substring match on an engine id is not a classification.
#: ``FGBuildableResourceExtractor`` covers the miners and the oil pumps alike.
EXEMPT_NATIVES = ("FGBuildableResourceExtractor", "FGBuildableWaterPump")

#: The dump's native class for a conveyor lift, told apart from a belt the same way
#: ``/api/belts`` tells them apart.
LIFT_NATIVE = "FGBuildableConveyorLift"

#: Where a placed thing ended up. ``band`` is the answer; the other three are the honest
#: ways of not having one.
GROUPS = ("band", "exempt", "terrain", "off-deck")

#: How a run relates to the floors. ``same-deck`` is the floor-assignable case, ``connector``
#: joins two decks, ``terrain`` is over no deck at all and ``mixed`` runs from one to the
#: other.
MEMBERSHIPS = ("same-deck", "connector", "terrain", "mixed")

#: What the report says instead of an empty band list when the save cannot carry the data.
TOO_OLD_NOTE = (
    "this save predates lightweight buildables (FGLightweightBuildableSubsystem, U8), so "
    "it records no foundations at all -- floors cannot be recovered from it. This is not a "
    "world without floors."
)

#: And when the subsystem is there but nothing has been poured yet.
NO_FOUNDATIONS_NOTE = (
    "this save records lightweight buildables but no foundations -- nothing has been built "
    "on a deck here, so there are no floors to recover"
)


# ------------------------------------------------------------------ the pieces


@dataclass(frozen=True)
class Deck:
    """One band, identified. Two decks are the same floor when both fields agree."""

    platform: int
    ordinal: int
    top_cm: float

    @property
    def key(self) -> tuple[int, int]:
        return (self.platform, self.ordinal)


@dataclass
class Band:
    """One floor of one platform: a level, its extent, and what stands on it."""

    ordinal: int
    top_cm: float
    low_cm: float
    high_cm: float
    pieces: int
    cells: int
    #: Share of the platform's largest band's cell count. Below ``MINOR_SHARE`` this band is
    #: a mezzanine rather than a storey, and a reader is told so rather than shown a
    #: six-cell ledge in the same voice as a 218-cell deck.
    share: float = 1.0
    machines: list[str] = field(default_factory=list)
    attachments: list[str] = field(default_factory=list)
    #: Which pieces of the ``structures`` table this deck is made of, by POSITION in it.
    #: That is the only name a lightweight buildable has; ``foundation_tops`` says why.
    rows: list[int] = field(default_factory=list)

    @property
    def minor(self) -> bool:
        return self.share < MINOR_SHARE

    @property
    def area_m2(self) -> float:
        return self.cells * (CELL_CM / 100.0) ** 2

    @property
    def span_cm(self) -> float:
        """How much of the band's own level is spread, rather than how tall the storey is."""
        return self.high_cm - self.low_cm


@dataclass
class Platform:
    """One 4-connected run of foundation cells, and the bands its tops fall into."""

    index: int
    cells: int
    pieces: int
    centre_cm: tuple[float, float]
    extent_cm: tuple[float, float]
    #: The 8 m cells themselves, kept because narrowing to one platform is a question about
    #: its footprint and answering it any other way means flood-filling twice.
    cell_set: set[tuple[int, int]] = field(default_factory=set, repr=False)
    bands: list[Band] = field(default_factory=list)
    #: Fraction of this platform's pieces within ``BAND_EPS_CM`` of one of its own bands --
    #: the premise of the whole feature, per platform rather than averaged away.
    clean: float = 1.0
    #: Naming only, from the player's own factory label and the ``structure.py`` slab this
    #: platform's machines belong to. Neither takes part in the decomposition.
    label: str | None = None
    slab: int | None = None

    @property
    def area_m2(self) -> float:
        return self.cells * (CELL_CM / 100.0) ** 2


@dataclass
class Placement:
    """One machine, extractor, generator or belt attachment, and the floor it is on."""

    instance: str
    cls: str
    kind: str
    pos_cm: tuple[float, float, float]
    group: str
    deck: Deck | None = None
    #: How far above its deck top it sits: ``0`` for a production building, whose pivot is
    #: its base, and ``100`` for a belt attachment. Reported as data rather than asserted.
    offset_cm: float | None = None
    #: How far above the extracted terrain it sits, where a field was offered.
    above_terrain_m: float | None = None


@dataclass
class Run:
    """One belt chain or one pipe, and how it relates to the floors it passes over.

    ``key`` is the join a client already has: the ``chain`` index that ``/api/belts`` puts
    on every piece, and for a pipe the position in ``pipes["segments"]`` -- the same
    positional join ``domain.world.flow`` promises.
    """

    kind: str
    key: int
    membership: str
    rise_cm: float
    pieces: int
    lift: bool
    ends: tuple[Deck | None, Deck | None] = (None, None)
    #: The 8 m cells the two ends sit over. Kept for narrowing, which is a question about
    #: footprint rather than about membership -- a terrain pipe running UNDER the deck
    #: being asked about belongs in that answer and has no deck to be found by.
    end_cells: tuple[tuple[int, int], ...] = ()

    @property
    def riser(self) -> bool:
        """Tall enough that it can only be a floor connector."""
        return self.rise_cm >= RISER_CM

    @property
    def decks(self) -> list[Deck]:
        return [d for d in self.ends if d is not None]


@dataclass
class FloorReport:
    """Everything one decomposition found. Empty with a ``note`` when it found nothing."""

    platforms: list[Platform] = field(default_factory=list)
    placements: list[Placement] = field(default_factory=list)
    runs: list[Run] = field(default_factory=list)
    #: Belt chains that rise ``RISER_CM`` or more and still land both ends on one band.
    #: Empty on every save tested; ``_violations`` says what an entry means.
    violations: list[Run] = field(default_factory=list)
    #: Why there is nothing to report, when there is nothing to report.
    note: str | None = None
    #: Whether a terrain field was offered. Without one, ``terrain`` is not a group anybody
    #: can be put in, and that has to be visible rather than inferred from an empty list.
    terrain_measured: bool = False
    #: What was asked for, when the answer is a slice of the world rather than all of it.
    selection: str | None = None

    @property
    def bands(self) -> int:
        return sum(len(p.bands) for p in self.platforms)

    def group(self, name: str) -> list[Placement]:
        return [p for p in self.placements if p.group == name]

    def runs_of(self, membership: str) -> list[Run]:
        return [r for r in self.runs if r.membership == membership]

    @property
    def connectors(self) -> list[Run]:
        return self.runs_of("connector")

    def counts(self) -> dict:
        """The shape of the answer before the rows.

        Nested rather than flat because ``terrain`` is both a placement group and a run
        membership, and a machine standing on the ground is a different tally from a pipe
        running along it.
        """
        return {
            "platforms": len(self.platforms),
            "bands": self.bands,
            "runs": len(self.runs),
            "violations": len(self.violations),
            "placements": {name: len(self.group(name)) for name in GROUPS},
            "membership": {name: len(self.runs_of(name)) for name in MEMBERSHIPS},
        }


# ------------------------------------------------------------------ the maths


def thickness_cm(cls: str) -> float:
    """A foundation's thickness, read out of its class name."""
    for token, thickness in THICKNESS_CM.items():
        if token in cls:
            return thickness
    return DEFAULT_THICKNESS_CM


def cell_of(x: float, y: float) -> tuple[int, int]:
    return (int(x // CELL_CM), int(y // CELL_CM))


def _flood(cells: set[tuple[int, int]]) -> list[set[tuple[int, int]]]:
    """4-connected components of occupied cells, largest first.

    Four rather than eight: two platforms that touch only at a corner are two platforms,
    and eight-connectivity roughly halves the platform count by joining things a player
    never joined.
    """
    seen: set[tuple[int, int]] = set()
    groups: list[set[tuple[int, int]]] = []
    for start in sorted(cells):
        if start in seen:
            continue
        stack, component = [start], set()
        seen.add(start)
        while stack:
            cx, cy = stack.pop()
            component.add((cx, cy))
            for nx, ny in ((cx + 1, cy), (cx - 1, cy), (cx, cy + 1), (cx, cy - 1)):
                if (nx, ny) in cells and (nx, ny) not in seen:
                    seen.add((nx, ny))
                    stack.append((nx, ny))
        groups.append(component)
    # Deterministic beyond the size, because ``platform=N`` is an argument a caller keeps.
    groups.sort(key=lambda comp: (-len(comp), min(comp)))
    return groups


def _single_linkage(values: list[float], tol: float) -> list[list[float]]:
    """Split a sorted list wherever a consecutive gap exceeds ``tol``."""
    if not values:
        return []
    ordered = sorted(values)
    clusters: list[list[float]] = []
    current = [ordered[0]]
    for value in ordered[1:]:
        if value - current[-1] <= tol:
            current.append(value)
        else:
            clusters.append(current)
            current = [value]
    clusters.append(current)
    return clusters


def foundation_tops(projection: dict) -> list[tuple[int, float, float, float, str]]:
    """``(row, x, y, top, class)`` for every foundation piece, centimetres.

    ``top`` is the surface a machine stands on: ``z + thickness/2``, because a lightweight's
    stored Z is its vertical CENTRE. Reading ``z`` as the deck surface puts every band half
    a metre low and every machine half a metre in the air.

    ``row`` is the piece's position in the decoded ``structures`` table, which is the only
    name a lightweight buildable has -- the subsystem stores no instance ids. Every reader
    walks the same ``saveio.rows`` iterator in the same order, which is what makes the
    position a join rather than a coincidence, and counting over what the iterator YIELDS
    drops a malformed row from both sides of that join at once.

    ``cls`` is ``""`` rather than ``None`` for an unresolvable class index: what follows
    asks whether a hint is a substring of it, and an unnamed piece is not a foundation.
    """
    out: list[tuple[int, float, float, float, str]] = []
    for row, piece in enumerate(saverows.iter_structures(projection)):
        cls = piece.cls or ""
        if not any(hint in cls for hint in FOUNDATION_HINTS):
            continue
        out.append((row, piece.x, piece.y, piece.z + thickness_cm(cls) / 2.0, cls))
    return out


def _platforms(tops: list[tuple[int, float, float, float, str]]) -> tuple[list[Platform], dict]:
    """Flood-fill the tops into platforms and cluster each platform's own levels.

    Returns the platforms and the cell index every assignment goes through: an 8 m cell to
    the decks that actually have a foundation in it, low to high. Per cell rather than per
    platform bounding box, so a machine standing over a HOLE in a deck is not assigned to a
    floor that is not under it.
    """
    by_cell: dict[tuple[int, int], list[int]] = defaultdict(list)
    for i, (_row, x, y, _top, _cls) in enumerate(tops):
        by_cell[cell_of(x, y)].append(i)

    platforms: list[Platform] = []
    index: dict[tuple[int, int], list[Deck]] = defaultdict(list)
    for order, component in enumerate(_flood(set(by_cell))):
        members = [i for cell in sorted(component) for i in by_cell[cell]]
        levels = [tops[i][3] for i in members]
        bands: list[Band] = []
        for cluster in _single_linkage(levels, CLUSTER_TOL_CM):
            if len(cluster) < MIN_BAND_PIECES:
                continue
            # The most common exact top, not the mean: a band has its own level and a mean
            # would smear it by however many pieces happen to sit at the edge of the eps.
            level = Counter(cluster).most_common(1)[0][0]
            inside = [i for i in members if abs(tops[i][3] - level) <= BAND_EPS_CM]
            cells = {cell_of(tops[i][1], tops[i][2]) for i in inside}
            bands.append(
                Band(
                    ordinal=0,
                    top_cm=level,
                    low_cm=min(cluster),
                    high_cm=max(cluster),
                    pieces=len(cluster),
                    cells=len(cells),
                    # Sorted, so the list is the same list on two runs over one save and a
                    # client can binary-search it rather than build a set from it.
                    rows=sorted(tops[i][0] for i in inside),
                )
            )
        bands.sort(key=lambda b: b.top_cm)
        widest = max((b.cells for b in bands), default=0)
        for ordinal, band in enumerate(bands):
            band.ordinal = ordinal
            band.share = band.cells / widest if widest else 1.0

        levels_only = [b.top_cm for b in bands]
        banded = sum(
            1
            for z in levels
            if min((abs(z - level) for level in levels_only), default=1e9) <= BAND_EPS_CM
        )
        xs = [tops[i][1] for i in members]
        ys = [tops[i][2] for i in members]
        platforms.append(
            Platform(
                index=order,
                cells=len(component),
                pieces=len(members),
                centre_cm=(sum(xs) / len(xs), sum(ys) / len(ys)),
                extent_cm=(max(xs) - min(xs) + CELL_CM, max(ys) - min(ys) + CELL_CM),
                cell_set=set(component),
                bands=bands,
                clean=banded / len(levels) if levels else 1.0,
            )
        )
        for band in bands:
            for i in members:
                if abs(tops[i][3] - band.top_cm) <= BAND_EPS_CM:
                    index[cell_of(tops[i][1], tops[i][2])].append(
                        Deck(platform=order, ordinal=band.ordinal, top_cm=band.top_cm)
                    )
    return platforms, {
        cell: sorted(set(decks), key=lambda d: d.top_cm) for cell, decks in index.items()
    }


def _deck_under(index: dict, x: float, y: float, z: float, slack: float) -> Deck | None:
    """The highest band top at or just above ``z``, in this cell or one next to it."""
    cx, cy = cell_of(x, y)
    best: Deck | None = None
    for dx in range(-NEIGHBOUR_CELLS, NEIGHBOUR_CELLS + 1):
        for dy in range(-NEIGHBOUR_CELLS, NEIGHBOUR_CELLS + 1):
            for deck in index.get((cx + dx, cy + dy), ()):
                if deck.top_cm <= z + slack and (best is None or deck.top_cm > best.top_cm):
                    best = deck
    return best


# ------------------------------------------------------------- what stands where


def _is_exempt(game, cls: str) -> bool:
    """True for the two families that stand on a node or on water rather than on a deck.

    ``False`` for a class the dump has no entry for, which is the same refusal
    ``/api/belts`` makes about a lift: an exemption guessed from an engine id would take a
    machine out of its floor on the strength of a substring.
    """
    building = game.buildings.get(cls) if game is not None else None
    return building is not None and building.native in EXEMPT_NATIVES


def _records(projection: dict):
    """Every placed thing the floors have an opinion about, with which list it came from."""
    for kind in ("machines", "extractors", "generators", "attachments"):
        for record in projection.get(kind, ()) or ():
            if not isinstance(record, dict):
                continue
            pos = record.get("pos")
            if not pos or len(pos) < 3:
                continue
            try:
                point = (float(pos[0]), float(pos[1]), float(pos[2]))
            except (TypeError, ValueError):
                continue
            yield kind, record.get("cls") or "", str(record.get("instance") or ""), point


def _assign(projection, game, index, platforms, terrain_field) -> list[Placement]:
    """Put every placed thing on a band, or say which of the three other things it is."""
    by_key = {(p.index, b.ordinal): b for p in platforms for b in p.bands}
    out: list[Placement] = []
    for kind, cls, instance, point in _records(projection):
        leaf = instance.rsplit(".", 1)[-1]
        if _is_exempt(game, cls):
            out.append(Placement(instance=leaf, cls=cls, kind=kind, pos_cm=point, group="exempt"))
            continue
        slack = RUN_SLACK_CM if kind == "attachments" else DECK_SLACK_CM
        deck = _deck_under(index, point[0], point[1], point[2], slack)
        if deck is not None:
            band = by_key.get(deck.key)
            if band is not None:
                (band.attachments if kind == "attachments" else band.machines).append(leaf)
            out.append(
                Placement(
                    instance=leaf,
                    cls=cls,
                    kind=kind,
                    pos_cm=point,
                    group="band",
                    deck=deck,
                    offset_cm=point[2] - deck.top_cm,
                )
            )
            continue
        above: float | None = None
        if terrain_field is not None:
            reading = terrain_field.at(point[0], point[1])
            if reading is not None:
                above = point[2] / 100.0 - reading.z_m
        group = "terrain" if above is not None and abs(above) <= TERRAIN_TOL_M else "off-deck"
        out.append(
            Placement(
                instance=leaf,
                cls=cls,
                kind=kind,
                pos_cm=point,
                group=group,
                above_terrain_m=above,
            )
        )
    return out


# ------------------------------------------------------------------- the runs


def belt_runs(projection: dict, game=None) -> list[tuple[int, bool, int, list]]:
    """``(chain, is_lift, pieces, points)`` per belt CHAIN, in travel order.

    The grouping that has to happen before any vertical reasoning: consecutive pieces of a
    chain join at a median 0.00 cm, so the chain is the run and a piece is a fragment of one.
    """
    # Resolved once per belt CLASS rather than once per piece, which is what the interned
    # class index is for: thousands of pieces share a handful of classes.
    lift_of: dict[int, bool] = {}
    chains: dict[int, list[tuple[int, list]]] = defaultdict(list)
    for segment in saverows.iter_belt_segments(projection):
        if segment.class_index not in lift_of:
            building = game.buildings.get(segment.cls) if game is not None else None
            lift_of[segment.class_index] = building is not None and building.native == LIFT_NATIVE
        chains[segment.chain].append((segment.class_index, segment.points))
    out = []
    for chain in sorted(chains):
        pieces = chains[chain]
        points = [p for _index, part in pieces for p in part]
        out.append((chain, any(lift_of[index] for index, _ in pieces), len(pieces), points))
    return out


def pipe_runs(projection: dict) -> list[tuple[int, list]]:
    """``(index, points)`` per pipe. A pipe's spline IS its run -- there is nothing to join.

    ``index`` is the position in ``pipes["segments"]``, which is the same positional key
    ``domain.world.flow`` hands back and ``/api/pipes`` emits rows in.
    """
    return [(segment.index, segment.points) for segment in saverows.iter_pipe_segments(projection)]


def _classify(index: dict, points: list, slack: float) -> Run:
    """One run's membership, from its two ENDS -- which is what a run is joined by."""
    head_pt = [float(v) for v in points[0][:3]]
    tail_pt = [float(v) for v in points[-1][:3]]
    head = _deck_under(index, head_pt[0], head_pt[1], head_pt[2], slack)
    tail = _deck_under(index, tail_pt[0], tail_pt[1], tail_pt[2], slack)
    if head is None and tail is None:
        membership = "terrain"
    elif head is None or tail is None:
        membership = "mixed"
    else:
        membership = "same-deck" if head.key == tail.key else "connector"
    return Run(
        kind="",
        key=0,
        membership=membership,
        rise_cm=abs(head_pt[2] - tail_pt[2]),
        pieces=1,
        lift=False,
        ends=(head, tail),
        end_cells=(cell_of(head_pt[0], head_pt[1]), cell_of(tail_pt[0], tail_pt[1])),
    )


def _runs(projection, game, index) -> tuple[list[Run], list[Run]]:
    runs: list[Run] = []
    for chain, lift, pieces, points in belt_runs(projection, game):
        run = _classify(index, points, RUN_SLACK_CM)
        run.kind, run.key, run.pieces, run.lift = "belt", chain, pieces, lift
        runs.append(run)
    for order, points in pipe_runs(projection):
        # No belt-height offset: a pipe meets a machine at its port, not a metre up.
        run = _classify(index, points, 0.0)
        run.kind, run.key = "pipe", order
        runs.append(run)
    return runs, _violations(runs)


def _violations(runs: list[Run]) -> list[Run]:
    """Risers that land both ends on one band. No belt chain measured has, so an entry here
    is a symptom of the decomposition drifting rather than of an unusual base.

    **Belts only.** A chain that climbs six metres has climbed to another floor, but a pipe
    is under no such obligation: plumbing loops up over an obstacle and comes back down on
    the same deck, so holding pipes to the rule reports a permanent false positive and
    teaches a reader to ignore this list.
    """
    return [r for r in runs if r.kind == "belt" and r.riser and r.membership == "same-deck"]


# ------------------------------------------------------------------- the naming


def _name_platforms(st, platforms, placements) -> None:
    """Hang the player's own words on a platform, without letting them decide anything.

    A slab is the sharpest signal for what the player calls one factory and the worst
    possible unit of decomposition, so it and the label store supply a name and nothing else.
    """
    try:
        labels = st.labels
        slabs = st.structures.slab_of
    except Exception:  # pragma: no cover - a missing store must not cost the decomposition
        return
    votes: dict[int, Counter] = defaultdict(Counter)
    slab_votes: dict[int, Counter] = defaultdict(Counter)
    for placement in placements:
        if placement.deck is None or placement.kind == "attachments":
            continue
        label = labels.label_for(placement.instance) if labels else None
        if label is not None:
            votes[placement.deck.platform][label.name] += 1
        slab = slabs.get(placement.instance)
        if slab is not None:
            slab_votes[placement.deck.platform][slab] += 1
    for platform in platforms:
        named = votes.get(platform.index)
        if named:
            platform.label = named.most_common(1)[0][0]
        claimed = slab_votes.get(platform.index)
        if claimed:
            platform.slab = claimed.most_common(1)[0][0]


# ------------------------------------------------------------------ the service


def floor_decomposition(
    st,
    *,
    platform: int | None = None,
    label: str | None = None,
    terrain_field=None,
) -> FloorReport:
    """Decompose a world -- or one platform of it -- into floors.

    ``platform`` is the index this module hands out, and it is stable across runs over one
    save. ``label`` is anything ``resolve_factory`` understands and narrows the answer to
    the platforms that factory's machines stand on, raising whatever the selector grammar
    raises rather than deciding how to say "no such factory".

    ``terrain_field`` is offered by the caller or not at all -- a domain answer must not
    depend on whether somebody has run the heightfield generator. With one, a thing on no
    band is measured against the ground and grouped ``terrain``; without one it is
    ``off-deck``, which is the weaker claim and is labelled as the weaker claim.
    """
    projection = getattr(st, "projection", None) or {}
    payload = projection.get("structures")
    if not payload or not (payload.get("instances") or ()):
        return FloorReport(note=TOO_OLD_NOTE)

    tops = foundation_tops(projection)
    if not tops:
        return FloorReport(note=NO_FOUNDATIONS_NOTE)

    platforms, index = _platforms(tops)
    placements = _assign(projection, getattr(st, "game", None), index, platforms, terrain_field)
    runs, violations = _runs(projection, getattr(st, "game", None), index)
    _name_platforms(st, platforms, placements)

    report = FloorReport(
        platforms=platforms,
        placements=placements,
        runs=runs,
        violations=violations,
        terrain_measured=terrain_field is not None,
    )
    if platform is None and label is None:
        return report
    return _narrow(report, st, platform, label)


def _narrow(report: FloorReport, st, platform: int | None, label: str | None) -> FloorReport:
    """Keep only the platforms asked for, and everything standing on their footprint.

    One rule, applied to placements and runs alike: a thing is in the view when an 8 m cell
    it occupies is one of the selected platforms' cells. Footprint rather than deck
    membership, for the reason ``Run.end_cells`` gives.
    """
    wanted: set[int] = set()
    selection: list[str] = []
    if platform is not None:
        wanted.add(platform)
        selection.append(f"platform {platform}")
    if label is not None:
        name, machines = resolve_factory(st, label)
        selection.append(f"factory {name!r}")
        chosen = set(machines)
        wanted |= {
            p.deck.platform
            for p in report.placements
            if p.deck is not None and p.instance in chosen
        }

    keep = [p for p in report.platforms if p.index in wanted]
    cells = {cell for p in keep for cell in p.cell_set}
    runs = [
        r
        for r in report.runs
        if any(deck.platform in wanted for deck in r.decks) or any(c in cells for c in r.end_cells)
    ]
    return FloorReport(
        platforms=keep,
        placements=[p for p in report.placements if cell_of(p.pos_cm[0], p.pos_cm[1]) in cells],
        runs=runs,
        violations=_violations(runs),
        note=None if keep else f"no platform matches {' and '.join(selection)}",
        terrain_measured=report.terrain_measured,
        selection=" and ".join(selection) or None,
    )

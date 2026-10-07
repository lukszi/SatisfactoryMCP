"""Floors: the storeys of a factory, recovered from geometry rather than read off the save.

Nothing in a ``.sav`` says "floor", but players build storeys at discrete repeated heights.
The unit is a PLATFORM, a 4-connected flood fill of occupied 8 m cells, and never a
``structure.py`` slab, which only NAMES the result. Within a platform the foundation tops
cluster by single linkage into bands, with no assumed storey pitch and each band carrying its
own cell area. docs/parked.md §16b has the measurements behind every constant here.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from collections.abc import Iterator, Sequence
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, NamedTuple, TypeAlias

from ...core.gamedata.model import GameData
from ...core.saveio import rows as saverows
from ...core.saveio.records import instance_leaf
from ...core.saveio.schema import BuildableRecord, Projection
from .select import resolve_factory
from .structure import TILE_CM
from .views import FloorCounts

if TYPE_CHECKING:
    from ..spatial.heightfield import Field
    from ..world.state import WorldState

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
    "BeltChain",
    "Deck",
    "FloorReport",
    "FoundationTop",
    "Placement",
    "Platform",
    "Run",
    "floor_decomposition",
]

#: One foundation tile: the grid every platform is flood-filled over.
CELL_CM = TILE_CM

#: Tops further apart than this start a new band; 10 to 50 decompose identically.
CLUSTER_TOL_CM = 50.0

#: How close a piece has to be to a band's level to be one of its members.
BAND_EPS_CM = 25.0

#: Pieces before a cluster is a band: fewer makes strays storeys, more loses half-steps.
MIN_BAND_PIECES = 3

#: How far ABOVE a building its deck may be found: float slop, as its pivot is its base.
DECK_SLACK_CM = 25.0

#: The same slack for a run endpoint; wider lets a deck storeys above win (§16b).
RUN_SLACK_CM = 50.0

#: Belt centre-line height above the deck; the median attachment sits at +100.2 cm.
BELT_HEIGHT_CM = 100.0

#: A chain that climbs this far is a floor connector; below it a lift is a belt-height jog.
RISER_CM = 600.0

#: How close to the heightfield, in metres, a no-band thing is "on terrain".
TERRAIN_TOL_M = 2.0

#: Below this share of its platform's widest band, a band is minor: reported, never merged.
MINOR_SHARE = 0.25

#: Cells either side a thing may look for its deck: one, so an edge machine still finds it.
NEIGHBOUR_CELLS = 1

#: The same two hints ``structure.py`` matches: lightweights carry no native class to read.
FOUNDATION_HINTS = ("Foundation", "Platform")

#: Thickness by the size token in the class name: ``8x1`` is an 8 m square 1 m thick.
THICKNESS_CM = {
    "8x1": 100.0,
    "8x2": 200.0,
    "8x4": 400.0,
    "4x1": 100.0,
    "4x2": 200.0,
    "4x4": 400.0,
}

#: A foundation family with no size token; every family seen so far carries one.
DEFAULT_THICKNESS_CM = 100.0

#: What stands on a node (miners, oil pumps) or on water rather than on a deck, by native.
EXEMPT_NATIVES = ("FGBuildableResourceExtractor", "FGBuildableWaterPump")

#: A conveyor lift, told apart from a belt the way ``/api/belts`` does it.
LIFT_NATIVE = "FGBuildableConveyorLift"

#: Where a placed thing ended up: ``band``, or one of three honest ways of not having one.
GROUPS = ("band", "exempt", "terrain", "off-deck")

#: How a run relates to the floors: on one deck, between two, over none, or from one to none.
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

#: An 8 m grid cell, ``cell_of``'s answer.
Cell: TypeAlias = tuple[int, int]


# ------------------------------------------------------------------ the pieces


class FoundationTop(NamedTuple):
    """One foundation piece by its row in the ``structures`` table, and its top surface."""

    row: int
    x: float
    y: float
    top_cm: float
    cls: str


class BeltChain(NamedTuple):
    """One belt chain, its pieces' points joined in travel order."""

    chain: int
    is_lift: bool
    pieces: int
    points: list[list[float]]


@dataclass(frozen=True)
class Deck:
    """One band, identified. Two decks are the same floor when both fields agree."""

    platform: int
    ordinal: int
    top_cm: float

    @property
    def key(self) -> tuple[int, int]:
        return (self.platform, self.ordinal)


#: Per 8 m cell, the decks with a foundation in it, low to high.
DeckIndex: TypeAlias = dict[Cell, list[Deck]]


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
    machines: list[str] = field(default_factory=list[str])
    attachments: list[str] = field(default_factory=list[str])
    #: Which pieces of the ``structures`` table this deck is made of, by POSITION in it.
    #: That is the only name a lightweight buildable has; ``foundation_tops`` says why.
    rows: list[int] = field(default_factory=list[int])

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
    cell_set: set[Cell] = field(default_factory=set[Cell], repr=False)
    bands: list[Band] = field(default_factory=list[Band])
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
    end_cells: tuple[Cell, ...] = ()

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

    platforms: list[Platform] = field(default_factory=list[Platform])
    placements: list[Placement] = field(default_factory=list[Placement])
    runs: list[Run] = field(default_factory=list[Run])
    #: Belt chains that rise ``RISER_CM`` or more and still land both ends on one band.
    #: Empty on every save tested; ``_violations`` says what an entry means.
    violations: list[Run] = field(default_factory=list[Run])
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

    def counts(self) -> FloorCounts:
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
        stack = [start]
        component: set[Cell] = set()
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


def foundation_tops(projection: Projection) -> list[FoundationTop]:
    """Every foundation piece and the surface a machine stands on, in centimetres.

    ``top_cm`` is ``z + thickness/2``, because a lightweight's stored Z is its vertical
    CENTRE. ``row`` is the piece's position in the decoded ``structures`` table, the only name
    a lightweight has; every reader walks the same ``saveio.rows`` iterator, so the position
    is a join, and a malformed row drops from both sides of it at once. ``cls`` is ``""``
    for an unresolvable class, which is then no foundation.
    """
    out: list[FoundationTop] = []
    for row, piece in enumerate(saverows.iter_structures(projection)):
        cls = piece.cls or ""
        if not any(hint in cls for hint in FOUNDATION_HINTS):
            continue
        out.append(FoundationTop(row, piece.x, piece.y, piece.z + thickness_cm(cls) / 2.0, cls))
    return out


def _tops_by_cell(tops: list[FoundationTop]) -> dict[tuple[int, int], list[int]]:
    by_cell: dict[tuple[int, int], list[int]] = defaultdict(list)
    for i, top in enumerate(tops):
        by_cell[cell_of(top.x, top.y)].append(i)
    return by_cell


def _platform_bands(tops: list[FoundationTop], members: list[int]) -> list[Band]:
    """One platform's tops clustered into bands, low to high, each with its share."""
    levels = [tops[i].top_cm for i in members]
    bands: list[Band] = []
    for cluster in _single_linkage(levels, CLUSTER_TOL_CM):
        if len(cluster) < MIN_BAND_PIECES:
            continue
        # The most common exact top, not the mean, which would smear the band's own level.
        level = Counter(cluster).most_common(1)[0][0]
        inside = [i for i in members if abs(tops[i].top_cm - level) <= BAND_EPS_CM]
        cells = {cell_of(tops[i].x, tops[i].y) for i in inside}
        bands.append(
            Band(
                ordinal=0,
                top_cm=level,
                low_cm=min(cluster),
                high_cm=max(cluster),
                pieces=len(cluster),
                cells=len(cells),
                # Sorted, so two runs over one save give the same list and a client can
                # binary-search it.
                rows=sorted(tops[i].row for i in inside),
            )
        )
    bands.sort(key=lambda b: b.top_cm)
    widest = max((b.cells for b in bands), default=0)
    for ordinal, band in enumerate(bands):
        band.ordinal = ordinal
        band.share = band.cells / widest if widest else 1.0
    return bands


def _bands_of(tops: list[FoundationTop]) -> list[Platform]:
    """Flood-fill the tops into platforms and cluster each platform's own levels."""
    by_cell = _tops_by_cell(tops)
    platforms: list[Platform] = []
    for order, component in enumerate(_flood(set(by_cell))):
        members = [i for cell in sorted(component) for i in by_cell[cell]]
        levels = [tops[i].top_cm for i in members]
        bands = _platform_bands(tops, members)
        band_levels = [b.top_cm for b in bands]
        banded = sum(
            1
            for z in levels
            if min((abs(z - level) for level in band_levels), default=1e9) <= BAND_EPS_CM
        )
        xs = [tops[i].x for i in members]
        ys = [tops[i].y for i in members]
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
    return platforms


def _index_decks(platforms: list[Platform], tops: list[FoundationTop]) -> DeckIndex:
    """An 8 m cell to the decks with a foundation in it, low to high.

    Per cell rather than per platform bounding box, so a machine standing over a HOLE in a
    deck is not assigned to a floor that is not under it.
    """
    by_cell = _tops_by_cell(tops)
    index: DeckIndex = defaultdict(list)
    for platform in platforms:
        members = [i for cell in sorted(platform.cell_set) for i in by_cell[cell]]
        for band in platform.bands:
            deck = Deck(platform=platform.index, ordinal=band.ordinal, top_cm=band.top_cm)
            for i in members:
                if abs(tops[i].top_cm - band.top_cm) <= BAND_EPS_CM:
                    index[cell_of(tops[i].x, tops[i].y)].append(deck)
    return {cell: sorted(set(decks), key=lambda d: d.top_cm) for cell, decks in index.items()}


def _deck_under(index: DeckIndex, x: float, y: float, z: float, slack: float) -> Deck | None:
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


def _is_exempt(game: GameData | None, cls: str) -> bool:
    """True for the two families that stand on a node or on water rather than on a deck.

    ``False`` for a class the dump has no entry for, which is the same refusal
    ``/api/belts`` makes about a lift: an exemption guessed from an engine id would take a
    machine out of its floor on the strength of a substring.
    """
    building = game.buildings.get(cls) if game is not None else None
    return building is not None and building.native in EXEMPT_NATIVES


def _records(projection: Projection) -> Iterator[tuple[str, str, str, tuple[float, float, float]]]:
    """Every placed thing the floors have an opinion about, with which list it came from."""
    for kind in ("machines", "extractors", "generators", "attachments"):
        records: Sequence[BuildableRecord] = projection.get(kind, ()) or ()
        for record in records:
            pos = record.get("pos")
            if not pos or len(pos) < 3:
                continue
            try:
                point = (float(pos[0]), float(pos[1]), float(pos[2]))
            except (TypeError, ValueError):
                continue
            yield kind, record.get("cls") or "", str(record.get("instance") or ""), point


def _assign(
    projection: Projection,
    game: GameData | None,
    index: DeckIndex,
    platforms: list[Platform],
    terrain_field: Field | None,
) -> list[Placement]:
    """Put every placed thing on a band, or say which of the three other things it is."""
    by_key = {(p.index, b.ordinal): b for p in platforms for b in p.bands}
    out: list[Placement] = []
    for kind, cls, instance, point in _records(projection):
        leaf = instance_leaf(instance)
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
            reading = terrain_field.texel_reading(point[0], point[1])
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


def belt_runs(projection: Projection, game: GameData | None = None) -> list[BeltChain]:
    """One ``BeltChain`` per belt CHAIN, in travel order.

    The grouping that has to happen before any vertical reasoning: consecutive pieces of a
    chain join at a median 0.00 cm, so the chain is the run and a piece is a fragment of one.
    """
    # Resolved once per belt CLASS rather than once per piece: thousands share a handful.
    lift_of: dict[int, bool] = {}
    chains: dict[int, list[tuple[int, list[list[float]]]]] = defaultdict(list)
    for segment in saverows.iter_belt_segments(projection):
        if segment.class_index not in lift_of:
            building = game.buildings.get(segment.cls or "") if game is not None else None
            lift_of[segment.class_index] = building is not None and building.native == LIFT_NATIVE
        chains[segment.chain].append((segment.class_index, segment.points))
    out: list[BeltChain] = []
    for chain in sorted(chains):
        pieces = chains[chain]
        points = [p for _index, part in pieces for p in part]
        is_lift = any(lift_of[index] for index, _ in pieces)
        out.append(BeltChain(chain, is_lift, len(pieces), points))
    return out


def pipe_runs(projection: Projection) -> list[tuple[int, list[list[float]]]]:
    """``(index, points)`` per pipe. A pipe's spline IS its run -- there is nothing to join.

    ``index`` is the position in ``pipes["segments"]``, which is the same positional key
    ``domain.world.flow`` hands back and ``/api/pipes`` emits rows in.
    """
    return [
        (segment.position, segment.points) for segment in saverows.iter_pipe_segments(projection)
    ]


def _run_over_decks(
    index: DeckIndex,
    points: list[list[float]],
    slack: float,
    *,
    kind: str,
    key: int,
    pieces: int = 1,
    lift: bool = False,
) -> Run:
    """One run and its membership, from its two ENDS -- which is what a run is joined by."""
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
        kind=kind,
        key=key,
        membership=membership,
        rise_cm=abs(head_pt[2] - tail_pt[2]),
        pieces=pieces,
        lift=lift,
        ends=(head, tail),
        end_cells=(cell_of(head_pt[0], head_pt[1]), cell_of(tail_pt[0], tail_pt[1])),
    )


def _classify_runs(
    projection: Projection, game: GameData | None, index: DeckIndex
) -> tuple[list[Run], list[Run]]:
    """Every belt chain and pipe as a ``Run``, and the risers among them that break the rule."""
    runs: list[Run] = []
    for belt in belt_runs(projection, game):
        runs.append(
            _run_over_decks(
                index,
                belt.points,
                RUN_SLACK_CM,
                kind="belt",
                key=belt.chain,
                pieces=belt.pieces,
                lift=belt.is_lift,
            )
        )
    for order, points in pipe_runs(projection):
        # No belt-height offset: a pipe meets a machine at its port, not a metre up.
        runs.append(_run_over_decks(index, points, 0.0, kind="pipe", key=order))
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


def _name_platforms(st: WorldState, platforms: list[Platform], placements: list[Placement]) -> None:
    """Hang the player's own words on a platform, without letting them decide anything.

    A slab is the sharpest signal for what the player calls one factory and the worst
    possible unit of decomposition, so it and the label store supply a name and nothing else.
    """
    try:
        labels = st.labels
        slabs = st.structures.slab_of
    except Exception:  # pragma: no cover - a missing store must not cost the decomposition
        return
    votes: dict[int, Counter[str]] = defaultdict(Counter)
    slab_votes: dict[int, Counter[int]] = defaultdict(Counter)
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
    st: WorldState,
    *,
    platform: int | None = None,
    label: str | None = None,
    terrain_field: Field | None = None,
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
    projection = st.projection or {}
    payload = projection.get("structures")
    if not payload or not (payload.get("instances") or ()):
        return FloorReport(note=TOO_OLD_NOTE)

    tops = foundation_tops(projection)
    if not tops:
        return FloorReport(note=NO_FOUNDATIONS_NOTE)

    platforms = _bands_of(tops)
    index = _index_decks(platforms, tops)
    placements = _assign(projection, st.game, index, platforms, terrain_field)
    runs, violations = _classify_runs(projection, st.game, index)
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


def _narrow(
    report: FloorReport, st: WorldState, platform: int | None, label: str | None
) -> FloorReport:
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

"""Why a machine is not running, from what the save actually records.

The productivity window is the only measured number in this MCP; uptime says a machine is
stopped, and its buffers say why: a dead node, starved of a named ingredient, or blocked on a
full output, which wins over starved. A missing FLUID then walks the plumbing manual's ladder.
docs/save-projection.md §6.2d and docs/plumbing.md §24.5 hold the rules and their evidence.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field, replace

from ...core.gamedata.constants import STACK_SIZE
from ...core.gamedata.model import GameData
from ...core.saveio import ports
from ...core.saveio.records import actor_class, iter_machine_records
from ..power.report import NO_FUEL, dry_input_classes, dry_inputs
from ..world.logistics import BASIS_UNKNOWN

__all__ = [
    "ACTIONABLE",
    "NO_GENERATOR",
    "NO_SOURCE",
    "NO_WIRE",
    "OK",
    "RUNGS",
    "STATES",
    "Feed",
    "MachineHealth",
    "assess",
]

#: Uptime at or above this counts as running flat out.
SATURATED = 0.999

#: Below this a machine is treated as stopped rather than merely slow.
STOPPED = 0.001

#: A stack this full counts as backed up: a just-unblocked machine sits a few items short.
FULL_FRACTION = 0.95

#: States a machine can be in, worst first. Order is the report order.
STATES = (
    "paused",
    "dead node",
    "no recipe",
    "blocked",
    "starved",
    "stalled",
    "intermittent",
    "saturated",
    "unmonitored",
)

#: States that need no action.
OK = frozenset({"saturated", "unmonitored"})

#: States that need the player to act, in report order; ``blocked`` by decision (§6.2d).
ACTIONABLE = ("dead node", "no recipe", "blocked", "starved", "stalled")

#: What a starved input's supply came to; only NOTHING and UNFED are findings (§6.2d).
NOTHING = "nothing"
UNFED = "unfed"
OPEN = "open"
JOINED = "joined"
FED = "fed"

#: The rung a fluid input's diagnosis stops at (plumbing.md §24.5); none without head lift.
CONNECTION = "connection"
HEAD_LIFT = "head lift"
FLOW_RATE = "flow rate"
UNDETERMINED = ""
RUNGS = (CONNECTION, HEAD_LIFT, FLOW_RATE)

#: Rung (1) as only the head-lift model sees it; ``Feed.rung`` stays ``CONNECTION``.
NO_SOURCE = f"{CONNECTION}: no source on its network"

#: Why nothing can power a machine: two different builds to finish (§6.1a).
NO_WIRE = "no power connection"
NO_GENERATOR = "no generator on its circuit"


@dataclass(frozen=True)
class Feed:
    """One hop back from a missing ingredient: a run that arrives, and what stands on it.

    One of these per arriving run, or a single ``NOTHING`` row where none arrives. What is
    further upstream is `trace`'s question and is deliberately not walked here.
    """

    item: str
    verdict: str
    #: The run, where the projection names one -- see ``logistics.Link.ident``.
    run: str = ""
    medium: str = ""
    pieces: int = 0
    #: The actor at the far end and its building name; empty when the run reaches nothing.
    far: str = ""
    far_name: str = ""
    #: The far end's own state, empty when it is not a thing that keeps a monitor.
    far_state: str = ""
    #: True only where that far end is KNOWN to make this item; false is "not established".
    makes: bool = False
    #: The rung this ingredient's diagnosis stopped at, shared by every row of one item.
    rung: str = UNDETERMINED


@dataclass
class MachineHealth:
    instance: str
    building: str
    recipe: str
    state: str
    uptime: float | None
    #: What is missing (starved) or backed up (blocked), as item names.
    cause: tuple[str, ...] = ()
    clock: float = 1.0
    #: One hop back from each missing ingredient; empty, not "nothing feeds it", without a
    #: physical graph.
    feeds: tuple[Feed, ...] = ()

    @property
    def needs_attention(self) -> bool:
        return self.state not in OK


@dataclass
class HealthReport:
    name: str
    machines: list[MachineHealth] = field(default_factory=list)
    by_state: Counter = field(default_factory=Counter)
    #: item name -> how many machines are blocked on it / starved of it
    blocked_on: Counter = field(default_factory=Counter)
    starved_of: Counter = field(default_factory=Counter)
    #: Machines no generator can reach over the wires: no power edge at all, or wired to a
    #: circuit no generator stands on. Lists rather than states because both cut across all
    #: nine; both empty when no graph was supplied.
    unwired: list[str] = field(default_factory=list)
    no_generator: list[str] = field(default_factory=list)
    #: Generators on no wire: capacity nothing can draw, kept out of ``unwired``.
    unwired_generators: list[str] = field(default_factory=list)

    @property
    def monitored(self) -> list[MachineHealth]:
        return [m for m in self.machines if m.uptime is not None]

    @property
    def mean_uptime(self) -> float | None:
        seen = [m.uptime for m in self.monitored]
        return sum(seen) / len(seen) if seen else None

    def worst(self, limit: int = 10) -> list[MachineHealth]:
        order = {s: i for i, s in enumerate(STATES)}
        return sorted(
            (m for m in self.machines if m.needs_attention),
            key=lambda m: (order.get(m.state, 99), m.uptime if m.uptime is not None else 0.0),
        )[:limit]


def _stack_limit(game: GameData, item_cls: str) -> int:
    item = game.items.get(item_cls)
    return STACK_SIZE.get(getattr(item, "stack_size", ""), 0)


def _input_items(record: dict) -> dict:
    return ((record.get("buffers") or {}).get("in") or {}).get("items") or {}


def _buffer_state(game: GameData, record: dict, recipe) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """Returns (items backed up in the output, ingredients missing from the input).

    Starvation is **a required ingredient at zero**, not an empty input, and an ABSENT intake
    inventory is not an empty one: a miner has none and yields no starvation evidence.
    """
    buffers = record.get("buffers") or {}
    backed: list[str] = []
    out = (buffers.get("out") or {}).get("items") or {}
    for item_cls, count in out.items():
        limit = _stack_limit(game, item_cls)
        if limit and count >= limit * FULL_FRACTION:
            backed.append(game.item_name(item_cls))

    if buffers.get("in") is not None and recipe is not None:
        held = _input_items(record)
        missing = [game.item_name(f.item) for f in recipe.ingredients if not held.get(f.item)]
        return tuple(sorted(backed)), tuple(sorted(missing))

    # No recipe to check against: a generator is starved of whatever its fuel inventory has
    # run out of, which for a coal plant includes the supplemental water.
    return tuple(sorted(backed)), dry_inputs(game, record)


def _medium(game: GameData, item_cls: str) -> str:
    item = game.items.get(item_cls)
    return ports.PIPE if item is not None and item.is_fluid else ports.CONVEYOR


@dataclass(frozen=True)
class _Conduits:
    """A physical graph, and which media it is known to resolve runs on at all.

    A projection can carry the pipes and not the belts: save versions 25 to 36 on one
    reference install resolve 88 pipe runs between coal generators and not one conveyor run
    in a world of 6,266 material couplings. "No run of that medium arrives" is therefore a
    fact only for a medium this graph resolves somewhere, and a blind spot everywhere else.
    """

    graph: object
    media: frozenset

    @classmethod
    def of(cls, physical, actors) -> _Conduits:
        return cls(physical, frozenset(link.medium for a in actors for link in physical.feeds(a)))

    def may_arrive(self, actor: str, medium: str) -> bool:
        """True unless NOTHING of that medium arrives -- the far end is deliberately not read.

        A run whose far end the save joins to no actor is ``OPEN``, a feeder unknown rather
        than a feeder absent, and only ``NOTHING`` is a finding here. Ten FICSMAS
        Constructors on one save turn on the difference.
        """
        if medium not in self.media:
            return True
        return any(link.medium == medium for link in self.graph.feeds(actor))


def _cut_off(leaf: str, record: dict, recipe, game: GameData, conduits, head_lift) -> bool:
    """Whether every required ingredient at zero can reach this machine from nowhere.

    Rung (1) in BOTH its forms, asked of a machine with no productivity window: no run of
    that medium arrives, or a pipe arrives and no source anywhere is on its network. An empty
    buffer alone is weak evidence here, because nothing has ever flowed through it.
    """
    if recipe is None or conduits is None or (record.get("buffers") or {}).get("in") is None:
        return False
    sourceless = head_lift is not None and leaf in head_lift.unfed_ports
    missing = [_medium(game, cls) for cls, _name in _missing_classes(record, recipe, game)]
    return bool(missing) and all(
        not conduits.may_arrive(leaf, medium) or (sourceless and medium == ports.PIPE)
        for medium in missing
    )


def _classify(
    group: str,
    record: dict,
    game: GameData,
    unpowered_reason: str,
    leaf: str,
    conduits,
    head_lift=None,
):
    """One record's ``(state, cause, uptime, recipe)``, on the ladder in the module docstring."""
    recipe = game.recipes.get(record.get("recipe") or "")
    live = record.get("uptime") or {}
    window = live.get("window_s") or 0.0
    # Each record's OWN window, never a hard-coded 300: one save reads 300.00, 300.01 and
    # 300.02. An absent produce_s is a real zero, since UE omits default-valued properties.
    uptime = (live.get("produce_s", 0.0) / window) if window else None
    backed, missing = _buffer_state(game, record, recipe)

    if record.get("paused"):
        state, cause = "paused", ()
    elif group == "extractors" and not record.get("node"):
        # mExtractableResource ABSENT, not merely unresolvable: a water pump's node is set.
        state, cause = "dead node", ("no resource node",)
    elif group == "machines" and not record.get("recipe"):
        state, cause = "no recipe", ()
    elif uptime is None:
        # A machine that has NEVER produced carries no window at all and never will.
        cut = _cut_off(leaf, record, recipe, game, conduits, head_lift)
        state, cause = ("starved", missing) if cut else ("unmonitored", ())
    elif uptime >= SATURATED:
        state, cause = "saturated", ()
    elif uptime > STOPPED:
        state, cause = "intermittent", backed or missing
    elif backed:
        # Checked BEFORE starvation: a blocked machine's input backs up too.
        state, cause = "blocked", backed
    elif missing:
        state, cause = "starved", missing
    elif unpowered_reason:
        # Input, room for output, not running, and no generator reaches it over the wires.
        state, cause = "stalled", (unpowered_reason,)
    else:
        # Input, room for output, still not running: power, or a monitor not caught up.
        state, cause = "stalled", ()
    return state, tuple(cause), uptime, recipe


def _generator_reach(graph, sources: set[str]) -> set[str] | None:
    """Every actor some generator reaches over the power wires, or ``None`` for no sources.

    ``None`` because a projection with no generator at all says something about the save, not
    about any one machine. An OPEN power switch still joins its two sides here, so this
    under-reports rather than over-reports.
    """
    if not sources:
        return None
    seen: set[str] = set()
    stack = list(sources)
    while stack:
        node = stack.pop()
        if node in seen:
            continue
        seen.add(node)
        stack.extend(graph.neighbours(node, "power"))
    return seen


def _makes(record: dict, item_cls: str, game: GameData) -> bool:
    """Whether this record is KNOWN to put ``item_cls`` on a belt or pipe: its recipe's
    products, or for an extractor what sits in its output buffer."""
    recipe = game.recipes.get(record.get("recipe") or "")
    if recipe is not None:
        return any(flow.item == item_cls for flow in recipe.products)
    out = ((record.get("buffers") or {}).get("out") or {}).get("items") or {}
    return item_cls in out


def _rung(leaf: str, item_cls: str, found: list[Feed], head_lift) -> str:
    """Which rung of the manual's ladder one missing FLUID stops at -- plumbing.md §24.5."""
    if all(row.verdict in (NOTHING, OPEN) for row in found):
        return CONNECTION
    if head_lift is None:
        return UNDETERMINED
    if leaf in head_lift.unfed_ports:
        return CONNECTION
    # A crest names its network's fluid, ``None`` where it carries none yet; matching on it
    # keeps a machine's second, working input out of it.
    if any(leaf in c.consumers and c.fluid in (None, item_cls) for c in head_lift.crests):
        return HEAD_LIFT
    return FLOW_RATE


def _feed_rows(
    leaf: str,
    missing_items: list[tuple[str, str]],
    game: GameData,
    physical,
    far_health,
    head_lift,
) -> tuple[Feed, ...]:
    """One hop back from each missing ingredient of one starved machine, and its rung.

    The medium separates the ingredients: the save cannot say which belt was meant to bring
    the Coal, but a solid arrives by conveyor and a fluid by pipe.
    """
    rows: list[Feed] = []
    for item_cls, item_name in missing_items:
        medium = _medium(game, item_cls)
        fluid = medium == ports.PIPE
        sourceless = fluid and head_lift is not None and leaf in head_lift.unfed_ports
        arriving = [link for link in physical.feeds(leaf) if link.medium == medium]
        found: list[Feed] = []
        if not arriving:
            found.append(Feed(item=item_name, verdict=NOTHING, medium=medium))
        for link in arriving:
            far = link.other(leaf)
            if far is None:
                found.append(Feed(item_name, OPEN, link.ident, medium, link.pieces))
                continue
            cls = actor_class(far)
            record, state = far_health(far)
            found.append(
                Feed(
                    item=item_name,
                    verdict=UNFED
                    if sourceless
                    else (JOINED if link.basis == BASIS_UNKNOWN else FED),
                    run=link.ident,
                    medium=medium,
                    pieces=link.pieces,
                    far=far,
                    far_name=game.building_name(cls) or cls,
                    far_state=state,
                    makes=_makes(record, item_cls, game) if record else False,
                )
            )
        rung = _rung(leaf, item_cls, found, head_lift) if fluid else UNDETERMINED
        rows.extend(replace(row, rung=rung) for row in found)
    return tuple(rows)


def _missing_classes(record: dict, recipe, game: GameData) -> list[tuple[str, str]]:
    """What a starved record has run out of, as ``(item class, item name)``: the classes
    `_buffer_state` leaves out, because a feeder has to be looked up by class."""
    if recipe is not None:
        held = _input_items(record)
        return [
            (flow.item, game.item_name(flow.item))
            for flow in recipe.ingredients
            if not held.get(flow.item)
        ]
    return [(cls, game.item_name(cls)) for cls in dry_input_classes(game, record) if cls != NO_FUEL]


def _with_rungs(cause: tuple[str, ...], feeds: tuple[Feed, ...]) -> tuple[str, ...]:
    """The missing items, each fluid one carrying the rung its diagnosis stopped at."""
    rung_of = {feed.item: feed.rung for feed in feeds if feed.rung}
    rung_of.update({feed.item: NO_SOURCE for feed in feeds if feed.verdict == UNFED})
    return tuple(f"{item} ({rung_of[item]})" if item in rung_of else item for item in cause)


def assess(
    name: str,
    machines: list[str],
    game: GameData,
    projection: dict,
    graph=None,
    physical=None,
    head_lift=None,
) -> HealthReport:
    """Classify every machine of ``machines``; extractors and generators too, when monitored.

    ``graph`` (a ``FactoryGraph``) adds the wire facts, ``physical`` (a ``PhysicalGraph``) the
    feed rows of a starved machine, and ``head_lift`` (a ``HeadLift``) the fluid rungs past
    the first. Each is optional, and its absence leaves that answer UNKNOWN, never negative:
    silence must not read as "unpowered", "nothing feeds this" or "flow rate".
    """
    wanted = set(machines)
    report = HealthReport(name=name)

    # Every machine-like record in the world: a starved machine's feeder is routinely outside
    # the factory asked about, and every generator anywhere is a power source.
    records_by_leaf: dict[str, tuple[str, dict]] = {}
    for group, leaf, record in iter_machine_records(projection):
        records_by_leaf[leaf] = (group, record)

    # What the never-run branch of `_classify` may read as "no run arrives".
    conduits = _Conduits.of(physical, records_by_leaf) if physical is not None else None
    sources = {s for s, (group, _r) in records_by_leaf.items() if group == "generators"}
    reached = _generator_reach(graph, sources) if graph is not None else None

    def unpowered_reason(actor: str) -> str:
        """Why nothing can power ``actor``; empty where something can or nothing is known.

        ``build_graph`` seeds every machine record as a node, so an isolated one has no
        edges rather than no entry.
        """
        if graph is None:
            return ""
        if not graph.neighbours(actor, "power"):
            return NO_WIRE
        return NO_GENERATOR if reached is not None and actor not in reached else ""

    def far_health(actor: str) -> tuple[dict | None, str]:
        found = records_by_leaf.get(actor)
        if found is None:
            return None, ""
        group, record = found
        reason = unpowered_reason(actor)
        return record, _classify(group, record, game, reason, actor, conduits, head_lift)[0]

    for leaf, (group, record) in records_by_leaf.items():
        if leaf not in wanted:
            continue
        reason = unpowered_reason(leaf)
        state, cause, uptime, recipe = _classify(
            group, record, game, reason, leaf, conduits, head_lift
        )
        entry = MachineHealth(
            instance=leaf,
            building=record.get("cls", "?"),
            recipe=recipe.name if recipe else "",
            state=state,
            uptime=uptime,
            cause=cause,
            clock=float(record.get("clock") or 1.0),
        )
        if state == "starved" and physical is not None:
            entry.feeds = _feed_rows(
                leaf, _missing_classes(record, recipe, game), game, physical, far_health, head_lift
            )
            entry.cause = _with_rungs(entry.cause, entry.feeds)
        report.machines.append(entry)
        report.by_state[state] += 1
        if reason == NO_WIRE:
            (report.unwired_generators if group == "generators" else report.unwired).append(leaf)
        elif reason == NO_GENERATOR:
            report.no_generator.append(leaf)
        if state == "blocked":
            for item in entry.cause:
                report.blocked_on[item] += 1
        elif state == "starved" and recipe is not None:
            for flow in recipe.ingredients:
                report.starved_of[game.item_name(flow.item)] += 1

    return report

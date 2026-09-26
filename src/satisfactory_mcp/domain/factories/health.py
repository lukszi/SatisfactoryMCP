"""Why a machine is not running, from what the save actually records.

Every buildable that manufactures carries a productivity monitor -- a roughly 300-second
window and the seconds it spent producing inside it -- and that ratio is the only measured
number in this whole MCP; everything else is nameplate. Uptime alone says a machine is
stopped and never why, so the input and output buffers settle it::

    an extractor with no node      -> DEAD NODE, it can never produce
    a required ingredient at zero  -> STARVED of that item, by name
    an output item at a full stack -> BLOCKED, its consumer is not keeping up
    both                           -> BLOCKED wins; a full output stops it regardless

A missing FLUID then walks a second ladder, the plumbing manual's own -- connection, head
lift, flow rate, stopping at the first that fires. It is written down in §24.5.

Two neighbouring save fields look usable and are not. ``mCurrentProductivityMeasurement*``
is a partial window still filling, so mixing it with the last complete one compares a
3-minute sample against a 5-minute one; ``mTimeSinceStartStopProducing`` carries FLT_MAX
on roughly half of all carriers as a "never flipped" sentinel, which is not a duration and
poisons any statistic it enters.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field, replace

from ...core.gamedata.constants import STACK_SIZE
from ...core.gamedata.model import GameData
from ...core.saveio import ports
from ..power.report import NO_FUEL, dry_input_classes, dry_inputs

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
    "summarise",
]

#: Uptime at or above this counts as running flat out.
SATURATED = 0.999

#: Below this a machine is treated as stopped rather than merely slow.
STOPPED = 0.001

#: A stack this full is treated as backed up. Not 100%: a machine that has just been
#: unblocked sits a few items short, and calling that healthy hides a real bottleneck.
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

#: States that need the player to act, in report order: every one but ``OK``, ``paused``
#: and ``intermittent``. ``blocked`` is here by Lukas's decision -- docs/frontend_vision.md §8.6.
ACTIONABLE = ("dead node", "no recipe", "blocked", "starved", "stalled")

#: What a starved input's supply came to. ``NOTHING`` and ``UNFED`` are FINDINGS: no run of
#: that medium reaches the machine, or a pipe does and no source anywhere reaches the network
#: it belongs to. The other three are not; ``OPEN`` means the save joins the far end to no
#: actor at all, which is a feeder unknown rather than a feeder absent.
NOTHING = "nothing"
UNFED = "unfed"
OPEN = "open"
JOINED = "joined"
FED = "fed"

#: The plumbing manual's troubleshooting order, and the rung a FLUID input's diagnosis stops
#: at -- §24.5. ``UNDETERMINED`` is what a caller supplying no head-lift model gets: rung (2)
#: is then unruled-out, and claiming rung (3) over it is the mistake the manual warns about.
#: A solid never carries one, because head lift is not a thing that happens to it.
CONNECTION = "connection"
HEAD_LIFT = "head lift"
FLOW_RATE = "flow rate"
UNDETERMINED = ""
RUNGS = (CONNECTION, HEAD_LIFT, FLOW_RATE)

#: How a cause spells rung (1) in the form only the head-lift model can see. Not a fourth
#: rung -- ``Feed.rung`` stays ``CONNECTION``, which is what ``RUNGS`` counts -- because the
#: fitting is real and the fix is a source rather than a pipe.
NO_SOURCE = f"{CONNECTION}: no source on its network"

#: Why no power can reach a machine, and they are two different builds to finish -- see
#: save-projection.md §6.1a. The fluid twin above is a different subject and the two must
#: not be read for each other: that one is a pipe network no fluid source reaches, this one
#: is a wire network no generator stands on. NOTHING WEAKER IS CLAIMED for either: the wires
#: are the only electrical fact the save carries, so a machine some generator can reach over
#: them is never called unpowered here, however dark it is in the running game.
NO_WIRE = "no power connection"
NO_GENERATOR = "no generator on its circuit"


@dataclass(frozen=True)
class Feed:
    """One hop back from a missing ingredient: a run that arrives, and what stands on it.

    One of these per arriving run, or a single ``NOTHING`` row where none arrives. What is
    further upstream is `..world.trace`'s question and is deliberately not walked here.
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
    #: True only where that far end is KNOWN to make this item; false is "not established",
    #: never "it does not".
    makes: bool = False
    #: Which rung of the ladder this input's diagnosis stopped at. Shared by every row of one
    #: item, since the rung is a property of the ingredient and not of one arriving run.
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
    #: One hop back from each missing ingredient. Empty when no physical graph was supplied
    #: -- see `assess`, where that absence must not read as "nothing feeds it".
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
    #: Machines no generator can reach over the wires, split by which build is unfinished:
    #: ``unwired`` have no power edge at all, ``no_generator`` are wired to a circuit no
    #: generator stands on. Disjoint lists rather than states, because both cut across all
    #: nine -- a machine nothing can power is as often paused, on no recipe or keeping no
    #: monitor, and each of those is still worth saying on its own terms. BOTH EMPTY when no
    #: graph was supplied -- see `assess`, where absent evidence must not read as a finding.
    unwired: list[str] = field(default_factory=list)
    no_generator: list[str] = field(default_factory=list)

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


def _held(record: dict) -> dict:
    return ((record.get("buffers") or {}).get("in") or {}).get("items") or {}


def _buffer_state(game: GameData, record: dict, recipe) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """Returns (items backed up in the output, ingredients missing from the input).

    Starvation is **a required ingredient at zero**, not an empty input: an assembler on
    Black Powder holding 100 Sulfur and no Coal is starved, and naming the missing
    ingredient is the whole value of the report. An ABSENT intake inventory is not an
    empty one either -- a miner draws from its node and has no InputInventory at all, so
    it yields no starvation evidence rather than a false positive.
    """
    buffers = record.get("buffers") or {}
    backed: list[str] = []
    out = (buffers.get("out") or {}).get("items") or {}
    for item_cls, count in out.items():
        limit = _stack_limit(game, item_cls)
        if limit and count >= limit * FULL_FRACTION:
            backed.append(game.item_name(item_cls))

    if buffers.get("in") is not None and recipe is not None:
        held = _held(record)
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

    A projection can carry the pipes and not the belts: save versions 25 to 36 on the author's
    machine resolve 88 pipe runs between coal generators and not one conveyor run in a world
    of 6,266 material couplings. "No run of that medium arrives" is therefore a fact only for
    a medium this graph resolves somewhere, and a blind spot everywhere else.
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


def _cut_off(short: str, record: dict, recipe, game: GameData, conduits, heads) -> bool:
    """Whether every required ingredient at zero can reach this machine from nowhere.

    Rung (1) of the manual's ladder in BOTH its forms, asked of a machine with no
    productivity window: no run of that medium arrives at all, or -- for a fluid -- a pipe
    does arrive and the head-lift model finds no source anywhere on its network, which the
    conduit graph cannot see. An empty buffer alone is weak evidence here, because nothing
    has ever flowed through it.
    """
    if recipe is None or conduits is None or (record.get("buffers") or {}).get("in") is None:
        return False
    sourceless = heads is not None and short in heads.unfed_ports
    missing = [_medium(game, cls) for cls, _name in _missing_classes(record, recipe, game)]
    return bool(missing) and all(
        not conduits.may_arrive(short, medium) or (sourceless and medium == ports.PIPE)
        for medium in missing
    )


def _classify(
    key: str, record: dict, game: GameData, dark: str, short: str, conduits, heads=None
):
    """One record's ``(state, cause, uptime, recipe)``, on the ladder in the module docstring."""
    recipe = game.recipes.get(record.get("recipe") or "")
    live = record.get("uptime") or {}
    window = live.get("window_s") or 0.0
    # Each record's OWN window, never a hard-coded 300: the game closes the window on a tick
    # boundary, so it reads 300.00, 300.01 or 300.02 across one save. An absent produce_s is
    # a real zero, since UE omits properties equal to their default -- a monitored idle
    # machine is 0.0 uptime, not unmonitored.
    uptime = (live.get("produce_s", 0.0) / window) if window else None
    backed, missing = _buffer_state(game, record, recipe)

    if record.get("paused"):
        state, cause = "paused", ()
    elif key == "extractors" and not record.get("node"):
        # mExtractableResource ABSENT, not merely unresolvable: the miner is bound to
        # nothing, which happens when a game update removes a resource node under it. A
        # water pump's node IS set but points at an FGWaterVolume that is not a purity-table
        # key, and that one works fine.
        state, cause = "dead node", ("no resource node",)
    elif key == "machines" and not record.get("recipe"):
        state, cause = "no recipe", ()
    elif uptime is None:
        # A machine that has NEVER produced carries no window at all and never will: this
        # branch is permanent, not a monitor yet to catch up.
        cut = _cut_off(short, record, recipe, game, conduits, heads)
        state, cause = ("starved", missing) if cut else ("unmonitored", ())
    elif uptime >= SATURATED:
        state, cause = "saturated", ()
    elif uptime > STOPPED:
        # Running, but not flat out: the same evidence, reported without calling the machine
        # stopped.
        state, cause = "intermittent", backed or missing
    elif backed:
        # Checked BEFORE starvation: a blocked machine's input backs up too, so testing the
        # input first would misread it as merely well-fed.
        state, cause = "blocked", backed
    elif missing:
        state, cause = "starved", missing
    elif dark:
        # Has input, output has room, not running, and no generator can reach it over the
        # wires. This is the one power fact the save states outright, so "usually power"
        # stops being advice and becomes the cause, in its own words.
        state, cause = "stalled", (dark,)
    else:
        # Has input, output not full, still not running: power, or a monitor that has not
        # caught up.
        state, cause = "stalled", ()
    return state, tuple(cause), uptime, recipe


def _lit(graph, sources: set[str]) -> set[str] | None:
    """Every actor some generator reaches over the power wires, or ``None`` for no sources.

    ``None`` guards a second-order absence. A projection carrying no generator at all leaves
    every actor in the world unreached, and that is a statement about the save -- an early
    world, a hand-built fixture -- rather than a finding about any one machine.

    An OPEN power switch would still join the two sides here, so this under-reports rather
    than over-reports, which is the direction it has to fail in.
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
    """Whether this record is KNOWN to put ``item_cls`` on a belt or pipe.

    Its recipe's products, or -- for an extractor, which runs no recipe -- what is sitting in
    its output buffer. False is "not established" and never "it does not".
    """
    recipe = game.recipes.get(record.get("recipe") or "")
    if recipe is not None:
        return any(flow.item == item_cls for flow in recipe.products)
    out = ((record.get("buffers") or {}).get("out") or {}).get("items") or {}
    return item_cls in out


def _rung(short: str, item_cls: str, found: list[Feed], heads) -> str:
    """Which rung of the manual's ladder one missing FLUID stops at -- §24.5.

    Rung (1) twice over, and they are different facts: no run of that medium arrives at the
    machine at all, or every one that does reaches nothing; and the run arriving from a real
    fitting on a network no source anywhere reaches, which only the head-lift model sees.
    """
    if all(row.verdict in (NOTHING, OPEN) for row in found):
        return CONNECTION
    if heads is None:
        return UNDETERMINED
    if short in heads.unfed_ports:
        return CONNECTION
    # A crest names the fluid of the network it stands on, and ``None`` where that network
    # carries none yet; matching on it keeps a machine's second, working input out of it.
    if any(short in c.consumers and c.fluid in (None, item_cls) for c in heads.crests):
        return HEAD_LIFT
    return FLOW_RATE


def _feed_rows(
    short: str,
    missing_items: list[tuple[str, str]],
    game: GameData,
    physical,
    far_health,
    heads,
) -> tuple[Feed, ...]:
    """One hop back from each missing ingredient of one starved machine, and its rung.

    The medium is what separates the ingredients: a run carries whatever is put on it, so the
    save cannot say which belt was meant to bring the Coal, but it does say that a solid
    arrives by conveyor and a fluid by pipe.
    """
    from ..world.logistics import UNKNOWN

    rows: list[Feed] = []
    for item_cls, item_name in missing_items:
        medium = _medium(game, item_cls)
        fluid = medium == ports.PIPE
        sourceless = fluid and heads is not None and short in heads.unfed_ports
        arriving = [link for link in physical.feeds(short) if link.medium == medium]
        found: list[Feed] = []
        if not arriving:
            found.append(Feed(item=item_name, verdict=NOTHING, medium=medium))
        for link in arriving:
            far = link.other(short)
            if far is None:
                found.append(Feed(item_name, OPEN, link.ident, medium, link.pieces))
                continue
            cls = _class_of(far)
            record, state = far_health(far)
            found.append(
                Feed(
                    item=item_name,
                    verdict=UNFED if sourceless else (JOINED if link.basis == UNKNOWN else FED),
                    run=link.ident,
                    medium=medium,
                    pieces=link.pieces,
                    far=far,
                    far_name=game.building_name(cls) or cls,
                    far_state=state,
                    makes=_makes(record, item_cls, game) if record else False,
                )
            )
        rung = _rung(short, item_cls, found, heads) if fluid else UNDETERMINED
        rows.extend(replace(row, rung=rung) for row in found)
    return tuple(rows)


def _class_of(actor: str) -> str:
    head, _, tail = actor.rpartition("_")
    return head if tail.isdigit() else actor


def _missing_classes(record: dict, recipe, game: GameData) -> list[tuple[str, str]]:
    """What a starved record has run out of, as ``(item class, item name)``.

    `_buffer_state` answers the same question in names alone, which is all the state ladder
    needs; a feeder has to be looked up by class.
    """
    if recipe is not None:
        held = _held(record)
        return [
            (flow.item, game.item_name(flow.item))
            for flow in recipe.ingredients
            if not held.get(flow.item)
        ]
    return [(cls, game.item_name(cls)) for cls in dry_input_classes(game, record) if cls != NO_FUEL]


def _laddered(cause: tuple[str, ...], feeds: tuple[Feed, ...]) -> tuple[str, ...]:
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
    heads=None,
) -> HealthReport:
    """Classify every machine in a set. Extractors and generators are included when they
    carry a monitor, since a starved coal plant is exactly what one wants to see.

    ``graph`` is a ``FactoryGraph`` and is what makes "no generator reaches this" answerable;
    ``physical`` is a ``logistics.PhysicalGraph`` and is what makes "and this is what feeds
    the input it lacks" answerable; ``heads`` is a ``headlift.HeadLift`` and is what lets a
    missing fluid be diagnosed on the manual's ladder rather than at its bottom rung, and is
    the only thing that sees a network no source reaches at all. All three optional, for one
    reason: without them the answer is UNKNOWN rather than negative.
    The save records no ``mHasPower`` and no ``mCircuitID``, so the one positive electrical
    fact it carries is the wire; a caller who supplies no conduit must not have silence read
    as "nothing feeds this"; and without the head-lift model no input is called a flow-rate
    problem, because ruling rung (2) out is what earns rung (3).
    """
    wanted = set(machines)
    report = HealthReport(name=name)

    # Every machine-like record in the world, not just the wanted ones: a starved machine's
    # feeder is routinely outside the factory being asked about, and every generator anywhere
    # is a source the reachability below has to start from.
    everything: dict[str, tuple[str, dict]] = {}
    for key in ("machines", "extractors", "generators"):
        for record in projection.get(key, ()):
            everything[record["instance"].rsplit(".", 1)[-1]] = (key, record)

    # What the never-run branch of `_classify` may read as "no run arrives". The feed rows
    # below keep the raw graph: they say NOTHING ARRIVES in their own words and always have.
    conduits = _Conduits.of(physical, everything) if physical is not None else None
    sources = {s for s, (key, _r) in everything.items() if key == "generators"}
    lit = _lit(graph, sources) if graph is not None else None

    def dark_of(actor: str) -> str:
        """Why nothing can power ``actor``, and empty where something can or nothing is known.

        Asks the graph about the machine rather than the machine about the graph: `build.py`
        puts every machine record in as a node precisely so an isolated one is a node with no
        edges rather than an absence, and ``neighbours`` on an unknown name is empty too.
        """
        if graph is None:
            return ""
        if not graph.neighbours(actor, "power"):
            return NO_WIRE
        return NO_GENERATOR if lit is not None and actor not in lit else ""

    def far_health(actor: str) -> tuple[dict | None, str]:
        found = everything.get(actor)
        if found is None:
            return None, ""
        key, record = found
        return record, _classify(key, record, game, dark_of(actor), actor, conduits, heads)[0]

    for short, (key, record) in everything.items():
        if short not in wanted:
            continue
        dark = dark_of(short)
        state, cause, uptime, recipe = _classify(
            key, record, game, dark, short, conduits, heads
        )
        entry = MachineHealth(
            instance=short,
            building=record.get("cls", "?"),
            recipe=recipe.name if recipe else "",
            state=state,
            uptime=uptime,
            cause=cause,
            clock=float(record.get("clock") or 1.0),
        )
        if state == "starved" and physical is not None:
            entry.feeds = _feed_rows(
                short, _missing_classes(record, recipe, game), game, physical, far_health, heads
            )
            entry.cause = _laddered(entry.cause, entry.feeds)
        report.machines.append(entry)
        report.by_state[state] += 1
        if dark == NO_WIRE:
            report.unwired.append(short)
        elif dark == NO_GENERATOR:
            report.no_generator.append(short)
        if state == "blocked":
            for item in entry.cause:
                report.blocked_on[item] += 1
        elif state == "starved" and recipe is not None:
            for flow in recipe.ingredients:
                report.starved_of[game.item_name(flow.item)] += 1

    return report


def summarise(report: HealthReport) -> str:
    """One line, states worst-first."""
    parts = [f"{report.by_state[s]} {s}" for s in STATES if report.by_state[s]]
    mean = report.mean_uptime
    head = f"{len(report.machines)} machines"
    if mean is not None:
        head += f", mean uptime {mean:.0%}"
    return head + (" -- " + ", ".join(parts) if parts else "")

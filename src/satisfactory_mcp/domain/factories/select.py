"""A tiny query language for carving out a set of machines.

A label is an *arbitrary* machine set, which is only workable if the player can name the
machines without listing instance ids: ``product:``, ``recipe:``, ``building:``, ``near:``,
``base:``, ``line:``, ``slab:``, ``proposal:``, ``label:``, ``machine:``, ``pin:`` and
``all``. Terms intersect, commas inside one term OR, and a leading ``-`` excludes, because
carving is subtractive in practice. docs/selectors.md has the whole grammar.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from ...core.gamedata.model import GameData
from ...core.saveio.schema import Projection
from ..spatial import geo, places
from .candidates import bases, cluster_machines, machines_making, positions, recipes_by_machine
from .model import FactoryGraph

if TYPE_CHECKING:
    from ..world.state import WorldState

__all__ = [
    "INDEX_WARNING",
    "SELECTOR_HELP",
    "SelectorError",
    "pin_notes",
    "resolve_factory",
    "select_machines",
]

#: Index selectors are positions in size-ordered lists rebuilt every call (§6.2b).
INDEX_WARNING = (
    "base:/line:/slab:/proposal: indices are positions in size-ordered lists rebuilt "
    "from this save -- they shift when you build. Name what you select now; do not "
    "reuse an index later."
)

SELECTOR_HELP = (
    "product:<item> | recipe:<name> | building:<class or name> | "
    "near:<place>@<radius_m> | base:<n> | line:<n> | slab:<n> | "
    "proposal:<n> | "
    "label:<name> | machine:<instance> | pin:<n> | all. Terms are ANDed; comma-separated values "
    "inside one term are ORed; prefix a term with '-' to exclude it"
)


class SelectorError(ValueError):
    """A selector that cannot be resolved, with the reason spelled out for the model."""


def _values(spec: str) -> list[str]:
    """Comma-separated alternatives inside one term. Empty pieces are dropped so a
    trailing comma is not an error."""
    return [v.strip() for v in spec.split(",") if v.strip()]


def _by_product(game: GameData, projection: Projection, spec: str) -> set[str]:
    return set(machines_making(game, projection, _values(spec)))


def _by_recipe(game: GameData, projection: Projection, spec: str) -> set[str]:
    wants = [v.casefold() for v in _values(spec)]
    out: set[str] = set()
    for machine, recipe_id in recipes_by_machine(projection).items():
        recipe = game.recipes.get(recipe_id)
        if recipe is None:
            continue
        name, low_id = recipe.name.casefold(), recipe_id.casefold()
        if any(w == name or w == low_id or w in name for w in wants):
            out.add(machine)
    return out


def _by_building(graph: FactoryGraph, game: GameData, spec: str) -> set[str]:
    """Matches the class name or the in-game display name.

    A player says "Foundry", not "Build_FoundryMk1_C"; the class is accepted too so a
    selector copied out of a tool result still works.
    """
    wants = [v.casefold() for v in _values(spec)]
    classes: set[str] = set()
    for cls in set(graph.cls.values()):
        building = game.buildings.get(cls)
        name: str = (getattr(building, "name", "") or "").casefold()
        low = cls.casefold()
        if any(w in low or (name and w in name) for w in wants):
            classes.add(cls)
    if not classes:
        raise SelectorError(f"no building matches {spec!r}")
    return {m for m in graph.machines() if graph.cls.get(m) in classes}


def _by_near(st: WorldState, graph: FactoryGraph, projection: Projection, spec: str) -> set[str]:
    """A circle around any place, resolved by the one place resolver every tool uses."""
    try:
        body, radius_m = places.parse_near(spec)
        centre, _where = places.resolve_place(st, body)
    except ValueError as exc:
        raise SelectorError(str(exc)) from exc
    pos = positions(projection)
    return {m for m in graph.machines() if m in pos and geo.distance_m(pos[m], centre) <= radius_m}


def _parse_index(spec: str, what: str, count: int) -> int:
    """``spec`` as an index into a list of ``count``, or a ``SelectorError`` saying why not."""
    try:
        index = int(spec)
    except ValueError as exc:
        raise SelectorError(f"{what}:{spec!r} needs an integer index") from exc
    if not 0 <= index < count:
        raise SelectorError(f"{what}:{index} out of range (0..{count - 1})")
    return index


def _indexed(groups: list[list[str]], spec: str, what: str) -> set[str]:
    return set(groups[_parse_index(spec, what, len(groups))])


def _resolve(
    term: str, st: WorldState, graph: FactoryGraph, game: GameData, projection: Projection
) -> set[str]:
    # Every facet is read from ``st`` INSIDE the branch that wants it: ``st.proposals`` is
    # the half-second view, and reading it up here would cost that on every term.
    if term.casefold() in ("all", "*"):
        return set(graph.machines())

    kind, sep, value = term.partition(":")
    if not sep:
        raise SelectorError(f"{term!r} is not a selector. Use one of: {SELECTOR_HELP}")
    kind = kind.strip().casefold()
    value = value.strip()

    if kind == "product":
        hits = _by_product(game, projection, value)
        if not hits:
            raise SelectorError(f"nothing is making {value!r} in this save")
        return hits
    if kind == "recipe":
        hits = _by_recipe(game, projection, value)
        if not hits:
            raise SelectorError(f"no machine is running a recipe matching {value!r}")
        return hits
    if kind == "building":
        return _by_building(graph, game, value)
    if kind == "near":
        return _by_near(st, graph, projection, value)
    if kind == "base":
        return _indexed(bases(graph), value, "base")
    if kind == "line":
        return _indexed(graph.machine_components("material"), value, "line")
    if kind == "slab":
        structures = st.structures
        # The slab's OWN index, as factory_map prints it: slabs are numbered by tile count,
        # and a list ordered by machine count would silently return a different platform.
        index = _parse_index(value, "slab", len(structures.slabs))
        # A bare platform selects nothing, which is an answer: factory_map lists it by this
        # index, so refusing it would point that table at a selector that fails.
        return set(structures.machines_on(index))
    if kind == "proposal":
        proposals = st.proposals
        return set(proposals[_parse_index(value, "proposal", len(proposals))].machines)
    if kind == "label":
        store = st.labels
        label = store.find(value) if store else None
        if label is None:
            raise SelectorError(f"no label named {value!r}")
        return set(label.anchors)
    if kind == "pin":
        return _by_pin(term, st, graph, game, projection)
    if kind == "machine":
        # Checked against the graph, because an unknown id used to select itself: the term
        # resolved to a one-element set of a machine that does not exist, and every tool
        # then reported "0 machines" for a typo, a stale id and a real machine alike.
        known = set(graph.machines())
        wanted = _values(value)
        missing = [v for v in wanted if v not in known]
        if missing:
            raise SelectorError(
                f"no machine {', '.join(repr(v) for v in missing)} in this save -- an id is "
                "the full instance name, as factory_query(show='machines') and trace_upstream "
                "print it"
            )
        return set(wanted)
    raise SelectorError(f"unknown selector {kind!r}. Use one of: {SELECTOR_HELP}")


def _pin_terms(term: str, st: WorldState) -> tuple[list[str], str]:
    from ..session import pins

    n = pins.parse(term)
    if n is None:
        raise SelectorError(f"{term!r} is not a pin: write pin:<n>. Use one of: {SELECTOR_HELP}")
    try:
        return pins.selector_terms(st, n, "machines")
    except pins.PinError as exc:
        raise SelectorError(str(exc)) from None


def _by_pin(
    term: str, st: WorldState, graph: FactoryGraph, game: GameData, projection: Projection
) -> set[str]:
    wanted, _echo = _pin_terms(term, st)
    out: set[str] = set()
    for inner in wanted:
        out |= _resolve(inner, st, graph, game, projection)
    return out


def pin_notes(selectors: list[str] | None, st: WorldState) -> list[str]:
    """What each ``pin:N`` term among ``selectors`` stood for, as tools echo it."""
    notes: list[str] = []
    for raw in selectors or ():
        term = raw.strip().removeprefix("-").strip()
        if term.casefold().startswith("pin:"):
            try:
                notes.append(_pin_terms(term, st)[1])
            except SelectorError:
                continue
    return notes


def expand_to_components(machines: set[str], graph: FactoryGraph) -> set[str]:
    """Pull in every machine belted or piped to one already selected.

    The complement of ``split``: some factories are defined by what feeds them rather
    than by what they make. The player's concrete setup is one miner into storage into
    one constructor into storage -- a self-contained 10-actor component that no product
    or radius term describes, but that the belt layer delimits exactly.
    """
    out = set(machines)
    for comp in graph.machine_components("material"):
        if out & set(comp):
            out |= set(comp)
    return out


def select_machines(
    selectors: list[str],
    st: WorldState,
    split: bool = False,
    expand: bool = False,
) -> list[str]:
    """Intersect the positive terms, then subtract the negated ones.

    Takes the whole world state rather than six of its facets, because ``near:`` resolves
    its place through the shared resolver and that resolver reads the save's labels, its
    slabs, its conduit runs, its plans and its player pawn. Handing over the pieces one
    call at a time is what left the two selector languages with different vocabularies.

    With ``split`` the result keeps only the largest spatial cluster. That is the
    escape hatch for a product that is made in several places at once -- 17 machines
    make Concrete across three sites, and a bare ``product:Concrete`` would name all
    of them as one factory.

    With ``expand`` the result grows to whole material components first. Exclusions are
    applied AFTER expanding, so ``-label:x`` still keeps a neighbouring factory out of
    the result rather than being undone by the expansion that follows it.
    """
    if not selectors:
        raise SelectorError("no selector given. " + SELECTOR_HELP)

    graph, game, projection = st.graph, st.game, st.projection
    include: set[str] | None = None
    exclude: set[str] = set()
    for raw in selectors:
        term = raw.strip()
        if not term:
            continue
        negate = term.startswith("-")
        resolved = _resolve(term[1:].strip() if negate else term, st, graph, game, projection)
        if negate:
            exclude |= resolved
        elif include is None:
            include = set(resolved)
        else:
            include &= resolved

    if include is None:
        raise SelectorError("only exclusions given; add something to start from, e.g. 'all'")
    if expand:
        include = expand_to_components(include, graph)
    result = include - exclude
    if split and result:
        groups = cluster_machines(sorted(result), projection)
        if groups:
            result = set(groups[0])
    return sorted(result)


def resolve_factory(st: WorldState, factory: str) -> tuple[str, list[str]]:
    """A label name, a selector, or a proposal index -- in that order.

    Label first because that is what a player types. Falling through to the selector
    grammar means ``factory_query("proposal:3", ...)`` works before anything is named.
    """
    label = st.labels.find(factory)
    if label is not None:
        alive = set(st.graph.machines())
        return label.name, [m for m in label.anchors if m in alive]
    try:
        picked = select_machines([factory], st)
    except SelectorError as exc:
        known = ", ".join(x.name for x in st.labels.labels) or "(none named yet)"
        raise SelectorError(f"{exc}. Named factories: {known}") from exc
    return factory, picked

"""A tiny query language for carving out a set of machines.

The whole labelling design rests on a label being an *arbitrary* machine set, because
no automatic grouping matches a player's own list. That is only workable if the player
can say which machines they mean without listing 50 instance ids. Hence selectors::

    product:Steel Pipe            everything making it, anywhere
    recipe:Alternate: Solid Steel Ingot
    building:Foundry
    near:-1069,-1273@200          within 200 m of any place -- a coordinate, me, a
    near:steel@150                factory, node:<id>, machine:<id>, slab:<n>, chain:<n>,
                                  plan:<name>
    base:0                        power island, largest first
    line:3                        material component, largest first
    slab:2                        foundation platform, by its own printed index
    proposal:7                    the nth cluster from propose_factories
    label:steel factory           what a label already covers
    machine:Build_SmelterMk1_C_3  named instances, exactly as the tools print them
    pin:4                         a machine or factory pin
    all                           every machine

Terms combine as an intersection, and any term may be negated with a leading ``-``::

    ["product:Concrete", "near:-1059,-1257@150"]      the 15-machine construction feed
    ["base:0", "-label:steel factory"]                the base minus what is named

Intersection rather than union because carving is subtractive in practice: the player
starts from something too big and narrows it. A comma inside one term is therefore always
the OR (or a coordinate pair), never a radius -- the radius follows ``@``, the same way
node selectors spell it. The whole grammar is written out in `docs/selectors.md`::

    ["product:Steel Ingot,Steel Pipe,Encased Industrial Beam,Steel Beam",
     "near:-1069,-1273@250"]
"""

from __future__ import annotations

from ...core.gamedata.model import GameData
from ..spatial import geo, places
from .candidates import bases, cluster_machines
from .model import FactoryGraph

__all__ = [
    "INDEX_WARNING",
    "SELECTOR_HELP",
    "SelectorError",
    "pin_notes",
    "resolve_factory",
    "select_machines",
]

#: ``base:``, ``line:``, ``slab:`` and ``proposal:`` are POSITIONS in lists that are
#: recomputed from the save every call, and every one of those lists is ordered by size.
#: Build a foundation or a machine and the numbering shifts. Read an index and name it in
#: the same breath; never store one. A LABEL is durable because it holds machine ids --
#: the index is only ever a way of pointing at them once.
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


def _positions(projection: dict) -> dict[str, tuple[float, float, float]]:
    out: dict[str, tuple[float, float, float]] = {}
    for key in ("machines", "extractors", "generators"):
        for record in projection.get(key, ()):
            pos = record.get("pos")
            if pos:
                out[record["instance"].rsplit(".", 1)[-1]] = tuple(pos)
    return out


def _recipe_of(projection: dict) -> dict[str, str]:
    return {
        r["instance"].rsplit(".", 1)[-1]: r["recipe"]
        for r in projection.get("machines", ())
        if r.get("recipe")
    }


def _values(spec: str) -> list[str]:
    """Comma-separated alternatives inside one term. Empty pieces are dropped so a
    trailing comma is not an error."""
    return [v.strip() for v in spec.split(",") if v.strip()]


def _by_product(game: GameData, projection: dict, spec: str) -> set[str]:
    want = {v.casefold() for v in _values(spec)}
    out = set()
    for machine, rid in _recipe_of(projection).items():
        recipe = game.recipes.get(rid)
        if recipe is None:
            continue
        if any(game.item_name(f.item).casefold() in want for f in recipe.products):
            out.add(machine)
    return out


def _by_recipe(game: GameData, projection: dict, spec: str) -> set[str]:
    wants = [v.casefold() for v in _values(spec)]
    out = set()
    for machine, rid in _recipe_of(projection).items():
        recipe = game.recipes.get(rid)
        if recipe is None:
            continue
        name, low_id = recipe.name.casefold(), rid.casefold()
        if any(w == name or w == low_id or w in name for w in wants):
            out.add(machine)
    return out


def _by_building(graph: FactoryGraph, game: GameData, spec: str) -> set[str]:
    """Matches the class name or the in-game display name.

    A player says "Foundry", not "Build_FoundryMk1_C"; the class is accepted too so a
    selector copied out of a tool result still works.
    """
    wants = [v.casefold() for v in _values(spec)]
    classes = set()
    for cls in set(graph.cls.values()):
        building = game.buildings.get(cls)
        name = (getattr(building, "name", "") or "").casefold()
        low = cls.casefold()
        if any(w in low or (name and w in name) for w in wants):
            classes.add(cls)
    if not classes:
        raise SelectorError(f"no building matches {spec!r}")
    return {m for m in graph.machines() if graph.cls.get(m) in classes}


def _by_near(st, graph: FactoryGraph, projection: dict, spec: str) -> set[str]:
    """A circle around any place, resolved by the one place resolver every tool uses."""
    try:
        body, radius_m = places.parse_near(spec)
        centre, _where = places.resolve_place(st, body)
    except ValueError as exc:
        raise SelectorError(str(exc)) from exc
    pos = _positions(projection)
    return {
        m for m in graph.machines() if m in pos and geo.distance_m(pos[m][:2], centre) <= radius_m
    }


def _indexed(groups: list[list[str]], spec: str, what: str) -> set[str]:
    try:
        index = int(spec)
    except ValueError as exc:
        raise SelectorError(f"{what}:{spec!r} needs an integer index") from exc
    if not 0 <= index < len(groups):
        raise SelectorError(f"{what}:{index} out of range (0..{len(groups) - 1})")
    return set(groups[index])


def _resolve(term: str, st, graph: FactoryGraph, game: GameData, projection: dict) -> set[str]:
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
        if structures is None:
            raise SelectorError("slab: needs the structure layer; re-read the save")
        # The slab's OWN index, which is what factory_map prints. Indexing into a list
        # ordered by machine count instead would silently return a different platform:
        # slabs are numbered by tile count, and the two orderings do not agree.
        try:
            index = int(value)
        except ValueError as exc:
            raise SelectorError(f"slab:{value!r} needs an integer index") from exc
        if not 0 <= index < len(structures.slabs):
            raise SelectorError(f"slab:{index} out of range (0..{len(structures.slabs) - 1})")
        # A platform with nothing on it selects nothing, and that is an answer rather than
        # an error: factory_map lists bare platforms by this index, so refusing them made
        # that table point at a selector it had just told the reader to use.
        return set(structures.machines_on(index))
    if kind == "proposal":
        proposals = st.proposals
        if proposals is None:
            raise SelectorError("proposal: needs the proposal list; re-read the save")
        try:
            index = int(value)
        except ValueError as exc:
            raise SelectorError(f"proposal:{value!r} needs an integer index") from exc
        if not 0 <= index < len(proposals):
            raise SelectorError(f"proposal:{index} out of range (0..{len(proposals) - 1})")
        return set(proposals[index].machines)
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


def _pin_terms(term: str, st) -> tuple[list[str], str]:
    from ..session import pins

    n = pins.parse(term)
    if n is None:
        raise SelectorError(f"{term!r} is not a pin: write pin:<n>. Use one of: {SELECTOR_HELP}")
    try:
        return pins.selector_terms(st, n, "machines")
    except pins.PinError as exc:
        raise SelectorError(str(exc)) from None


def _by_pin(term: str, st, graph: FactoryGraph, game: GameData, projection: dict) -> set[str]:
    wanted, _echo = _pin_terms(term, st)
    out: set[str] = set()
    for inner in wanted:
        out |= _resolve(inner, st, graph, game, projection)
    return out


def pin_notes(selectors: list[str] | None, st) -> list[str]:
    """What each ``pin:N`` term among ``selectors`` stood for, as tools echo it."""
    notes = []
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
    st,
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


def resolve_factory(st, factory: str):
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

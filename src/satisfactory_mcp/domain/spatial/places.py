"""Where a place is: a coordinate, the player, a factory, a node, a run, a slab, a plan.

The ONE place vocabulary. Every tool that takes a place and both selector languages
resolve through ``resolve_place``, so a term that works in one of them works in all of
them. The grammar it implements is written out in `docs/selectors.md`.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, TypeAlias

from ...core.saveio.records import instance_leaf, iter_machine_records
from . import geo, nodes

if TYPE_CHECKING:
    from ..factories.labels import Label
    from ..world.state import WorldState

#: A place in centimetres, and what it resolved FROM.
Place: TypeAlias = tuple[tuple[float, float], str]

#: The conduit-run spelling this project settles on, everywhere: ``chain:<n>`` for a belt
#: chain and ``pipe:<n>`` for a pipeline piece, which is the ident ``search_conduits``
#: prints in its own id and connects columns. The web map's "pipe #12" is a caption.
RUN_PREFIXES = ("chain", "pipe")

#: Foundation platforms, by the index ``factory_map show=slabs`` prints.
SLAB_PREFIX = "slab"

#: One resource node, by the instance id ``search_resource_nodes`` prints -- the same
#: spelling the node selectors use to pick that node OUT of a field.
NODE_PREFIX = "node"

#: One standing machine, by the instance id the tools print, as in ``machine:`` selects.
MACHINE_PREFIX = "machine"

#: A stored plan's recorded site, by the plan's own name.
PLAN_PREFIX = "plan"

#: A located pin, by the number the pins card prints.
PIN_PREFIX = "pin"

#: The player, spelled the same wherever a place is taken.
PLAYER_WORDS = ("me", "player", "here")

#: The HUB. A factory label of the same name wins, so naming one "HUB" changes nothing.
HUB_WORDS = ("hub",)

#: The HUB's own parts; the HUB itself carries no record in the projection.
HUB_PARTS = ("Build_StorageIntegrated_C", "Build_GeneratorIntegratedBiomass_C")

#: What a term that named no place should have said, so rule 6 of the grammar holds
#: wherever a place is taken rather than only where a tool remembered to list them.
PLACE_GRAMMAR = (
    "A place is x,y in metres, 'me', 'hub', a factory name, node:<id>, machine:<id>, "
    "slab:<n>, chain:<n>/pipe:<n>, plan:<name>, or pin:<n>"
)

#: The one spelling of a circle, for node selectors and machine selectors alike. ``@``
#: separates the place from the radius so that a comma always means a coordinate or an
#: alternative and never a distance.
NEAR_GRAMMAR = (
    "A circle is near:<place>@<radius_m> -- e.g. near:-1069,-1273@200, near:me@500, "
    "near:steel factory@150. The radius follows '@', never a comma"
)


def parse_near(spec: str) -> tuple[str, float]:
    """Split a ``near:`` body into its place and its radius in metres.

    Splits on the LAST ``@`` so a label may contain one. The retired ``x,y,r`` spelling is
    recognised only to name its replacement: a stored plan written that way must fail
    loudly rather than resolve to a circle it no longer describes.
    """
    place, at, radius_txt = spec.rpartition("@")
    if not at:
        raise ValueError(f"near:{spec!r} has no radius. {_retired(spec)}{NEAR_GRAMMAR}")
    place, radius_txt = place.strip(), radius_txt.strip()
    if not place:
        raise ValueError(f"near:{spec!r} has no place before '@'. {NEAR_GRAMMAR}")
    try:
        radius_m = float(radius_txt)
    except ValueError:
        raise ValueError(
            f"near: radius must be a number in metres, got {radius_txt!r}. {NEAR_GRAMMAR}"
        ) from None
    return place, radius_m


def _retired(spec: str) -> str:
    parts = [p.strip() for p in spec.split(",")]
    if len(parts) == 3 or (len(parts) == 2 and parts[0].casefold() in PLAYER_WORDS):
        return f"Write near:{','.join(parts[:-1])}@{parts[-1]} instead. "
    return ""


def player_xy(st: WorldState | None) -> tuple[float, float] | None:
    """Player XY for the near:me selector, or None if the save has no pawn."""
    here = st.player_position() if st else None
    return (here[0], here[1]) if here else None


def hub_xy(st: WorldState | None) -> tuple[float, float] | None:
    """The HUB's position in centimetres, off its built-in storage or burners."""
    if st is None:
        return None
    for key in ("storage", "generators"):
        for cls in HUB_PARTS:
            for record in st.projection.get(key, ()):
                if record.get("cls") == cls and record.get("pos"):
                    return record["pos"][0], record["pos"][1]
    return None


def _run_origin(st: WorldState | None, text: str) -> Place:
    """Centre on a belt chain or pipe piece, by the ident ``search_conduits`` prints.

    Its MIDPOINT, so a radius around it reaches both ways along the run; the answer names
    the run's own length, because a radius smaller than that only sees part of it.
    """
    if st is None:
        raise ValueError(f"{text!r} names a conduit run, which needs a readable save")
    want = text.casefold()
    for run in st.conduit_runs:
        if run.ident.casefold() == want:
            return run.midpoint(), f"{run.ident} (midpoint of a {run.length_m:.0f}m {run.label})"
    raise ValueError(f"no conduit run called {text!r}; search_conduits lists the ids it takes")


def _slab_origin(st: WorldState | None, text: str) -> Place:
    """Centre on a foundation platform, by the index ``factory_map show=slabs`` prints.

    The tile MEAN the slab carries, not its bbox centre: an L-shaped platform's bbox
    centre is a spot with no floor on it. A platform with no machines on it resolves like
    any other -- a bare slab is exactly the thing this answers questions about.
    """
    if st is None:
        raise ValueError(f"{text!r} names a platform, which needs a readable save")
    try:
        index = int(text.partition(":")[2])
    except ValueError:
        raise ValueError(
            f"{text!r} needs an integer index, the one factory_map show=slabs prints"
        ) from None
    slabs = st.structures.slabs
    if not 0 <= index < len(slabs):
        raise ValueError(f"slab:{index} out of range (0..{len(slabs) - 1})")
    slab = slabs[index]
    width, depth = slab.extent
    return (slab.centre[0], slab.centre[1]), (
        f"slab:{index} ({slab.tiles} tiles, {width / 100:.0f}x{depth / 100:.0f}m, "
        f"{slab.storeys} storey(s))"
    )


def _node_origin(text: str) -> Place:
    """Centre on one resource node, by the id ``search_resource_nodes`` prints.

    Map data rather than save data, so this is the one place kind that resolves with no
    save at all. The short id is accepted beside the full instance path because that is
    the half the tools print.
    """
    want = text.partition(":")[2].strip()
    table = nodes.load_nodes()
    for instance, node in table.by_instance().items():
        if want in (instance, instance_leaf(instance)):
            return (node["x"], node["y"]), f"node:{want} ({node['purity']} {node['kind']})"
    raise ValueError(f"no resource node called {want!r}; search_resource_nodes lists the ids")


def _machine_origin(st: WorldState | None, text: str) -> Place:
    """Centre on one standing machine; the leaf and the full instance path both match."""
    if st is None:
        raise ValueError(f"{text!r} names a machine, which needs a readable save")
    want = instance_leaf(text.partition(":")[2].strip())
    for _group, leaf, record in iter_machine_records(st.projection):
        if (pos := record.get("pos")) and leaf == want:
            return (pos[0], pos[1]), f"machine:{want}"
    raise ValueError(f"no machine called {want!r} in this save")


def _plan_origin(st: WorldState | None, text: str) -> Place:
    """Centre on a stored plan's recorded site, which only a sited plan has."""
    # Imported here because planning.siting imports this module: the plan record is
    # parsed in one place, and that place is above this one.
    from ..planning import siting

    if st is None:
        raise ValueError(f"{text!r} names a stored plan, which needs a readable save")
    name = text.partition(":")[2].strip()
    stored = st.plans.find(name)
    if stored is None:
        known = ", ".join(x.name for x in st.plans.plans) or "(none)"
        raise ValueError(f"no saved plan named {name!r}. Saved: {known}")
    sited = siting.parse(stored)
    if sited is None:
        raise ValueError(
            f"plan {stored.name!r} has no siting recorded -- set one with site_plan, or "
            f"plan_factory site_at=... save_as={stored.name!r}"
        )
    return (sited.x_m * 100.0, sited.y_m * 100.0), f"plan {stored.name!r} site ({sited.describe()})"


def resolve_place(st: WorldState | None, near: str) -> Place:
    """Resolve a place to a point in centimetres, paired with what it resolved FROM.

    A factory name is the useful one now that factories exist -- "nearest coal to the
    coal powerplant" is the question actually being asked, and hand-copying a centroid
    out of another tool's output is how the wrong coordinate gets used. A node id, a run
    ident, a platform index and a plan name close the same loop for four more sets of
    ids that other tools print and nothing would take back.
    """
    text = near.strip()
    head = text.partition(":")[0].casefold()
    if ":" in text:
        # Only a KNOWN prefix is claimed here; anything else falls through to the label
        # lookup, because a factory may be named with a colon in it.
        if head in RUN_PREFIXES:
            return _run_origin(st, text)
        if head == SLAB_PREFIX:
            return _slab_origin(st, text)
        if head == NODE_PREFIX:
            return _node_origin(text)
        if head == MACHINE_PREFIX:
            return _machine_origin(st, text)
        if head == PLAN_PREFIX:
            return _plan_origin(st, text)
        if head == PIN_PREFIX:
            return _pin_origin(st, text)
    if "," in text:
        try:
            x_m, y_m = (float(v) for v in text.split(",", 1))
        except ValueError as exc:
            raise ValueError(f"{near!r} is not an x,y pair in metres") from exc
        return (x_m * 100.0, y_m * 100.0), f"{int(x_m)},{int(y_m)}"

    if text.casefold() in PLAYER_WORDS:
        here = player_xy(st)
        if here is None:
            raise ValueError("this save has no player pawn, so 'me' cannot be resolved")
        return here, "you"

    label = st.labels.find(text) if st else None
    if label is None and text.casefold() in HUB_WORDS:
        hub = hub_xy(st)
        if hub is None:
            raise ValueError("this save has no HUB, so 'hub' cannot be resolved")
        return hub, "the HUB"
    if st is None or label is None:
        known = ", ".join(x.name for x in st.labels.labels) if st else ""
        raise ValueError(
            f"{near!r} does not name a place. {PLACE_GRAMMAR}"
            + (f". Named factories: {known}" if known else "")
        )
    centre = label_centre(st, label)
    if centre is None:
        raise ValueError(f"{label.name!r} has no machines left to centre on")
    return centre, label.name


def _pin_origin(st: WorldState | None, text: str) -> Place:
    from ..session import pins

    n = pins.parse(text)
    if n is None:
        raise ValueError(f"{text!r} is not a pin: write pin:<n>, as the pins card prints it")
    try:
        return pins.position(st, n)
    except pins.PinError as exc:
        raise ValueError(str(exc)) from None


def label_centre(st: WorldState, label: Label) -> tuple[float, float] | None:
    """The centroid of a label's standing machines in centimetres, or None when none stand."""
    pos = {
        leaf: here
        for _group, leaf, record in iter_machine_records(st.projection)
        if (here := record.get("pos"))
    }
    points = [(pos[m][0], pos[m][1]) for m in label.anchors if m in pos]
    return geo.centroid(points) if points else None

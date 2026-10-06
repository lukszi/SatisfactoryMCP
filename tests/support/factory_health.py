"""Hand-built machine records, stub graphs and the ``assess`` call the health tests share."""

from __future__ import annotations

from satisfactory_mcp.core.saveio import ports
from satisfactory_mcp.domain.factories.health import assess
from satisfactory_mcp.domain.world.headlift import HeadLift
from satisfactory_mcp.domain.world.logistics import BY_ROLE, Link

#: The productivity window the game measures over, in seconds.
WINDOW = 300.0

#: The one pole every ``SingleCircuit`` node hangs off. ``assess`` walks outwards from the
#: generators, so the stub answers from both ends or every other machine reads as dark.
POLE = "a pole"


def uptime_record(fraction: float) -> dict:
    """A monitor that saw the machine produce for ``fraction`` of the window."""
    return {
        "window_s": WINDOW,
        "produce_s": WINDOW * fraction,
        "cur_window_s": 100.0,
        "cur_produce_s": 0.0,
        "producing": fraction > 0,
    }


def machine(name, recipe, *, uptime=None, buffers=None, **extra):
    """A machine record as the projection stores it."""
    record = {
        "instance": f"L:P.{name}",
        "cls": name.rsplit("_", 1)[0],
        "recipe": recipe,
        "pos": [0, 0, 0],
        **extra,
    }
    if uptime is not None:
        record["uptime"] = uptime
    if buffers is not None:
        record["buffers"] = buffers
    return record


class SingleCircuit:
    """A ``FactoryGraph`` reduced to the one question ``assess`` asks it: one shared circuit."""

    def __init__(self, wired):
        self.wired = set(wired)

    def neighbours(self, node, layer="material"):
        assert layer == "power", "health asks about electricity and nothing else"
        if node == POLE:
            return sorted(self.wired)
        return [POLE] if node in self.wired else []


class ArrivingRuns:
    """A ``PhysicalGraph`` reduced to the one question ``assess`` asks it."""

    def __init__(self, arriving):
        self.arriving = list(arriving)

    def feeds(self, actor):
        return list(self.arriving)


def link(source, target, medium=ports.CONVEYOR, basis=BY_ROLE, pieces=1):
    return Link(source=source, target=target, medium=medium, basis=basis, pieces=pieces)


def head_lift_with(crests=(), unfed=()):
    """A head-lift model holding only the given crests and unfed ports."""
    return HeadLift(
        crests=tuple(crests),
        consumers=0,
        unfed_ports=tuple(unfed),
        networks=0,
        gas_networks=0,
        ambiguous_ports=0,
    )


def assess_records(
    game, machines=(), extractors=(), generators=(), graph=None, physical=None, heads=None
):
    """``assess`` over a projection holding only the given records."""
    projection = {
        "machines": list(machines),
        "extractors": list(extractors),
        "generators": list(generators),
    }
    return assess("test", record_names(projection), game, projection, graph, physical, heads)


def record_names(projection: dict) -> list[str]:
    """The leaf name of every machine, extractor and generator in ``projection``."""
    return [
        record["instance"].rsplit(".", 1)[-1]
        for key in ("machines", "extractors", "generators")
        for record in projection.get(key, ())
    ]


def state_of(report, instance):
    return next(m.state for m in report.machines if m.instance == instance)


def rungs_of(report, instance):
    """The plumbing ladder rung each ingredient of ``instance`` stopped at."""
    found = next(m for m in report.machines if m.instance == instance)
    return {f.item: f.rung for f in found.feeds}

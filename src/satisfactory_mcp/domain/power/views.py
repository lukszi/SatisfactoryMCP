"""The power report as data: ``PowerLedger.power_report``'s answer, field for field.

Wire rules, and why these are ``typing_extensions`` TypedDicts: docs/web-wire.md.
"""

from __future__ import annotations

from typing_extensions import TypedDict

__all__ = ["GeneratorTotal", "PowerReport", "StarvedEntry"]


class GeneratorTotal(TypedDict):
    """One generator class, counted and summed: a value of ``PowerReport.by_generator``."""

    name: str
    count: int
    mw: float


class StarvedEntry(TypedDict):
    """A generator out of an input it burns, and idle for it; ``missing`` names the inputs."""

    instance: str
    name: str
    mw: float
    missing: list[str]


class PowerReport(TypedDict):
    """Generation capacity, draw both nameplate and measured, and what each leaves out.

    Declaration order is the order ``PowerLedger.power_report`` returns them in; what each
    figure can claim is save-projection §6.1a. ``utilisation`` is never null: it is
    ``measured / draw``, and ``1.0`` when nothing draws at all -- a factory with nothing built
    is fully utilised in the only sense the ratio has.
    """

    generation_mw: float
    draw_mw: float
    headroom_mw: float
    measured_draw_mw: float
    measured_headroom_mw: float
    monitored: int
    unmonitored: int
    paused_consumers: int
    utilisation: float
    by_generator: dict[str, GeneratorTotal]
    #: Generator classes Docs carries no entry for. Sorted, and empty on a world that has
    #: none, which is a measurement rather than a gap.
    unmodellable: list[str]
    paused_count: int
    starved_generators: list[StarvedEntry]
    starved_generation_mw: float
    #: Records on no power edge, left out of every figure above.
    unwired_generators: int
    unwired_generation_mw: float
    unwired_consumers: int
    unwired_draw_mw: float
    unwired_paused: int
    #: Whether hand-fed biomass burners count; when they do not, the wired, unpaused ones
    #: and their MW are what was left out.
    biomass_counted: bool
    biomass_generators: int
    biomass_mw: float

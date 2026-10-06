"""The solver's vocabulary: a process column, the scenario to solve, and its solution."""

from __future__ import annotations

import math
from dataclasses import dataclass, field

from ....core.gamedata.model import GameData

__all__ = [
    "MW",
    "NEGLIGIBLE_IPM",
    "PAYBACK_STOPS",
    "Process",
    "Scenario",
    "Solution",
    "normalise_objective",
]

MW = "__MW__"
#: The payback horizons the solve is read out at, in hours of play.
#: docs/planner-payback-horizon_contract.md §2.
PAYBACK_STOPS = (0.0, 1.0, 2.0, 5.0, 10.0, 20.0, 50.0, 100.0)
#: A process whose every item rate is below this is omitted from the build table, though
#: still counted; a rate, not a machine count (docs/planning.md §8.2c).
NEGLIGIBLE_IPM = 0.01

#: Both spellings turn up in one conversation, so each is accepted (docs/planning.md §8.9).
_OBJECTIVE_ALIASES = {
    "max_power": "max_mw",
    "maximise_power": "max_mw",
    "maximize_power": "max_mw",
    "max_watts": "max_mw",
    "min_mw": "min_power",
    "minimise_power": "min_power",
    "minimize_power": "min_power",
}


def normalise_objective(objective: str) -> str:
    """Canonical objective name, accepting the power/mw spellings interchangeably."""
    key = (objective or "").strip().casefold()
    return _OBJECTIVE_ALIASES.get(key, key)


@dataclass
class Process:
    """One column of the matrix."""

    pid: str
    kind: str  # recipe | extractor | generator
    label: str
    rates: dict[str, float]  # item -> net per-minute for ONE unit
    mw: float  # net MW for one unit at this mode: negative consumes, positive generates
    #: MW for one machine at 100% and the exponent, so the readout recomputes power exactly.
    mw_at_full: float = 0.0
    power_exponent: float = 1.0
    building: str | None = None
    recipe: str | None = None
    #: Node purity of an extractor: the key that joins its row back to the save's nodes.
    purity: str = ""
    clock: float = 1.0
    sloops: int = 0
    max_count: float | None = None
    #: Clock modes of the same machines share a group and one cap (docs/planning.md §8.9).
    group: str | None = None

    def net(self, item: str) -> float:
        return self.rates.get(item, 0.0)


@dataclass
class Scenario:
    """Inputs to a solve."""

    game: GameData
    recipes: list[str]  # allowed recipe ids
    objective: str = "max_mw"  # max_mw | max_item | min_raw | min_machines | min_power
    target_item: str | None = None
    #: Items that may leave the plant: a whitelist, never every item (docs/planning.md §8.2).
    exports: tuple[str, ...] = (MW,)
    export_minimums: dict[str, float] = field(default_factory=dict)
    raw_caps: dict[str, float] = field(default_factory=dict)
    #: Per-resource weight in ``min_raw``, 1.0 when missing; 0 only as the first half of a
    #: lexicographic pair (docs/planning.md §8.7).
    raw_weights: dict[str, float] = field(default_factory=dict)
    extractor_nodes: dict[tuple[str, str, str], int] = field(default_factory=dict)
    allow_sinks: bool = True
    #: Extra clock modes offered as choices; ratio clocks need none (docs/planning.md §8.4).
    clocks: tuple[float, ...] = (1.0,)
    #: Clock modes for extractors only; None uses ``clocks`` (docs/planning.md §8.9).
    extractor_clocks: tuple[float, ...] | None = None
    #: Process ids removed by name, for burn and extraction no recipe ban reaches
    #: (docs/planning.md §8.2c).
    excluded_pids: frozenset[str] = frozenset()
    #: Pids that may run but must not feed each other (docs/planning.md §8.2h).
    recycle_once: frozenset[str] = frozenset()
    sloop_budget: int = 0
    max_machines: float | None = None
    #: MW one machine costs when the goal is power, from a 5 h horizon its build points over
    #: the horizon instead (``machine_price_mw``, docs/planning.md §8.4).
    machine_cost_mw: float = 5.0
    belt_ipm: float = 780.0  # Mk5; used to price sinks and to count logistics lines
    pipe_m3min: float = 600.0
    #: Force whole machine-equivalents in the solver; off, ratios stay exact (§8.4).
    integral: bool = False
    buildings_available: set[str] | None = None
    #: MW the plant may draw from the grid; forced to 0 when MW is exported
    #: (docs/planning.md §8.1).
    grid_import_mw: float | None = None
    #: Hours of play the power a spread row saves must repay its extra machines in. 0 is
    #: the plain build. docs/planner-payback-horizon_contract.md.
    payback_hours: float = 0.0
    #: Running price of power, points per MWh.
    power_price: float = 0.0
    #: Build points per building class: save-priced materials plus floor area.
    build_points: dict[str, float] = field(default_factory=dict)
    #: Offer each row one machine fewer, the last one overclocked, as a priced candidate.
    overclock_last: bool = False
    #: Power Shards that candidate may spend; None is no limit.
    overclock_shards: float | None = None
    #: Per recipe id, a row's own choice over ``overclock_last``: ``"last"`` builds the
    #: overclocked candidate whatever it costs, ``"spread"`` never does.
    row_overclock: dict[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.objective = normalise_objective(self.objective)
        hours = self.payback_hours
        if isinstance(hours, bool) or not math.isfinite(hours) or hours < 0:
            raise ValueError(f"payback_hours must be 0 or more, not {hours!r}")


@dataclass
class Solution:
    status: str
    objective_value: float
    net_mw: float
    processes: list[dict]
    raw_used: dict[str, float]
    exports: dict[str, float]
    sunk: dict[str, float]
    machines_total: float
    grid_import_mw: float = 0.0
    machine_penalty_mw: float = 0.0
    logistics: list[dict] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    binding: list[str] = field(default_factory=list)
    #: The same recipes read out at every payback stop:
    #: ``{hours, machines, draw_mw, buildings: {class: count}, shards}``, where
    #: ``buildings`` counts only the rows a horizon can spread. The last entry, flagged
    #: ``plain``, is 0 h with no overclock: what every stop is compared with.
    payback_curve: list[dict] = field(default_factory=list)
    #: The overclock-last pick at this solve's horizon, made whether or not it is on.
    overclock: dict = field(default_factory=dict)

    @classmethod
    def infeasible(cls, reason: str) -> Solution:
        """A solve that found no plan, with ``reason`` as its one warning."""
        return cls("infeasible", 0.0, 0.0, [], {}, {}, {}, 0.0, warnings=[reason])

    @property
    def ok(self) -> bool:
        return self.status == "optimal"

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
#: A process whose every item rate is below this is omitted from the build table.
#: One item per ten hours is not a build instruction, and a whole machine printed at
#: 0.0087% clock reads as one. Deliberately a RATE rather than a machine count: the same
#: fraction of a machine means very different throughput for a miner and a refinery.
NEGLIGIBLE_IPM = 0.01

#: "power" reads more naturally than "mw" in a sentence, and both show up in the
#: same conversation, so either spelling is accepted everywhere an objective or an
#: export is named.
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
    #: MW for one machine at 100% clock, and the exponent power scales by. Together
    #: these let the readout recompute power exactly at the derived clock instead of
    #: assuming it is linear.
    mw_at_full: float = 0.0
    power_exponent: float = 1.0
    building: str | None = None
    recipe: str | None = None
    #: Node purity for extractor columns. Carried through to the readout because it is
    #: the only key that joins a plan's extractor row back to the nodes in the save --
    #: recovering it by parsing the pid would tie the diff to a string format.
    purity: str = ""
    clock: float = 1.0
    sloops: int = 0
    max_count: float | None = None
    #: Processes sharing a group draw on the SAME physical machines, so their counts
    #: must sum under one cap. Without it, offering a node set at two clock speeds
    #: would let the solver mine every node twice.
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
    #: Items that may leave the system. MW-only is the default for a power plant, but
    #: it makes a crude-oil plant infeasible, because every crude->fuel route emits
    #: Polymer Resin and resin only terminates in plastic or rubber.
    exports: tuple[str, ...] = (MW,)
    export_minimums: dict[str, float] = field(default_factory=dict)
    raw_caps: dict[str, float] = field(default_factory=dict)
    #: Per-resource weight in the ``min_raw`` objective. Missing means 1.0.
    #:
    #: Exists because ``min_raw`` otherwise sums every resource with weight one and
    #: therefore trades crude against water. Water is effectively unlimited on this
    #: map, so that trade is always the wrong way round -- measured at 0.94 m3 crude
    #: per Plastic with zero water, when 0.33 crude plus water was available. A
    #: weight of 0 makes a resource free, which only makes sense as the first half
    #: of a lexicographic pair: minimise the priced resources, then pin them and
    #: minimise the free one, or the free one comes back at its cap.
    raw_weights: dict[str, float] = field(default_factory=dict)
    extractor_nodes: dict[tuple[str, str, str], int] = field(default_factory=dict)
    allow_sinks: bool = True
    #: Extra discrete clock modes to offer the solver as CHOICES.
    #:
    #: Normally you want just (1.0). Ratio underclocking does not need a mode: a
    #: solution of 52.8 machine-equivalents is reported as 53 machines at 99.6%,
    #: which is exact, always a clean ratio, and provably the power-optimal way to
    #: run that throughput (c**1.32 is convex, so a uniform clock beats any mix).
    #:
    #: Offering explicit sub-100% modes lets the solver instead SPREAD a fixed
    #: throughput over more machines purely to save power -- measured at +1140 MW for
    #: +441 machines. That is a real option but it is not free, so it is priced by
    #: machine_cost_mw. Overclock modes are not offered by default because they
    #: consume Power Shards, which nothing here counts.
    clocks: tuple[float, ...] = (1.0,)
    #: Clock modes offered to EXTRACTORS only, when they should differ from the rest.
    #:
    #: Overclocking miners and pumps is the standard play -- they are capped by how
    #: many nodes exist, so the only way to get more from a fixed node is to run it
    #: faster -- while overclocking production machines usually just burns power.
    #: None means extractors use `clocks` like everything else.
    extractor_clocks: tuple[float, ...] | None = None
    #: Process ids removed by name. Generator burn and extraction are SYNTHESISED here
    #: from building data -- they are not recipes and have no entry in Docs.json -- so
    #: exclude_recipes could never reach them. "Coal-Powered Generator on Coal" printed
    #: in the build table matched nothing, and the user had to drop 20 generators by
    #: hand after noticing coal happened to be a leaf.
    excluded_pids: frozenset[str] = frozenset()
    #: Process ids that may run but must NOT feed each other -- "use this cycle once".
    #: Named rather than detected, and not a pass count: docs/planning.md says why.
    recycle_once: frozenset[str] = frozenset()
    sloop_budget: int = 0
    max_machines: float | None = None
    #: What one machine costs, in MW, when the objective is power.
    #:
    #: Only bites when `clocks` offers sub-100% modes. Not arbitrary: spreading
    #: throughput via 50% clocks was measured to gain +1140 MW for +441 machines,
    #: i.e. 2.58 MW per extra machine. A default above that rejects marginal
    #: spreading while still accepting a genuinely good trade. Set to 0 to reproduce
    #: an unpriced (ill-posed) max-power solve. From a 5 h payback horizon each
    #: building's ``K / (H r)`` replaces it (``machine_mw``).
    machine_cost_mw: float = 5.0
    belt_ipm: float = 780.0  # Mk5; used to price sinks and to count logistics lines
    pipe_m3min: float = 600.0
    #: Force whole machine-equivalents in the SOLVER.
    #:
    #: Off by default and rarely wanted: a fractional result is not a rounding error,
    #: it is the exact throughput, and it is rendered as whole machines at a derived
    #: clock. Forcing integrality here instead makes exact ratios unreachable and can
    #: turn a feasible plan infeasible, because every item balance is an equality.
    integral: bool = False
    buildings_available: set[str] | None = None
    #: MW the plant may draw from the existing grid.
    #:
    #: Without this the power row forces generation == consumption, i.e. every plan
    #: must be fully self-powered -- which silently reports 0 output for any factory
    #: that has no on-site generator able to burn its own byproducts.
    #: Ignored (forced to 0) when MW is an export, since a power plant that imports
    #: power to export it is unbounded.
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

    @property
    def ok(self) -> bool:
        return self.status == "optimal"

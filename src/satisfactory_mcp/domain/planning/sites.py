"""Cutting one solved plan into named sites, and reporting what crosses the boundaries.

This is NOT a joint multi-site solver: nothing in the model prices distance, so a joint LP
with no per-site cap would collapse every site into one and would be RIGHT to, since a
split into rig, generator hall and resin plant is usually a preference the caller never
expressed as a constraint. What has a defensible answer is to declare the partition and
report it -- every flow crossing a boundary, as item, rate, direction and carrier count.

The unit of assignment is a PROCESS rather than a machine, so shared infrastructure lives
wholly in one site and exports: a water farm serving two coastal sites appears as an
interface that may not exist on the ground, and splitting it would mean per-MACHINE
assignment, which breaks the "a machine is in one place" rule that makes ``contested``
meaningful. An interface the caller named is reported even at zero, because a decoupled
design is characterised entirely by one flow BEING zero and a table of nonzero rows would
omit exactly that.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from ...core.gamedata.model import GameData
from .carrier import carrier_for
from .planlog import is_power
from .slice import PlanSlice, slice_of

__all__ = ["Interface", "Site", "SitePlan", "claim_processes", "partition"]

#: Net rate below this is LP noise rather than a flow between two places.
_EPS = 1e-6


@dataclass
class Site:
    name: str
    patterns: list[str] = field(default_factory=list)
    slice: PlanSlice | None = None

    @property
    def machines(self) -> int:
        return self.slice.machines if self.slice else 0

    @property
    def net_mw(self) -> float:
        # Partial slices carry no sink share, so this is generation minus draw only.
        return self.slice.net_mw if self.slice else 0.0


@dataclass
class Interface:
    item: str
    name: str
    source: str
    target: str
    rate: float
    carrier: str
    lines: int

    @property
    def zero(self) -> bool:
        return abs(self.rate) <= _EPS


@dataclass
class SitePlan:
    sites: list[Site] = field(default_factory=list)
    interfaces: list[Interface] = field(default_factory=list)
    #: Processes matching no site. Named, never folded into "external": an unassigned
    #: refinery is exactly how a supplier goes missing from a hand reconciliation.
    unassigned: list[dict] = field(default_factory=list)
    #: Processes matching more than one site, with the sites that claimed them. A
    #: machine cannot be in two places, and first-wins would hide the ambiguity.
    contested: list[tuple[str, list[str]]] = field(default_factory=list)
    #: Items some site consumes that no site produces and no extractor supplies.
    unsupplied: list[tuple[str, str]] = field(default_factory=list)
    #: Declared sites that matched no process at all, and patterns that matched none. A
    #: site silently coming back empty is how a whole building's flows vanish from the
    #: interface table.
    empty: list[str] = field(default_factory=list)
    dead_patterns: list[tuple[str, str]] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not (self.unassigned or self.contested or self.unsupplied or self.empty)


def _matches(proc: dict, pattern: str) -> bool:
    """Same widening match as ``exclude_recipes``: label, building name, building id, plus
    MW/power for every generator, since power is a product a site can be defined by.

    A synthesised process such as "Fuel-Powered Generator" has no recipe at all, so
    matching on the recipe set alone would leave every generator unassignable.
    """
    needle = pattern.strip().casefold()
    if not needle:
        return False
    if is_power(needle):
        # Exact token, not substring: "power" as a substring would also claim every
        # "Coal-Powered Generator" LABEL -- true but redundant -- and, worse, any future
        # process whose label merely contains the word.
        return proc.get("kind") == "generator"
    return any(
        needle in str(proc.get(field_) or "").casefold()
        for field_ in ("label", "building", "building_id", "recipe")
    )


def claim_processes(processes: list[dict], spec: dict[str, list[str]]) -> dict[str, list[str]]:
    """Which sites claim each process: pid -> owner names, in spec order.

    The one matching pass, shared by ``partition`` and by the per-site layout: two matchers
    would disagree about exactly the processes a caller cares where they stand.
    """
    return {
        proc["pid"]: [
            name for name, patterns in spec.items() if any(_matches(proc, p) for p in patterns)
        ]
        for proc in processes
    }


def partition(
    prepared,
    game: GameData,
    spec: dict[str, list[str]],
) -> SitePlan:
    """Cut a solved plan into named sites and report what crosses between them.

    ``spec`` maps a site name to patterns, matched exactly as ``exclude_recipes`` matches:
    against the process label, its building, or its recipe.
    """
    out = SitePlan()
    if prepared.solution is None:
        out.notes.append("no solved plan to partition")
        return out

    processes = prepared.solution.processes
    claims = claim_processes(processes, spec)
    for proc in processes:
        owners = claims[proc["pid"]]
        if not owners:
            out.unassigned.append(proc)
        elif len(owners) > 1:
            out.contested.append((proc["label"], owners))

    for name, patterns in spec.items():
        site = Site(name=name, patterns=list(patterns))
        site.slice = slice_of(
            prepared,
            game,
            keep=lambda p, n=name: claims.get(p["pid"], []) == [n],
            label=name,
        )
        out.sites.append(site)

    # An item's producers and consumers across sites. The plan is mass-balanced by the
    # LP's equality rows, so within a COMPLETE partition every consumed item has a
    # producer; the interesting output is where each flow lands, not whether it balances.
    sc = prepared.request.scenario
    for item in sorted({i for s in out.sites if s.slice for i in s.slice.flows}):
        makers = [
            (s.name, s.slice.flows[item])
            for s in out.sites
            if s.slice and s.slice.flows.get(item, 0.0) > _EPS
        ]
        takers = [
            (s.name, -s.slice.flows[item])
            for s in out.sites
            if s.slice and s.slice.flows.get(item, 0.0) < -_EPS
        ]
        if not takers:
            continue
        if not makers:
            for taker, _ in takers:
                out.unsupplied.append((taker, game.item_name(item)))
            continue
        # Proportional attribution. The LP gives net balances, never who fed whom -- the
        # same reason `layout` models a bus rather than producer-consumer pairs. Splitting
        # a shared flow by share is the only answer that is not invented.
        supply = sum(v for _, v in makers)
        for taker, wanted in takers:
            for maker, made in makers:
                rate = wanted * (made / supply) if supply else 0.0
                if maker == taker:
                    continue
                line = carrier_for(game, item, sc.belt_ipm, sc.pipe_m3min)
                out.interfaces.append(
                    Interface(
                        item=item,
                        name=game.item_name(item),
                        source=maker,
                        target=taker,
                        rate=rate,
                        carrier=line.kind,
                        lines=line.lines_for(rate),
                    )
                )

    # Both loudly: a site or a pattern that quietly matches nothing takes every flow it
    # should have carried out of the interface table, leaving a plausible table that is
    # short by a whole building.
    for site in out.sites:
        if site.machines == 0:
            out.empty.append(site.name)
        for pattern in site.patterns:
            if not any(_matches(proc, pattern) for proc in processes):
                out.dead_patterns.append((site.name, pattern))

    out.interfaces.sort(key=lambda i: -i.rate)
    for name in out.empty:
        out.notes.append(
            f"site {name!r} matched NO process, so nothing it should contain is in the "
            "interface table. Patterns match a process LABEL, its building or its recipe "
            "-- plus 'MW' or 'power', which claims every generator -- never any other "
            "item a process produces"
        )
    for name, pattern in out.dead_patterns[:4]:
        out.notes.append(f"{name}: pattern {pattern!r} matches nothing in this plan")
    if out.unassigned:
        out.notes.append(
            f"{len(out.unassigned)} process(es) match no site: "
            + ", ".join(sorted({p["label"] for p in out.unassigned})[:5])
            + ". Their flows are NOT in the interface table, so it is incomplete -- an "
            "unassigned producer is exactly how a supplier goes missing"
        )
    for label, owners in out.contested[:4]:
        out.notes.append(f"{label} matches {' and '.join(owners)}; a machine is in one place")
    for site, item in out.unsupplied[:4]:
        out.notes.append(f"{site} consumes {item} and no site produces it")
    return out

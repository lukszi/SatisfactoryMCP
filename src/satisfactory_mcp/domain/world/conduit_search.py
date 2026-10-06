"""The conduit runs near a place, between two places, or by fluid network.

``search_conduits`` and ``/api/world/conduits`` both answer from here, over the runs
``conduits.build_runs`` makes; docs/mcp-surface.md §10.1f has the decisions.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from ..spatial.places import resolve_place
from .conduits import NEAR_RADIUS_M, ConduitRun

__all__ = ["KINDS", "ConduitSearch", "NetworkView", "networks", "search"]

KINDS = ("belt", "pipe")


@dataclass
class ConduitSearch:
    """The runs near one place, or between two, as ``search_conduits`` and the page list them."""

    origin: tuple[float, float] | None = None
    where: str = ""
    second: tuple[float, float] | None = None
    where_to: str = ""
    radius_m: float = 0.0
    to_radius_m: float | None = None
    kind: str | None = None
    network: int | None = None
    run: str | None = None
    hits: list[ConduitRun] = field(default_factory=list)
    bridged: list[str] = field(default_factory=list)
    error: str | None = None

    @property
    def belts(self) -> list[ConduitRun]:
        return [r for r in self.hits if r.kind != "pipe"]

    @property
    def pipes(self) -> list[ConduitRun]:
        return [r for r in self.hits if r.kind == "pipe"]


def search(
    st,
    near: str,
    radius_m: float = NEAR_RADIUS_M,
    to: str | None = None,
    to_radius_m: float | None = None,
    kind: str | None = None,
    network: int | None = None,
    run: str | None = None,
) -> ConduitSearch:
    """Runs within ``radius_m`` of ``near`` (and of ``to``), longest first.

    ``kind`` is ``belt``, ``pipe`` or ``None`` for both. ``network`` lists every pipe of one
    fluid network and ``run`` one run by its ident; both ignore the radii, and distance is
    still measured from ``near``.
    """
    out = ConduitSearch(radius_m=radius_m, kind=kind, network=network, run=run)
    try:
        out.origin, out.where = resolve_place(st, near)
        if to is not None:
            out.second, out.where_to = resolve_place(st, to)
    except ValueError as exc:
        out.error = f"! {exc}"
        return out
    if out.second is not None:
        out.to_radius_m = to_radius_m if to_radius_m is not None else radius_m
    runs = st.conduit_runs
    if run is not None:
        want = run.strip().casefold()
        out.hits = [r for r in runs if r.ident.casefold() == want]
        if not out.hits:
            out.error = f"! no conduit run called {run!r}; search_conduits lists the ids it takes"
        return out
    if network is not None:
        out.hits = sorted(
            (r for r in runs if r.kind == "pipe" and r.network == network),
            key=lambda r: -r.length_m,
        )
        return out

    bridged: dict[int, dict] = {}
    direct_nets: set[int] = set()
    for r in runs:
        if kind is not None and (r.kind == "pipe") != (kind == "pipe"):
            continue
        near_a = r.dist_m(*out.origin) <= radius_m
        if out.second is None:
            if near_a:
                out.hits.append(r)
            continue
        near_b = r.dist_m(*out.second) <= out.to_radius_m
        if near_a and near_b:
            out.hits.append(r)
            if r.network is not None:
                direct_nets.add(r.network)
        elif r.network is not None and (near_a or near_b):
            entry = bridged.setdefault(r.network, {"fluid": r.fluid, "a": 0, "b": 0})
            entry["a"] += near_a
            entry["b"] += near_b
    out.hits.sort(key=lambda r: -r.length_m)

    game = st.game
    out.bridged = [
        f"pipe network {net} ({game.item_name(entry['fluid']) if entry['fluid'] else '?'}) "
        f"touches BOTH areas -- one connected plumbing system, {entry['a']} piece(s) near "
        f"{out.where} and {entry['b']} near {out.where_to}, though no single piece spans both"
        for net, entry in sorted(bridged.items())
        if entry["a"] and entry["b"] and net not in direct_nets
    ]
    if out.second is not None and kind != "pipe" and (out.hits or out.bridged):
        out.bridged.append(
            "a belt route through a splitter is several chains, so a chain near only one "
            "end may still continue to the other -- follow its connects column, or "
            "trace_upstream from the machine it feeds"
        )
    return out


@dataclass
class NetworkView:
    """One fluid network: what it carries, how much pipe, where, and what it ends on."""

    network: int | None
    fluid: str | None
    runs: list[ConduitRun]
    length_m: float
    centre: tuple[float, float]
    z_min_m: float
    z_max_m: float
    distance_m: float
    touches: list[str]

    @property
    def pieces(self) -> int:
        return len(self.runs)


def networks(st, origin: tuple[float, float]) -> list[NetworkView]:
    """Every fluid network in the world, most pipe first, placed relative to ``origin``."""
    grouped: dict[object, list[ConduitRun]] = {}
    for r in st.conduit_runs:
        if r.kind == "pipe":
            grouped.setdefault(r.network, []).append(r)
    out = []
    for net, runs in sorted(grouped.items(), key=lambda kv: -sum(r.length_m for r in kv[1])):
        ends = [e for r in runs for e in (r.a, r.b)]
        touches: list[str] = []
        for r in runs:
            for name in (r.a.plugs, r.b.plugs, *r.via):
                if name and not name.startswith(("pipe:", "chain:")) and name not in touches:
                    touches.append(name)
        out.append(
            NetworkView(
                network=net,
                fluid=next((r.fluid for r in runs if r.fluid), None),
                runs=runs,
                length_m=sum(r.length_m for r in runs),
                centre=(sum(e.x for e in ends) / len(ends), sum(e.y for e in ends) / len(ends)),
                z_min_m=min(r.z_min_m for r in runs),
                z_max_m=max(r.z_max_m for r in runs),
                distance_m=min(r.dist_m(*origin) for r in runs),
                touches=touches,
            )
        )
    return out

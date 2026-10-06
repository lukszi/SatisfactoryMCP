"""Which machines in the save count as built for a stored plan, found at the plan's site.

Position picks seed machines; the coherence clusters decide membership; a machine another
named factory holds never counts. docs/planner_p4.md, "How built is found", has the rules,
the thresholds and the measurements behind them.
"""

from __future__ import annotations

import math
from collections import Counter
from dataclasses import dataclass, field

from ....core.gamedata.model import GameData
from ....core.text import plural
from ...factories import naming
from ...factories.resolve import resolve_factory
from ...factories.select import SelectorError
from ...spatial import geo
from ...spatial.origin import parse_near, resolve_origin
from ...world.state import WorldState
from .. import siting as siting_mod
from ..solver.prepare import PreparedPlan
from ..stored.planlog import PlanState
from .diff import DiffReport, _group_processes, machine_rate

__all__ = [
    "AUTO",
    "NONE",
    "SENTINELS",
    "WORLD",
    "BuiltAt",
    "Candidate",
    "SearchArea",
    "detect",
    "fill_progress",
    "mode_of",
    "plan_radius_m",
    "search_area",
]

WORLD = "/world"
NONE = "/none"
AUTO = ""
SENTINELS = (WORLD, NONE)

#: Smallest search circle around a plan's site, and the margin past its estimated extent.
SITE_MIN_RADIUS_M = 100.0
SITE_MARGIN_M = 50.0
#: Ground per machine footprint once belts and walkways are counted.
GROUND_PER_FOOTPRINT = 2.0

SURE_SHARE = 0.8
RIVAL_SHARE = 0.2
RIVAL_FIT = 0.5
THIN_SHARE = 0.1


@dataclass(frozen=True)
class SearchArea:
    """Where a plan's seeds may stand. Circles are (x_cm, y_cm, radius_m)."""

    kind: str
    circles: tuple[tuple[float, float, float], ...] = ()
    siting: siting_mod.Siting | None = None
    margin_m: float = 0.0
    node_ids: frozenset[str] = frozenset()
    words: str = ""

    def contains(self, x_cm: float, y_cm: float) -> bool:
        if self.siting is not None and self.siting.has_footprint:
            s = self.siting
            wide = siting_mod.Siting(
                s.x_m,
                s.y_m,
                None,
                s.yaw_deg,
                s.width_m + 2 * self.margin_m,
                s.depth_m + 2 * self.margin_m,
            )
            return wide.contains_cm(x_cm, y_cm)
        return any(geo.distance_m((cx, cy), (x_cm, y_cm)) <= r for cx, cy, r in self.circles)


@dataclass
class Candidate:
    """One owner of counted machines: a named factory or an unnamed cluster."""

    kind: str
    name: str
    proposal: int | None
    machines: list[str]
    rate_share: float
    fit: float = 0.0
    bbox_m: list[float] | None = None


@dataclass
class BuiltAt:
    """Where a plan's built machines were found, how sure that is, and the progress."""

    mode: str
    confidence: str = ""
    area: SearchArea | None = None
    scope: set[str] | None = None
    scope_low: set[str] | None = None
    candidates: list[Candidate] = field(default_factory=list)
    picked: str = ""
    foreign: list[tuple[str, int]] = field(default_factory=list)
    also_here: list[str] = field(default_factory=list)
    node_owner: str = ""
    hint: str = ""
    fallback: str = ""
    built: int | None = None
    built_max: int | None = None
    total: int = 0
    percent: float | None = None
    percent_max: float | None = None
    missing: list[str] = field(default_factory=list)

    @property
    def top(self) -> Candidate | None:
        return self.candidates[0] if self.candidates else None

    def where(self) -> str:
        """The page's one line, without the progress figure."""
        top = self.top
        if self.mode == "world":
            return "counting the whole world"
        if self.mode == "none":
            return "marked as nothing built yet"
        if self.mode == "picked":
            return f"built at “{self.picked}” (picked)"
        if self.confidence == "no site":
            return "not placed: pick a spot or a factory to count what is built"
        if self.confidence == "unsure":
            return "which factory is this plan?"
        if self.confidence == "nothing" or top is None:
            line = "nothing of this plan stands at its site"
            return line + (f" · {self.node_owner}" if self.node_owner else "")
        named = top.kind == "factory"
        line = f"built at “{top.name}”" if named else f"built at unnamed cluster “{top.name}”"
        return line + (" · found automatically" if self.confidence == "likely" else "")

    def figure(self) -> str:
        """``12 / 16``, ``12–16 / 20`` or ``–``."""
        if self.built is None:
            return "–"
        if self.built_max is not None and self.built_max != self.built:
            return f"{self.built}–{self.built_max} / {self.total}"
        return f"{self.built} / {self.total}"

    def details(self) -> list[str]:
        out = []
        if self.missing:
            out.append("missing: " + ", ".join(self.missing))
        if self.also_here:
            out.append("also here: " + ", ".join(self.also_here))
        if self.foreign:
            out.append(
                "; ".join(
                    f"{n} matching {plural('machine', n)} nearby belong to “{name}”"
                    for name, n in self.foreign
                )
            )
        if self.node_owner and self.confidence != "nothing":
            out.append(self.node_owner)
        return out

    def tool_text(self) -> str:
        """The chat line: where, how sure, and the figure."""
        line = self.where()
        if self.built is not None:
            line += f" · {self.figure()} machines built"
            if self.percent is not None:
                pct = f"{self.percent:.0f}%"
                if self.percent_max is not None and round(self.percent_max) != round(self.percent):
                    pct = f"{self.percent:.0f}–{self.percent_max:.0f}%"
                line += f" ({pct} of the rate)"
        if self.confidence == "unsure":
            names = [c.name for c in self.candidates if c.kind == "factory"]
            line += " candidates: " + ", ".join(f"“{n}”" for n in names)
        return line


# ------------------------------------------------------------------ mode


def mode_of(value: str | None) -> str:
    """ "auto", "world", "none" or "picked" for a stored ``factory`` value."""
    text = (value or "").strip()
    if not text:
        return "auto"
    if text == WORLD:
        return "world"
    if text == NONE:
        return "none"
    return "picked"


# ------------------------------------------------------------------ the area


def plan_radius_m(g: GameData, prepared: PreparedPlan) -> float:
    """How far a plan of this size reaches from its site: the circle round the square
    its machines' footprints need, plus a margin."""
    foundations = 0
    for proc in prepared.solution.processes:
        if proc["kind"] == "extractor":
            continue
        b = g.buildings.get(proc.get("building_id") or "")
        fp = getattr(b, "footprint", None) if b is not None else None
        foundations += int(proc["machines"]) * (fp.foundations if fp is not None else 1)
    side = 8.0 * math.ceil(math.sqrt(max(1.0, foundations * GROUND_PER_FOOTPRINT)))
    return max(SITE_MIN_RADIUS_M, side / math.sqrt(2.0) + SITE_MARGIN_M)


def _leaf(text: str) -> str:
    return str(text or "").rsplit(".", 1)[-1]


def search_area(
    g: GameData, st: WorldState, state: PlanState, prepared: PreparedPlan
) -> SearchArea | None:
    """Where this plan's machines would stand, or None when it has no single spot."""
    radius = plan_radius_m(g, prepared)
    sit = siting_mod.parse(state)
    if sit is not None and sit.has_footprint:
        return SearchArea("footprint", siting=sit, margin_m=SITE_MARGIN_M, words="its pad")
    if sit is not None:
        centre = (sit.x_m * 100.0, sit.y_m * 100.0)
        return SearchArea("circle", ((*centre, radius),), words=f"{radius:,.0f} m around its site")
    args = state.args
    raw = args.get("sources") if isinstance(args, dict) else args.sources
    sources = [str(s).strip() for s in (raw or [])]
    near = [s for s in sources if s.casefold().startswith("near:")]
    if near and len(near) == len(sources):
        circles = []
        for term in near:
            try:
                place, _reach = parse_near(term[5:])
                point, _label = resolve_origin(st, place)
            except (ValueError, KeyError):
                continue
            circles.append((point[0], point[1], radius))
        if circles:
            x, y = circles[0][0] / 100.0, circles[0][1] / 100.0
            return SearchArea(
                "circle", tuple(circles), words=f"{radius:,.0f} m around {x:,.0f}, {y:,.0f}"
            )
        return None
    if sources and all(s.casefold().startswith("node:") for s in sources):
        rows = [r for r in prepared.request.node_rows if r.get("kind") == "node"]
        circles = tuple((float(r["x"]), float(r["y"]), radius) for r in rows)
        ids = frozenset(_leaf(r["instance"]) for r in rows)
        if circles:
            return SearchArea(
                "nodes",
                circles,
                node_ids=ids,
                words=f"around its {len(rows)} {plural('node', len(rows))}",
            )
    return None


# ------------------------------------------------------------------ detection


@dataclass
class _Rec:
    leaf: str
    group: str
    cls: str
    key: tuple
    rate: float
    xy: tuple[float, float] | None
    node: str


def _records(st: WorldState) -> dict[str, _Rec]:
    out: dict[str, _Rec] = {}
    for group in ("machines", "generators", "extractors"):
        for r in st.projection.get(group, ()):
            leaf = _leaf(r.get("instance"))
            if group == "machines":
                key: tuple = ("recipe", r["cls"], r.get("recipe") or "")
            elif group == "generators":
                key = ("generator", r["cls"])
            else:
                key = ("extractor", r["cls"])
            pos = r.get("pos")
            out[leaf] = _Rec(
                leaf,
                group,
                r["cls"],
                key,
                machine_rate(r),
                (pos[0], pos[1]) if pos else None,
                _leaf(r.get("node") or ""),
            )
    return out


def _coverage(have: dict[tuple, float], want: dict[tuple, float]) -> tuple[float, float]:
    """(share of the plan's rate covered, overlap over union)."""
    total = sum(want.values()) or 1.0
    overlap = sum(min(have.get(k, 0.0), v) for k, v in want.items())
    surplus = sum(max(0.0, v - want.get(k, 0.0)) for k, v in have.items())
    return overlap / total, overlap / (total + surplus)


def _bbox_m(recs: list[_Rec]) -> list[float] | None:
    points = [r.xy for r in recs if r.xy is not None]
    if not points:
        return None
    xs, ys = [p[0] / 100.0 for p in points], [p[1] / 100.0 for p in points]
    return [round(min(xs), 1), round(min(ys), 1), round(max(xs), 1), round(max(ys), 1)]


def _cluster_names(st: WorldState, wanted: set[int]) -> dict[int, str]:
    if not wanted:
        return {}
    # Every proposal is named at once (~300 ms); kept per state and labels version.
    memo = st.__dict__.setdefault("_proposal_names", {})
    names = memo.get(st.labels.version)
    if names is None:
        names = memo[st.labels.version] = naming.proposal_names(st, st.proposals)
    return {i: names.get(i, f"cluster {i}") for i in wanted}


def _rates(recs: list[_Rec]) -> dict[tuple, float]:
    have: dict[tuple, float] = {}
    for r in recs:
        if r.group != "extractors":
            have[r.key] = have.get(r.key, 0.0) + r.rate
    return have


def _auto(g: GameData, st: WorldState, prepared: PreparedPlan, area: SearchArea) -> BuiltAt:
    groups = _group_processes(prepared.solution)
    want = {gr["key"]: gr["clock"] for gr in groups if gr["kind"] != "extractor"}
    extractor_classes = {gr["building_id"] for gr in groups if gr["kind"] == "extractor"}
    recs = _records(st)
    out = BuiltAt(mode="auto", area=area, confidence="nothing", scope=set())

    seeds = [
        r
        for r in recs.values()
        if r.group != "extractors" and r.key in want and r.xy is not None and area.contains(*r.xy)
    ]
    plan_nodes = {
        _leaf(row["instance"]) for row in prepared.request.node_rows if row.get("kind") == "node"
    }
    on_nodes = [r for r in recs.values() if r.group == "extractors" and r.node in plan_nodes]
    if not seeds and not on_nodes:
        return out

    label_of = {m: label.name for label in st.labels.labels for m in label.anchors}
    proposals = st.proposals
    prop_of = {m: i for i, pr in enumerate(proposals) for m in pr.machines}
    makes = {
        prop_of[r.leaf] for r in recs.values() if r.group != "extractors" and r.leaf in prop_of
    }

    def owner(leaf: str) -> tuple:
        if leaf in label_of:
            return ("factory", label_of[leaf])
        if leaf in prop_of:
            return ("cluster", prop_of[leaf])
        return ("loose", leaf)

    # Seeds grow into their clusters; extractors never grow one.
    grown = sorted({prop_of[s.leaf] for s in seeds if s.leaf in prop_of})
    counted = {s.leaf for s in seeds}
    for i in grown:
        counted |= {
            m
            for m in proposals[i].machines
            if m in recs and recs[m].group != "extractors" and recs[m].key in want
        }

    by_owner: dict[tuple, list[_Rec]] = {}
    for m in sorted(counted):
        by_owner.setdefault(owner(m), []).append(recs[m])
    ranked = []
    for o, members in by_owner.items():
        share, fit = _coverage(_rates(members), want)
        ranked.append((o, members, share, fit))
    ranked.sort(key=lambda c: (-c[3], -c[2], -len(c[1]), str(c[0][1])))
    top_owner = ranked[0][0] if ranked else None

    # A machine another named factory holds never counts.
    def ours(m: str) -> bool:
        return m not in label_of or owner(m) == top_owner

    kept = {m for m in counted if ours(m)}
    excluded = counted - kept

    kept_owners = {owner(m) for m in kept}
    fed: dict[tuple, list[_Rec]] = {}
    for r in on_nodes:
        o = owner(r.leaf)
        standalone = o[0] == "loose" or (o[0] == "cluster" and o[1] not in makes)
        if o in kept_owners or standalone:
            kept.add(r.leaf)
            if not ranked:
                by_owner.setdefault(o, []).append(r)
        else:
            fed.setdefault(o, []).append(r)
    for i in grown:
        kept |= {
            m
            for m in proposals[i].machines
            if m in recs
            and recs[m].group == "extractors"
            and recs[m].cls in extractor_classes
            and ours(m)
        }
    if not ranked:
        ranked = [
            (o, members, 0.0, 0.0)
            for o, members in sorted(by_owner.items(), key=lambda kv: -len(kv[1]))
        ]

    also: Counter = Counter()
    for i in grown:
        for m in proposals[i].machines:
            rec = recs.get(m)
            if rec is not None and rec.group != "extractors" and rec.key not in want and ours(m):
                also[g.building_name(rec.cls) or rec.cls] += 1

    clusters = {int(o[1]) for o, *_ in ranked if o[0] == "cluster"}
    clusters |= {int(o[1]) for o in fed if o[0] == "cluster"}
    names = _cluster_names(st, clusters)

    def display(o: tuple) -> str:
        if o[0] == "factory":
            return str(o[1])
        if o[0] == "cluster":
            return names.get(int(o[1]), f"cluster {o[1]}")
        return "a loose machine"

    out.candidates = [
        Candidate(
            kind="factory" if o[0] == "factory" else "cluster",
            name=display(o),
            proposal=int(o[1]) if o[0] == "cluster" else None,
            machines=[r.leaf for r in members],
            rate_share=round(share, 3),
            fit=round(fit, 3),
            bbox_m=_bbox_m(members),
        )
        for o, members, share, fit in ranked
    ]
    out.foreign = sorted(
        Counter(label_of[m] for m in excluded).items(), key=lambda kv: (-kv[1], kv[0])
    )
    out.also_here = [
        f"{n} {plural(name, n)}"
        for name, n in sorted(also.items(), key=lambda kv: (-kv[1], kv[0]))[:3]
    ]
    resource = {_leaf(row["instance"]): row["resource"] for row in prepared.request.node_rows}
    out.node_owner = "; ".join(
        f"its {len(rows)} {(g.item_name(resource.get(rows[0].node, '')) or 'resource').lower()} "
        f"{plural('node', len(rows))} already {'feeds' if len(rows) == 1 else 'feed'} "
        f"“{display(o)}”"
        for o, rows in sorted(fed.items(), key=lambda kv: (-len(kv[1]), str(kv[0][1])))
    )
    if not kept:
        return out

    top = out.candidates[0]
    kept_share, _ = _coverage(_rates([recs[m] for m in kept]), want)
    excluded_share, _ = _coverage(_rates([recs[m] for m in excluded]), want)
    rivals = [
        c
        for c in out.candidates[1:]
        if c.kind == "factory" and c.rate_share >= RIVAL_SHARE and c.fit >= RIVAL_FIT * top.fit
    ]
    thin = top.kind == "factory" and top.rate_share < THIN_SHARE
    if rivals or thin or excluded_share > kept_share:
        out.confidence = "unsure"
        out.scope_low = {m for m in kept if m not in label_of}
        out.scope = set(kept).union(*(set(c.machines) for c in rivals))
        return out
    sure = (
        top.kind == "factory"
        and area.kind != "nodes"
        and not excluded
        and top.rate_share >= SURE_SHARE * kept_share
    )
    out.confidence = "sure" if sure else "likely"
    out.scope = kept
    return out


def detect(
    g: GameData,
    st: WorldState,
    state: PlanState,
    prepared: PreparedPlan,
    factory: str | None = None,
) -> BuiltAt:
    """What a stored plan counts as built. ``factory`` overrides the stored value for one
    call; the stored value always wins the count over detection (docs/planner_p4.md)."""
    value = state.factory if factory is None else factory
    mode = mode_of(value)
    if mode == "world":
        return BuiltAt(mode="world", scope=None)
    area = search_area(g, st, state, prepared)
    if mode == "none":
        out = BuiltAt(mode="none", area=area, scope=set())
        seen = _auto(g, st, prepared, area) if area is not None else None
        if seen is not None and seen.scope:
            out.hint = f"auto sees {len(seen.scope)} matching machines here"
        return out
    if mode == "picked":
        try:
            name, machines = resolve_factory(st, value)
        except SelectorError:
            name, machines = value, []
        if machines:
            out = BuiltAt(mode="picked", picked=name, scope=set(machines), area=area)
            seen = _auto(g, st, prepared, area) if area is not None else None
            top = seen.top if seen is not None and seen.scope else None
            if top is not None and top.name != name and top.rate_share >= 0.5:
                out.hint = (
                    f"auto sees {len(top.machines)} matching machines in “{top.name}” at the site"
                )
                out.candidates = seen.candidates
            return out
        fallback = f"“{value}” has no machines left: showing what stands at the site"
    else:
        fallback = ""
    if area is None:
        out = BuiltAt(mode="auto", confidence="no site", scope=None)
    else:
        out = _auto(g, st, prepared, area)
    out.fallback = fallback
    return out


# ------------------------------------------------------------------ progress


def _row_figures(rows) -> tuple[int, int, int, float, float]:
    total = built = built_max = 0
    need_rate = low_rate = high_rate = 0.0
    for r in rows:
        total += r.need
        high = min(r.need, r.have)
        low = min(r.need, r.have if r.have_min is None else r.have_min)
        built += low
        built_max += high
        need_rate += r.need_rate
        high_rate += min(r.need_rate, r.have_rate)
        low_rate += min(
            r.need_rate, r.have_rate if r.have_min is None else r.have_min * r.plan_clock
        )
    return total, built, built_max, low_rate, high_rate


def fill_progress(built: BuiltAt, rep: DiffReport, low: DiffReport | None = None) -> None:
    """Progress figures from the matched diff; ``low`` is the strict set's diff when unsure."""
    total, lo, hi, lo_rate, hi_rate = _row_figures(rep.rows)
    need_rate = sum(r.need_rate for r in rep.rows) or 1.0
    built.total = total
    if built.mode == "auto" and built.confidence == "no site":
        built.built = built.built_max = None
        built.percent = built.percent_max = None
    else:
        if low is not None:
            _t, lo, _h, lo_rate, _hr = _row_figures(low.rows)
        built.built, built.built_max = lo, hi
        built.percent = round(100.0 * lo_rate / need_rate, 1)
        built.percent_max = round(100.0 * hi_rate / need_rate, 1)
    short = sorted((r for r in rep.rows if r.build > 0), key=lambda r: (-r.build, r.process))
    built.missing = [f"{r.build} {plural(r.building, r.build)} ({r.process})" for r in short[:3]]

"""The advisor pass: rows that each state one measured fact and where to look.

docs/advisors_contract.md is the specification: the kinds, their rules, the order and the
ids. The pass only reads; dismiss and snooze are ``store.py``. Order is a fixed tuple, never
a score.
"""

from __future__ import annotations

import hashlib
from collections import Counter
from dataclasses import dataclass, replace

from ...core.singleflight import Singleflight
from ...core.text import plural
from ..factories import health
from ..planning import manage
from ..planning.diff_service import match_scope
from ..planning.planlog import PlanLog, PlanLogError
from ..planning.prepare import prepare
from ..spatial import nodes as nodes_mod
from ..spatial import place, regions
from ..world.headlift import head_lift

__all__ = [
    "HEADROOM_SHARE",
    "KINDS",
    "PER_KIND",
    "SEVERITIES",
    "SEVERITY",
    "TONE",
    "VISIBLE",
    "WORDS",
    "Advisory",
    "Spot",
    "capped",
    "compute",
    "ids_for",
    "key_for",
    "with_ids",
]

SEVERITIES = ("act", "consider", "note")
TONE = {"act": "blocked", "consider": "mid", "note": "muted"}
#: Rank order, which is also the order of the kinds in docs/advisors_contract.md §2.
KINDS = (
    "unconnected",
    "dead_node",
    "starved",
    "power",
    "underclock",
    "headroom",
    "no_recipe",
    "plan",
    "pickups",
    "box_empty",
)
SEVERITY = {
    "unconnected": "act",
    "dead_node": "act",
    "starved": "act",
    "power": "act",
    "underclock": "consider",
    "headroom": "consider",
    "no_recipe": "consider",
    "plan": "consider",
    "pickups": "note",
    "box_empty": "note",
}
#: The chip word of each kind, on the page and in chat.
WORDS = {
    "unconnected": "unconnected",
    "dead_node": "no node",
    "starved": "starved",
    "power": "power",
    "underclock": "underclock",
    "headroom": "headroom",
    "no_recipe": "no recipe",
    "plan": "plan",
    "pickups": "pickups",
    "box_empty": "box empty",
}
VISIBLE = 5
PER_KIND = 3
HEADROOM_SHARE = 0.05
SPOTS_SENT = 50
TEXT_MAX = 90
TOOL_MAX = 160
FULL_CLOCK = 0.999
#: Pickup kinds a row never counts: hard drives are hoarded on purpose (contract §2, K9).
NO_PICKUP = frozenset({"hard_drive", "crashed_drop_pod"})
RARE_FIRST = ("somersloop", "mercer_sphere", "power_slug_purple", "power_slug_yellow")
#: The head-lift model per projection; the entry holds the projection, the key is its id().
_HEADS = Singleflight(maxsize=3)

STORAGE = (
    "Build_StorageContainer",
    "Build_StorageIntegrated",
    "Build_StoragePlayer",
    "Build_CentralStorage",
    "Build_PipeStorageTank",
    "Build_IndustrialTank",
)


@dataclass(frozen=True)
class Spot:
    """One place a row names: a machine (``instance`` its leaf) or a pickup (``instance`` "")."""

    instance: str
    name: str
    x_m: float | None
    y_m: float | None


@dataclass(frozen=True)
class Advisory:
    key: str
    id: str
    kind: str
    severity: str
    subject_kind: str
    subject: str
    text: str
    tool_text: str
    weight: float
    members: tuple[str, ...]
    spots: tuple[Spot, ...]
    bbox_m: tuple[float, float, float, float] | None
    lines: tuple[str, ...]
    next_call: str
    seed: str | None
    reveal: tuple[str, ...]
    plan: str | None
    source: str

    @property
    def tone(self) -> str:
        return TONE[self.severity]

    def rank(self) -> tuple:
        return (
            SEVERITIES.index(self.severity),
            KINDS.index(self.kind),
            -self.weight,
            self.subject,
        )


def key_for(kind: str, subject_kind: str, subject: str) -> str:
    """``<kind>|<subject kind>:<subject>``; a ``|`` inside the subject is written ``%7C``."""
    if subject_kind == "world":
        return f"{kind}|world"
    return f"{kind}|{subject_kind}:{subject.replace('|', '%7C')}"


def ids_for(keys) -> dict[str, str]:
    """``adv:`` plus four hex of sha1(key); keys sharing a prefix get six hex each."""
    keys = sorted(set(keys))
    full = {k: hashlib.sha1(k.encode("utf-8")).hexdigest() for k in keys}
    short = Counter(h[:4] for h in full.values())
    return {k: "adv:" + (h[:4] if short[h[:4]] == 1 else h[:6]) for k, h in full.items()}


def capped(rows: list, visible: int = VISIBLE, per_kind: int = PER_KIND) -> tuple[list, list]:
    """``(shown, rest)``: the first rows a card shows, at most ``per_kind`` of one kind and
    ``visible`` in all, keeping rank order. advice.ts applies the same rule."""
    per: Counter = Counter()
    shown, rest = [], []
    for row in rows:
        per[row.kind] += 1
        (shown if per[row.kind] <= per_kind and len(shown) < visible else rest).append(row)
    return shown, rest


def _leaf(text) -> str:
    return str(text or "").rsplit(".", 1)[-1]


def _cut(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def _bare(cause: str) -> str:
    return cause.split(" (", 1)[0]


def _names(name: str, n: int) -> str:
    head, mark, tail = name.partition(" Mk.")
    return plural(head, n) + mark + tail if mark else plural(name, n)


def _verb(n: int, one: str, many: str) -> str:
    return one if n == 1 else many


def _list(items: list[str], shown: int = 2) -> str:
    out = ", ".join(items[:shown])
    return out + (f" +{len(items) - shown}" if len(items) > shown else "")


def _mw(value: float) -> str:
    return f"{value:,.0f}"


class _World:
    """What every rule reads: one world-wide ``assess``, owners, positions and depths."""

    def __init__(self, st) -> None:
        self.st = st
        self.g = st.game
        self.records: dict[str, tuple[str, dict]] = {}
        for group in ("machines", "extractors", "generators"):
            for r in st.projection.get(group, ()):
                self.records[_leaf(r["instance"])] = (group, r)
        heads = _HEADS.get(
            (id(st.projection), id(self.g)),
            lambda: (st.projection, self.g, head_lift(st.projection, self.g, st.graph)),
        )[2]
        self.report = health.assess(
            "world", list(self.records), self.g, st.projection, st.graph, st.physical, heads
        )
        self.owner: dict[str, str] = {}
        for label in st.labels.labels:
            for anchor in label.anchors:
                self.owner.setdefault(anchor, label.name)
        self.regions = regions.load_regions()
        self._region: dict[str, str] = {}
        self._depth: dict[str, int] = {}
        self._by_name = {self.g.item_name(c): c for c in self.g.items}

    def spot(self, leaf: str) -> Spot:
        group_rec = self.records.get(leaf)
        rec = group_rec[1] if group_rec else {}
        pos = rec.get("pos")
        name = self.g.building_name(rec.get("cls")) or str(rec.get("cls") or "machine")
        if not pos:
            return Spot(leaf, name, None, None)
        return Spot(leaf, name, round(pos[0] / 100.0, 1), round(pos[1] / 100.0, 1))

    def subject(self, leaf: str) -> tuple[str, str]:
        if leaf in self.owner:
            return "factory", self.owner[leaf]
        if leaf not in self._region:
            pos = (self.records.get(leaf, ("", {}))[1]).get("pos")
            name = self.regions.label_for(pos[0], pos[1]).name if pos else None
            self._region[leaf] = name or "no region"
        return "region", self._region[leaf]

    def depth(self, item_name: str) -> int:
        """Steps from a raw resource by standard recipes; lower is rawer."""
        cls = self._by_name.get(item_name)
        if cls is None:
            return 99
        return self._depth_of(cls, set())

    def _depth_of(self, cls: str, seen: set) -> int:
        if cls in self._depth:
            return self._depth[cls]
        item = self.g.items.get(cls)
        if item is not None and item.is_resource:
            self._depth[cls] = 0
            return 0
        if cls in seen:
            return 99
        seen.add(cls)
        best = None
        for r in self.g.recipes.values():
            if r.kind != "part" or r.is_alternate or r.is_event or r.machine is None:
                continue
            if not any(p.item == cls for p in r.products):
                continue
            if any(i.item == cls for i in r.ingredients):
                continue
            step = 1 + max((self._depth_of(i.item, seen) for i in r.ingredients), default=0)
            best = step if best is None else min(best, step)
        seen.discard(cls)
        self._depth[cls] = 0 if best is None else best
        return self._depth[cls]


def _where(subject_kind: str, subject: str) -> str:
    if subject_kind == "factory":
        return f"in “{subject}”"
    return f"in {subject}, outside factories"


def _count(w: _World, leaves: list[str]) -> str:
    classes = {(w.records.get(m, ("", {}))[1]).get("cls") for m in leaves}
    n = len(leaves)
    if len(classes) == 1:
        name = w.g.building_name(next(iter(classes))) or "machine"
        return f"{n} {_names(name, n)}"
    return f"{n} {plural('machine', n)}"


def _bbox(spots) -> tuple[float, float, float, float] | None:
    xs = [s.x_m for s in spots if s.x_m is not None]
    ys = [s.y_m for s in spots if s.y_m is not None]
    if not xs:
        return None
    return (min(xs), min(ys), max(xs), max(ys))


def _health_call(subject_kind: str, subject: str, bbox) -> str:
    if subject_kind == "factory":
        return f'factory_health factory="{subject}"'
    if bbox is None:
        return "factory_health"
    x1, y1, x2, y2 = bbox
    return f'factory_health factory="bbox:{x1 - 10:.0f},{y1 - 10:.0f},{x2 + 10:.0f},{y2 + 10:.0f}"'


def _row(
    w: _World | None,
    kind: str,
    subject_kind: str,
    subject: str,
    *,
    text: str,
    tool_text: str,
    weight: float,
    members: list[str] = (),
    spots: list[Spot] | None = None,
    lines: list[str] = (),
    next_call: str,
    seed: str | None = None,
    reveal: tuple[str, ...] = ("machines",),
    plan: str | None = None,
    source: str,
) -> Advisory:
    if spots is None:
        spots = [w.spot(m) for m in members] if w is not None else []
    return Advisory(
        key=key_for(kind, subject_kind, subject),
        id="",
        kind=kind,
        severity=SEVERITY[kind],
        subject_kind=subject_kind,
        subject=subject,
        text=_cut(text, TEXT_MAX),
        tool_text=_cut(tool_text, TOOL_MAX),
        weight=float(weight),
        members=tuple(members),
        spots=tuple(spots[:SPOTS_SENT]),
        bbox_m=_bbox(spots),
        lines=tuple(_cut(line, TOOL_MAX) for line in lines[:3]),
        next_call=next_call,
        seed=seed,
        reveal=reveal,
        plan=plan,
        source=source,
    )


# ------------------------------------------------------------------ machine rules


def _unconnected(m: health.MachineHealth) -> bool:
    if not m.feeds:
        return False
    by_item: dict[str, list[health.Feed]] = {}
    for f in m.feeds:
        by_item.setdefault(f.item, []).append(f)
    for rows in by_item.values():
        if all(f.verdict == health.NOTHING for f in rows):
            continue
        if any(f.verdict == health.UNFED for f in rows):
            continue
        if rows[0].medium == "pipe" and rows[0].rung == health.CONNECTION:
            continue
        return False
    return True


def _box_fed(m: health.MachineHealth) -> bool:
    return bool(m.feeds) and all(f.far.startswith(STORAGE) for f in m.feeds)


def _grouped(w: _World, leaves) -> dict[tuple[str, str], list[str]]:
    out: dict[tuple[str, str], list[str]] = {}
    for leaf in leaves:
        out.setdefault(w.subject(leaf), []).append(leaf)
    return out


def _items(w: _World, ms: list[health.MachineHealth]) -> list[str]:
    counts = Counter(_bare(c) for m in ms for c in m.cause)
    return sorted(counts, key=lambda i: (w.depth(i), -counts[i], i))


def _machine_rows(w: _World, box_fed: bool) -> list[Advisory]:
    out: list[Advisory] = []
    by_leaf = {m.instance: m for m in w.report.machines}
    starved = [
        m
        for m in w.report.machines
        if m.state == "starved" and w.records[m.instance][0] != "generators"
    ]
    cut = [m for m in starved if _unconnected(m)]
    boxed = [m for m in starved if m not in cut and _box_fed(m)]
    pool = [m for m in starved if m not in cut and m not in boxed]

    for (sk, subject), leaves in sorted(_grouped(w, [m.instance for m in cut]).items()):
        ms = [by_leaf[x] for x in leaves]
        items = _items(w, ms)
        who = _count(w, leaves)
        out.append(
            _row(
                w,
                "unconnected",
                sk,
                subject,
                text=f"nothing brings {_list(items)} to {who} {_where(sk, subject)}",
                tool_text=f"no belt or pipe brings {', '.join(items)} to {who} "
                f"{_where(sk, subject)}",
                weight=len(leaves),
                members=leaves,
                lines=[f"missing: {', '.join(sorted({c for m in ms for c in m.cause}))}"],
                next_call=f"trace_upstream seed={leaves[0]}",
                seed=leaves[0],
                source="health.assess",
            )
        )

    for (sk, subject), leaves in sorted(_grouped(w, [m.instance for m in boxed]).items()):
        if not box_fed:
            break
        who = _count(w, leaves)
        out.append(
            _row(
                w,
                "box_empty",
                sk,
                subject,
                text=f"{who} {_where(sk, subject)} emptied the box that feeds "
                + _verb(len(leaves), "it", "them"),
                tool_text=f"{who} {_where(sk, subject)} starve, fed only from a storage box "
                f"that is empty of {', '.join(_items(w, [by_leaf[x] for x in leaves]))}",
                weight=len(leaves),
                members=leaves,
                next_call=_health_call(sk, subject, _bbox([w.spot(x) for x in leaves])),
                seed=leaves[0],
                source="health.assess",
            )
        )

    pool, under = _underclock(w, pool)
    out += under

    blocked: dict[str, Counter] = {}
    for m in w.report.machines:
        if m.state == "blocked":
            for item in m.cause:
                blocked.setdefault(item, Counter())[w.subject(m.instance)] += 1

    for (sk, subject), leaves in sorted(_grouped(w, [m.instance for m in pool]).items()):
        ms = [by_leaf[x] for x in leaves]
        items = _items(w, ms)
        seed_item = items[0]
        raw = next((i for i in items if blocked.get(i, {}).get((sk, subject))), seed_item)
        held = blocked.get(raw, Counter())
        here = held.get((sk, subject), 0)
        elsewhere = [(s, n) for s, n in held.most_common() if s != (sk, subject)][:2]
        who = _count(w, leaves)
        verb = _verb(len(leaves), "starves", "starve")
        tail = f" · {here} blocked here hold {raw if raw != items[0] else 'it'}" if here else ""
        text = f"{who} {_where(sk, subject)} {verb} of {_list(items)}{tail}"
        if len(text) > TEXT_MAX:
            text = f"{who} {_where(sk, subject)} {verb} of {_list(items, 1)}{tail}"
        seed = next(m.instance for m in ms if seed_item in {_bare(c) for c in m.cause})
        lines = []
        if here or elsewhere:
            parts = [f"{here} here"] if here else []
            parts += [f"{s[1]} {n}" for s, n in elsewhere]
            lines.append(f"blocked holding {raw}: " + " · ".join(parts))
        feeds = Counter(
            f"{f.far_name or 'nothing'} ({f.verdict})"
            for m in ms
            for f in m.feeds
            if f.item == raw or _bare(f.item) == raw
        )
        if feeds:
            lines.append("feed: " + ", ".join(k for k, _ in feeds.most_common(2)))
        lines.append("window: last 300 s before the save")
        out.append(
            _row(
                w,
                "starved",
                sk,
                subject,
                text=text,
                tool_text=f"{who} {_where(sk, subject)} {verb} of {', '.join(items)}"
                + (f"; {here} blocked machines here hold {raw}" if here else ""),
                weight=len(leaves),
                members=leaves,
                lines=lines,
                next_call=_health_call(sk, subject, _bbox([w.spot(x) for x in leaves]))
                + f" then trace_upstream seed={seed}",
                seed=seed,
                source="health.assess",
            )
        )

    for state, kind, words in (
        ("dead node", "dead_node", ("stands on no node", "stand on no node")),
        ("no recipe", "no_recipe", ("has no recipe", "have no recipe")),
    ):
        hit = [m.instance for m in w.report.machines if m.state == state]
        for (sk, subject), leaves in sorted(_grouped(w, hit).items()):
            who = _count(w, leaves)
            phrase = f"{who} {_where(sk, subject)} {_verb(len(leaves), *words)}"
            out.append(
                _row(
                    w,
                    kind,
                    sk,
                    subject,
                    text=phrase,
                    tool_text=phrase,
                    weight=len(leaves),
                    members=leaves,
                    next_call=_health_call(sk, subject, _bbox([w.spot(x) for x in leaves])),
                    seed=leaves[0],
                    source="health.assess",
                )
            )
    return out


def _underclock(w: _World, pool):
    """K6: an extractor below 100 % in a named factory where a machine starves of its item."""
    resource = {_leaf(n["instance"]): n["resource"] for n in nodes_mod.load_nodes().nodes}
    rows, taken = [], set()
    found: dict[str, list[tuple[str, set[str]]]] = {}
    for leaf, (group, rec) in w.records.items():
        if group != "extractors" or float(rec.get("clock") or 1.0) >= FULL_CLOCK:
            continue
        factory = w.owner.get(leaf)
        if factory is None:
            continue
        out = ((rec.get("buffers") or {}).get("out") or {}).get("items") or {}
        made = {w.g.item_name(c) for c in out}
        node = resource.get(_leaf(rec.get("node") or ""))
        if node:
            made.add(w.g.item_name(node))
        found.setdefault(factory, []).append((leaf, made))
    for factory, extractors in sorted(found.items()):
        made = set().union(*(m for _, m in extractors))
        hungry = [
            m
            for m in pool
            if w.owner.get(m.instance) == factory and made & {_bare(c) for c in m.cause}
        ]
        if not hungry:
            continue
        taken |= {m.instance for m in hungry}
        items = sorted(made & {_bare(c) for m in hungry for c in m.cause})
        first = w.records[extractors[0][0]][1]
        name = w.g.building_name(first.get("cls")) or "extractor"
        if len(extractors) == 1:
            lead = f"{name} at {float(first.get('clock') or 1.0):.0%}"
        else:
            lead = f"{len(extractors)} extractors below 100 %"
        who = _count(w, [m.instance for m in hungry])
        verb = _verb(len(hungry), "starves", "starve")
        text = f"{lead} while {who} in “{factory}” {verb} of {_list(items)}"
        leaves = [e for e, _ in extractors] + [m.instance for m in hungry]
        rows.append(
            _row(
                w,
                "underclock",
                "factory",
                factory,
                text=text,
                tool_text=text,
                weight=len(hungry),
                members=leaves,
                next_call=f'factory_health factory="{factory}"',
                seed=hungry[0].instance,
                source="health.assess",
            )
        )
    return [m for m in pool if m.instance not in taken], rows


# ------------------------------------------------------------------ world rules


def _power(w: _World, biomass: bool, headroom: str) -> list[Advisory]:
    pw = w.st.power_report(biomass=biomass)
    out = []
    starved = pw["starved_generators"]
    unwired = pw["unwired_consumers"]
    if starved or unwired:
        parts, members = [], []
        if starved:
            n = len(starved)
            parts.append(
                f"{n} {plural('generator', n)} ran dry ({_mw(pw['starved_generation_mw'])} MW)"
            )
            members += [s["instance"] for s in starved]
        if unwired:
            parts.append(
                f"{unwired} {plural('machine', unwired)} on no power wire "
                f"({_mw(pw['unwired_draw_mw'])} MW)"
            )
            members += list(w.report.unwired)
        text = " · ".join(parts)
        out.append(
            _row(
                w,
                "power",
                "world",
                "world",
                text=text,
                tool_text=text,
                weight=pw["starved_generation_mw"] + pw["unwired_draw_mw"],
                members=members,
                next_call="power_report",
                seed=members[0] if members else None,
                source="power_report",
            )
        )
    gen = pw["generation_mw"]
    free = pw["measured_headroom_mw"] if headroom == "measured" else pw["headroom_mw"]
    if gen > 0 and free < HEADROOM_SHARE * gen:
        word = "headroom now" if headroom == "measured" else "headroom at full rate"
        text = f"{word} is {_mw(free)} MW of {_mw(gen)} MW"
        lines = [f"under {HEADROOM_SHARE:.0%} of generation · stage headroom: {headroom}"]
        if not biomass and pw["biomass_generators"]:
            lines.append(f"{pw['biomass_generators']} biomass burners not counted")
        out.append(
            _row(
                None,
                "headroom",
                "world",
                "world",
                text=text,
                tool_text=text + f", under {HEADROOM_SHARE:.0%} of generation",
                weight=HEADROOM_SHARE * gen - free,
                spots=[],
                lines=lines,
                next_call="power_report",
                reveal=(),
                source="power_report",
            )
        )
    return out


#: (world, plan key, rev, save token, labels version) -> (failure, drift flags, node owner).
_PLANS: dict[tuple, tuple[str, list[str], str]] = {}
_PLANS_MAX = 256


def _plan_facts(st, state) -> tuple[str, list[str], str]:
    status = manage.plan_status(st, state)
    drift = [f for f in status.flags if f.startswith("field ")]
    if status.broken:
        return status.flags[-1], drift, ""
    prepared = prepare(st.game, st, state.kwargs(), diagnose=False)
    if prepared.failure:
        return "it no longer solves", drift, ""
    if not prepared.solution.processes:
        return "", drift, ""
    rep, _ = match_scope(st.game, st, prepared, None, False, stored=state)
    built = rep.built_at
    return "", drift, (built.node_owner if built is not None else "")


def _plans(st) -> list[Advisory]:
    try:
        heads = PlanLog(st.world_id, st.header.get("session_name") or "").heads()
    except (PlanLogError, OSError):
        return []
    out = []
    for state in heads:
        key = (st.world_id, state.key, state.rev, st.token, st.labels.version)
        facts = _PLANS.get(key)
        if facts is None:
            if len(_PLANS) >= _PLANS_MAX:
                _PLANS.clear()
            facts = _PLANS[key] = _plan_facts(st, state)
        failure, drift, owner = facts
        lines = []
        for flag in drift:
            then, _, now = flag[len("field ") :].partition("->")
            lines.append(f"a source selector found {then} nodes when saved, {now} now")
        if owner:
            lines.append(owner)
        if failure:
            lines.insert(0, failure)
        if not lines:
            continue
        head = lines[0]
        out.append(
            _row(
                None,
                "plan",
                "plan",
                state.name,
                text=f"plan “{state.name}”: {head}",
                tool_text=f"plan “{state.name}”: " + "; ".join(lines),
                weight=len(lines),
                spots=[],
                lines=lines[1:],
                next_call=f'diff_vs_save plan="{state.name}"',
                reveal=(),
                plan=state.key,
                source="plan_status" if failure or drift else "built.detect",
            )
        )
    return out


def _singular(label: str) -> str:
    return label.removesuffix("s")


def _pickups(st, spoilers: bool) -> list[Advisory]:
    me = st.player_position()
    if me is None:
        return []
    near = [
        r
        for r in place._pickups_near(st, me[0], me[1])
        if r["category"] not in NO_PICKUP and (spoilers or not r["spoiler"])
    ]
    if not near:
        return []
    pick = near[0]
    for category in RARE_FIRST:
        rare = [r for r in near if r["category"] == category]
        if rare:
            pick = rare[0]
            break
    n = len(near)
    reach = f"{place.PICKUP_REACH_M:.0f} m"
    lead = f"{n} {plural('pickup', n)} within {reach} of where you saved"
    text = f"{lead} · {_singular(pick['label'])} {pick['distance_m']:.0f} m"
    kinds = Counter(_singular(r["label"]) for r in near)
    spots = [
        Spot(
            "",
            _singular(r["label"]),
            round(r["pos"][0] / 100.0, 1),
            round(r["pos"][1] / 100.0, 1),
        )
        for r in near
    ]
    return [
        _row(
            None,
            "pickups",
            "world",
            "world",
            text=text,
            tool_text=lead + ": " + ", ".join(f"{c} {k}" for k, c in kinds.most_common()),
            weight=n,
            members=[f"{r['category']}:{r['name']}" for r in near],
            spots=spots,
            next_call="collected_from_world show=nearest near=me",
            reveal=tuple(sorted({f"pickup: {r['category']}" for r in near})),
            source="place._pickups_near",
        )
    ]


def compute(
    st,
    *,
    biomass: bool = False,
    headroom: str = "measured",
    box_fed: bool = False,
    spoilers: bool = False,
) -> list[Advisory]:
    """Every advisory that fires on this save, ranked, ids not yet assigned (``with_ids``)."""
    w = _World(st)
    rows = _machine_rows(w, box_fed) + _power(w, biomass, headroom) + _plans(st)
    rows += _pickups(st, spoilers)
    return sorted(rows, key=Advisory.rank)


def with_ids(rows: list[Advisory], extra_keys=()) -> list[Advisory]:
    """``rows`` with their ids; ``extra_keys`` (stored keys not firing) join the assignment."""
    ids = ids_for([r.key for r in rows] + list(extra_keys))
    return [replace(r, id=ids[r.key]) for r in rows]

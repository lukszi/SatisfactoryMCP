"""The advisor pass: rows that each state one measured fact and where to look.

docs/advisors_contract.md is the specification: the kinds, their rules, the order and the
ids. The pass only reads; dismiss and snooze are ``store.py``. Order is a fixed tuple, never
a score.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import replace

from ...core.saveio.records import instance_leaf, iter_machine_records
from ...core.singleflight import Singleflight
from ...core.text import ellipsize, plural
from ..factories import health
from ..planning.progress.diff_service import diff_in_scope
from ..planning.solver.prepare import prepare
from ..planning.stored import manage
from ..planning.stored.planlog import PlanLog, PlanLogError
from ..spatial import nodes as nodes_mod
from ..spatial import regions, surroundings
from ..world.headlift import head_lift
from .advisory import SEVERITY, SPOTS_SENT, TEXT_MAX, TOOL_MAX, Advisory, Spot, ids_for, key_for

__all__ = ["HEADROOM_SHARE", "RuleContext", "compute", "plan_heads", "with_ids"]

HEADROOM_SHARE = 0.05
FULL_CLOCK = 0.999
#: Pickup kinds a row never counts: hard drives are hoarded on purpose (contract §2, K9).
NO_PICKUP = frozenset({"hard_drive", "crashed_drop_pod"})
RARE_FIRST = ("somersloop", "mercer_sphere", "power_slug_purple", "power_slug_yellow")
#: Recipe depth of an item no recipe reaches; sorts it after every real one.
UNKNOWN_DEPTH = 99
#: The head-lift model per projection; the entry holds the projection, the key is its id().
_HEAD_LIFT_CACHE = Singleflight(maxsize=3)

STORAGE_CLASS_PREFIXES = (
    "Build_StorageContainer",
    "Build_StorageIntegrated",
    "Build_StoragePlayer",
    "Build_CentralStorage",
    "Build_PipeStorageTank",
    "Build_IndustrialTank",
)


def _cause_item(cause: str) -> str:
    """The item a health cause names, without its ``(detail)``."""
    return cause.split(" (", 1)[0]


def _plural_building_name(name: str, count: int) -> str:
    """``Miner Mk.2`` -> ``Miners Mk.2``: the tier mark stays after the plural."""
    head, mark, tail = name.partition(" Mk.")
    return plural(head, count) + mark + tail if mark else plural(name, count)


def _verb(count: int, one: str, many: str) -> str:
    return one if count == 1 else many


def _short_list(items: list[str], shown: int = 2) -> str:
    out = ", ".join(items[:shown])
    return out + (f" +{len(items) - shown}" if len(items) > shown else "")


def _mw(value: float) -> str:
    return f"{value:,.0f}"


class RuleContext:
    """What every rule reads: one world-wide ``assess``, owners, positions and depths."""

    def __init__(self, st) -> None:
        self.st = st
        self.game = st.game
        self.records: dict[str, tuple[str, dict]] = {}
        for group, leaf, record in iter_machine_records(st.projection):
            self.records[leaf] = (group, record)
        lift = _HEAD_LIFT_CACHE.get(
            (id(st.projection), id(self.game)),
            lambda: (st.projection, self.game, head_lift(st.projection, self.game, st.graph)),
        )[2]
        self.report = health.assess(
            "world", list(self.records), self.game, st.projection, st.graph, st.physical, lift
        )
        self.owner: dict[str, str] = {}
        for label in st.labels.labels:
            for anchor in label.anchors:
                self.owner.setdefault(anchor, label.name)
        self.regions = regions.load_regions()
        self._region: dict[str, str] = {}
        self._depth: dict[str, int] = {}
        self._by_name = {self.game.item_name(c): c for c in self.game.items}

    def spot(self, leaf: str) -> Spot:
        group_record = self.records.get(leaf)
        record = group_record[1] if group_record else {}
        pos = record.get("pos")
        name = self.game.building_name(record.get("cls")) or str(record.get("cls") or "machine")
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
            return UNKNOWN_DEPTH
        return self._depth_of(cls, set())

    def _depth_of(self, cls: str, seen: set) -> int:
        if cls in self._depth:
            return self._depth[cls]
        item = self.game.items.get(cls)
        if item is not None and item.is_resource:
            self._depth[cls] = 0
            return 0
        if cls in seen:
            return UNKNOWN_DEPTH
        seen.add(cls)
        best = None
        for recipe in self.game.recipes.values():
            if (
                recipe.kind != "part"
                or recipe.is_alternate
                or recipe.is_event
                or recipe.machine is None
            ):
                continue
            if not any(p.item == cls for p in recipe.products):
                continue
            if any(i.item == cls for i in recipe.ingredients):
                continue
            step = 1 + max((self._depth_of(i.item, seen) for i in recipe.ingredients), default=0)
            best = step if best is None else min(best, step)
        seen.discard(cls)
        self._depth[cls] = 0 if best is None else best
        return self._depth[cls]


def _where(subject_kind: str, subject: str) -> str:
    if subject_kind == "factory":
        return f"in “{subject}”"
    return f"in {subject}, outside factories"


def _machines_phrase(ctx: RuleContext, leaves: list[str]) -> str:
    """``3 Foundries`` when the machines share a building, else ``3 machines``."""
    classes = {(ctx.records.get(m, ("", {}))[1]).get("cls") for m in leaves}
    count = len(leaves)
    if len(classes) == 1:
        name = ctx.game.building_name(next(iter(classes))) or "machine"
        return f"{count} {_plural_building_name(name, count)}"
    return f"{count} {plural('machine', count)}"


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
    ctx: RuleContext | None,
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
        spots = [ctx.spot(m) for m in members] if ctx is not None else []
    return Advisory(
        key=key_for(kind, subject_kind, subject),
        id="",
        kind=kind,
        severity=SEVERITY[kind],
        subject_kind=subject_kind,
        subject=subject,
        text=ellipsize(text, TEXT_MAX),
        tool_text=ellipsize(tool_text, TOOL_MAX),
        weight=float(weight),
        members=tuple(members),
        spots=tuple(spots[:SPOTS_SENT]),
        bbox_m=_bbox(spots),
        lines=tuple(ellipsize(line, TOOL_MAX) for line in lines[:3]),
        next_call=next_call,
        seed=seed,
        reveal=reveal,
        plan=plan,
        source=source,
    )


# ------------------------------------------------------------------ machine rules


def _unconnected(machine: health.MachineHealth) -> bool:
    if not machine.feeds:
        return False
    by_item: dict[str, list[health.Feed]] = {}
    for feed in machine.feeds:
        by_item.setdefault(feed.item, []).append(feed)
    for feeds in by_item.values():
        if all(f.verdict == health.NOTHING for f in feeds):
            continue
        if any(f.verdict == health.UNFED for f in feeds):
            continue
        if feeds[0].medium == "pipe" and feeds[0].rung == health.CONNECTION:
            continue
        return False
    return True


def _box_fed(machine: health.MachineHealth) -> bool:
    return bool(machine.feeds) and all(
        f.far.startswith(STORAGE_CLASS_PREFIXES) for f in machine.feeds
    )


def _grouped(ctx: RuleContext, leaves) -> dict[tuple[str, str], list[str]]:
    out: dict[tuple[str, str], list[str]] = {}
    for leaf in leaves:
        out.setdefault(ctx.subject(leaf), []).append(leaf)
    return out


def _missing_items_rawest_first(
    ctx: RuleContext, machines: list[health.MachineHealth]
) -> list[str]:
    counts = Counter(_cause_item(c) for m in machines for c in m.cause)
    return sorted(counts, key=lambda item: (ctx.depth(item), -counts[item], item))


def _split_starved(ctx: RuleContext) -> tuple[list, list, list]:
    """Starved non-generators as ``(unconnected, box-fed only, other starved)``."""
    starved = [
        m
        for m in ctx.report.machines
        if m.state == "starved" and ctx.records[m.instance][0] != "generators"
    ]
    unconnected = [m for m in starved if _unconnected(m)]
    box_fed_only = [m for m in starved if m not in unconnected and _box_fed(m)]
    other_starved = [m for m in starved if m not in unconnected and m not in box_fed_only]
    return unconnected, box_fed_only, other_starved


def _unconnected_rows(ctx: RuleContext, unconnected: list, by_leaf: dict) -> list[Advisory]:
    rows = []
    grouped = _grouped(ctx, [m.instance for m in unconnected])
    for (subject_kind, subject), leaves in sorted(grouped.items()):
        machines = [by_leaf[leaf] for leaf in leaves]
        items = _missing_items_rawest_first(ctx, machines)
        who = _machines_phrase(ctx, leaves)
        where = _where(subject_kind, subject)
        rows.append(
            _row(
                ctx,
                "unconnected",
                subject_kind,
                subject,
                text=f"nothing brings {_short_list(items)} to {who} {where}",
                tool_text=f"no belt or pipe brings {', '.join(items)} to {who} {where}",
                weight=len(leaves),
                members=leaves,
                lines=[f"missing: {', '.join(sorted({c for m in machines for c in m.cause}))}"],
                next_call=f"trace_upstream seed={leaves[0]}",
                seed=leaves[0],
                source="health.assess",
            )
        )
    return rows


def _box_empty_rows(ctx: RuleContext, box_fed_only: list, by_leaf: dict) -> list[Advisory]:
    rows = []
    grouped = _grouped(ctx, [m.instance for m in box_fed_only])
    for (subject_kind, subject), leaves in sorted(grouped.items()):
        who = _machines_phrase(ctx, leaves)
        where = _where(subject_kind, subject)
        items = _missing_items_rawest_first(ctx, [by_leaf[leaf] for leaf in leaves])
        rows.append(
            _row(
                ctx,
                "box_empty",
                subject_kind,
                subject,
                text=f"{who} {where} emptied the box that feeds "
                + _verb(len(leaves), "it", "them"),
                tool_text=f"{who} {where} starve, fed only from a storage box "
                f"that is empty of {', '.join(items)}",
                weight=len(leaves),
                members=leaves,
                next_call=_health_call(
                    subject_kind, subject, _bbox([ctx.spot(leaf) for leaf in leaves])
                ),
                seed=leaves[0],
                source="health.assess",
            )
        )
    return rows


def _blocked_by_item(ctx: RuleContext) -> dict[str, Counter]:
    """Blocked machines per item they hold, counted by subject."""
    blocked: dict[str, Counter] = {}
    for machine in ctx.report.machines:
        if machine.state == "blocked":
            for item in machine.cause:
                blocked.setdefault(item, Counter())[ctx.subject(machine.instance)] += 1
    return blocked


def _starved_rows(ctx: RuleContext, other_starved: list, by_leaf: dict) -> list[Advisory]:
    rows = []
    blocked = _blocked_by_item(ctx)
    grouped = _grouped(ctx, [m.instance for m in other_starved])
    for (subject_kind, subject), leaves in sorted(grouped.items()):
        here_key = (subject_kind, subject)
        machines = [by_leaf[leaf] for leaf in leaves]
        items = _missing_items_rawest_first(ctx, machines)
        seed_item = items[0]
        blocking_item = next(
            (item for item in items if blocked.get(item, {}).get(here_key)), seed_item
        )
        blocked_by_subject = blocked.get(blocking_item, Counter())
        here = blocked_by_subject.get(here_key, 0)
        elsewhere = [(s, n) for s, n in blocked_by_subject.most_common() if s != here_key][:2]
        who = _machines_phrase(ctx, leaves)
        where = _where(subject_kind, subject)
        verb = _verb(len(leaves), "starves", "starve")
        held_word = blocking_item if blocking_item != items[0] else "it"
        tail = f" · {here} blocked here hold {held_word}" if here else ""
        text = f"{who} {where} {verb} of {_short_list(items)}{tail}"
        if len(text) > TEXT_MAX:
            text = f"{who} {where} {verb} of {_short_list(items, 1)}{tail}"
        seed = next(m.instance for m in machines if seed_item in {_cause_item(c) for c in m.cause})
        lines = []
        if here or elsewhere:
            parts = [f"{here} here"] if here else []
            parts += [f"{s[1]} {n}" for s, n in elsewhere]
            lines.append(f"blocked holding {blocking_item}: " + " · ".join(parts))
        feeds = Counter(
            f"{f.far_name or 'nothing'} ({f.verdict})"
            for m in machines
            for f in m.feeds
            if f.item == blocking_item or _cause_item(f.item) == blocking_item
        )
        if feeds:
            lines.append("feed: " + ", ".join(k for k, _ in feeds.most_common(2)))
        lines.append("window: last 300 s before the save")
        rows.append(
            _row(
                ctx,
                "starved",
                subject_kind,
                subject,
                text=text,
                tool_text=f"{who} {where} {verb} of {', '.join(items)}"
                + (f"; {here} blocked machines here hold {blocking_item}" if here else ""),
                weight=len(leaves),
                members=leaves,
                lines=lines,
                next_call=_health_call(
                    subject_kind, subject, _bbox([ctx.spot(leaf) for leaf in leaves])
                )
                + f" then trace_upstream seed={seed}",
                seed=seed,
                source="health.assess",
            )
        )
    return rows


def _state_rows(ctx: RuleContext) -> list[Advisory]:
    """The ``dead node`` and ``no recipe`` rows, one per subject."""
    rows = []
    for state, kind, words in (
        ("dead node", "dead_node", ("stands on no node", "stand on no node")),
        ("no recipe", "no_recipe", ("has no recipe", "have no recipe")),
    ):
        hit = [m.instance for m in ctx.report.machines if m.state == state]
        for (subject_kind, subject), leaves in sorted(_grouped(ctx, hit).items()):
            who = _machines_phrase(ctx, leaves)
            phrase = f"{who} {_where(subject_kind, subject)} {_verb(len(leaves), *words)}"
            rows.append(
                _row(
                    ctx,
                    kind,
                    subject_kind,
                    subject,
                    text=phrase,
                    tool_text=phrase,
                    weight=len(leaves),
                    members=leaves,
                    next_call=_health_call(
                        subject_kind, subject, _bbox([ctx.spot(leaf) for leaf in leaves])
                    ),
                    seed=leaves[0],
                    source="health.assess",
                )
            )
    return rows


def _machine_rows(ctx: RuleContext, box_fed: bool) -> list[Advisory]:
    by_leaf = {m.instance: m for m in ctx.report.machines}
    unconnected, box_fed_only, other_starved = _split_starved(ctx)
    rows = _unconnected_rows(ctx, unconnected, by_leaf)
    if box_fed:
        rows += _box_empty_rows(ctx, box_fed_only, by_leaf)
    other_starved, underclock_rows = _underclock(ctx, other_starved)
    rows += underclock_rows
    rows += _starved_rows(ctx, other_starved, by_leaf)
    rows += _state_rows(ctx)
    return rows


def _underclock(ctx: RuleContext, other_starved):
    """K6: an extractor below 100 % in a named factory where a machine starves of its item."""
    resource = {instance_leaf(n["instance"]): n["resource"] for n in nodes_mod.load_nodes().nodes}
    rows, taken = [], set()
    found: dict[str, list[tuple[str, set[str]]]] = {}
    for leaf, (group, record) in ctx.records.items():
        if group != "extractors" or float(record.get("clock") or 1.0) >= FULL_CLOCK:
            continue
        factory = ctx.owner.get(leaf)
        if factory is None:
            continue
        out = ((record.get("buffers") or {}).get("out") or {}).get("items") or {}
        made = {ctx.game.item_name(c) for c in out}
        node = resource.get(instance_leaf(record.get("node") or ""))
        if node:
            made.add(ctx.game.item_name(node))
        found.setdefault(factory, []).append((leaf, made))
    for factory, extractors in sorted(found.items()):
        made = set().union(*(m for _, m in extractors))
        hungry = [
            m
            for m in other_starved
            if ctx.owner.get(m.instance) == factory and made & {_cause_item(c) for c in m.cause}
        ]
        if not hungry:
            continue
        taken |= {m.instance for m in hungry}
        items = sorted(made & {_cause_item(c) for m in hungry for c in m.cause})
        first = ctx.records[extractors[0][0]][1]
        name = ctx.game.building_name(first.get("cls")) or "extractor"
        if len(extractors) == 1:
            lead = f"{name} at {float(first.get('clock') or 1.0):.0%}"
        else:
            lead = f"{len(extractors)} extractors below 100 %"
        who = _machines_phrase(ctx, [m.instance for m in hungry])
        verb = _verb(len(hungry), "starves", "starve")
        text = f"{lead} while {who} in “{factory}” {verb} of {_short_list(items)}"
        leaves = [e for e, _ in extractors] + [m.instance for m in hungry]
        rows.append(
            _row(
                ctx,
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
    return [m for m in other_starved if m.instance not in taken], rows


# ------------------------------------------------------------------ world rules


def _power(ctx: RuleContext, biomass: bool, headroom: str) -> list[Advisory]:
    report = ctx.st.power_report(biomass=biomass)
    out = []
    starved = report["starved_generators"]
    unwired = report["unwired_consumers"]
    if starved or unwired:
        parts, members = [], []
        if starved:
            count = len(starved)
            parts.append(
                f"{count} {plural('generator', count)} ran dry "
                f"({_mw(report['starved_generation_mw'])} MW)"
            )
            members += [s["instance"] for s in starved]
        if unwired:
            parts.append(
                f"{unwired} {plural('machine', unwired)} on no power wire "
                f"({_mw(report['unwired_draw_mw'])} MW)"
            )
            members += list(ctx.report.unwired)
        text = " · ".join(parts)
        out.append(
            _row(
                ctx,
                "power",
                "world",
                "world",
                text=text,
                tool_text=text,
                weight=report["starved_generation_mw"] + report["unwired_draw_mw"],
                members=members,
                next_call="power_report",
                seed=members[0] if members else None,
                source="power_report",
            )
        )
    generation = report["generation_mw"]
    free = report["measured_headroom_mw"] if headroom == "measured" else report["headroom_mw"]
    if generation > 0 and free < HEADROOM_SHARE * generation:
        word = "headroom now" if headroom == "measured" else "headroom at full rate"
        text = f"{word} is {_mw(free)} MW of {_mw(generation)} MW"
        lines = [f"under {HEADROOM_SHARE:.0%} of generation · stage headroom: {headroom}"]
        if not biomass and report["biomass_generators"]:
            lines.append(f"{report['biomass_generators']} biomass burners not counted")
        out.append(
            _row(
                None,
                "headroom",
                "world",
                "world",
                text=text,
                tool_text=text + f", under {HEADROOM_SHARE:.0%} of generation",
                weight=HEADROOM_SHARE * generation - free,
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


def plan_heads(st) -> list:
    """The world's live plan heads; none when the plan log cannot be read."""
    try:
        return PlanLog(st.world_id, st.session_name).heads()
    except (PlanLogError, OSError):
        return []


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
    report, _ = diff_in_scope(st.game, st, prepared, None, False, stored=state)
    built = report.built_at
    return "", drift, (built.node_owner if built is not None else "")


def _plans(st) -> list[Advisory]:
    out = []
    for state in plan_heads(st):
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
        out.append(
            _row(
                None,
                "plan",
                "plan",
                state.name,
                text=f"plan “{state.name}”: {lines[0]}",
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
        for r in surroundings.pickups_near(st, me[0], me[1])
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
    count = len(near)
    reach = f"{surroundings.PICKUP_REACH_M:.0f} m"
    lead = f"{count} {plural('pickup', count)} within {reach} of where you saved"
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
            weight=count,
            members=[f"{r['category']}:{r['name']}" for r in near],
            spots=spots,
            next_call="collected_from_world show=nearest near=me",
            reveal=tuple(sorted({f"pickup: {r['category']}" for r in near})),
            source="surroundings._pickups_near",
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
    ctx = RuleContext(st)
    rows = _machine_rows(ctx, box_fed) + _power(ctx, biomass, headroom) + _plans(st)
    rows += _pickups(st, spoilers)
    return sorted(rows, key=Advisory.rank)


def with_ids(rows: list[Advisory], extra_keys=()) -> list[Advisory]:
    """``rows`` with their ids; ``extra_keys`` (stored keys not firing) join the assignment."""
    ids = ids_for([r.key for r in rows] + list(extra_keys))
    return [replace(r, id=ids[r.key]) for r in rows]

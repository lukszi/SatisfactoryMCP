"""One construction path from tool arguments to a solvable Scenario.

plan_factory, plan_layout and diff_vs_save must describe the same factory for the same
arguments, so the translation lives here once, with the ``plan_id`` that hashes the arguments
together with the save-derived solve inputs (docs/planning.md §8.6). ``chain_scenario`` and
``with_recipes`` derive the variants the analyses solve from it.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field, replace
from typing import TYPE_CHECKING, NamedTuple

from ....core.gamedata.constants import (
    UNLIMITED_RATE,
    WATER_EXTRACTOR_CAP_ASSUMED,
    WATER_EXTRACTOR_KEY,
    WATER_PUMP,
)
from ....core.gamedata.model import GameData
from ....core.gamedata.search import match_recipes, resolve_item
from ... import settings
from ...spatial import nodes as nodes_mod
from ...spatial.nodes.selectors import Selection, select_nodes
from .. import siting as siting_mod
from ..stored.plan_args import inherits_default, is_power
from . import prices as prices_mod
from .model import MW, Scenario
from .processes import build_processes

if TYPE_CHECKING:  # pragma: no cover - import cycle only matters for type checkers
    from ...world.state import WorldState

__all__ = [
    "EXPORT_HELP",
    "ChainScenario",
    "PlanRequest",
    "build_scenario",
    "chain_scenario",
    "select_for",
    "shard_stock",
    "with_recipes",
]

#: Quoted verbatim whenever an export token is refused.
EXPORT_HELP = (
    "exports takes item names or class ids, plus MW/mw/power/Power for grid output. "
    "It REPLACES the default [MW] rather than extending it -- list MW yourself to "
    "export power as well as items."
)

#: Extractors are tried best-first: the first one that can tap a node wins it.
_EXTRACTOR_PREFERENCE = (
    "Build_OilPump_C",
    "Build_MinerMk3_C",
    "Build_MinerMk2_C",
    "Build_MinerMk1_C",
)


def _export_token(game: GameData, name: str) -> tuple[str | None, str | None]:
    """``(item id, None)`` for one export token, or ``(None, why)``; power spellings are MW.

    An unresolvable token is named rather than passed through, where it would be an
    unsatisfiable balance row (docs/planning.md §8.9).
    """
    if is_power(name):
        return MW, None
    resolved = resolve_item(game, name)
    if resolved is None:
        return None, f"no item matches {name!r}"
    return resolved, None


def select_for(game: GameData, state: WorldState, sources: list[str] | None) -> Selection:
    """Resolve a source spec against this world -- the ONE place that wiring lives.

    The world state goes in as ``st``, never as ``origin``, which would turn every direction
    into a cone from the player (docs/planning.md §8.9). ``stored.provenance`` re-resolves
    through here too, so a staleness check measures the field the plan plans over.
    """
    table = nodes_mod.load_nodes()
    return select_nodes(
        sources,
        table.nodes,
        resolve_resource=lambda q: resolve_item(game, q),
        st=state,
    )


@dataclass
class PlanRequest:
    """Everything a planning tool needs, and everything diff_vs_save needs to match."""

    scenario: Scenario
    selection: Selection
    #: In-scope nodes annotated with tapped/tapped_by/reachable; the diff's exact match.
    node_rows: list[dict]
    plan_id: str
    #: Recipes removed by exclude_recipes, and patterns that matched nothing (§8.8).
    excluded: list[str] = field(default_factory=list)
    recipe_errors: list[str] = field(default_factory=list)
    #: Export, minimum and supplied tokens that resolve to no item, dropped and reported.
    export_errors: list[str] = field(default_factory=list)
    #: Every in-scope node before the reachable/tapped filters, so a missing raw is
    #: explainable (docs/planning.md §8.2a).
    scoped_nodes: list[dict] = field(default_factory=list)
    only_free_nodes: bool = False
    #: Recipe ids ``required`` put in force, after the refusals (contract §6).
    required: list[str] = field(default_factory=list)
    #: Where this plan stands, resolved; outside ``plan_id`` because nothing here enters the LP.
    site: siting_mod.Siting | None = None
    #: A ``site_at`` that would not resolve: reported, never raised.
    site_errors: list[str] = field(default_factory=list)
    #: How the horizon, price and overclock switch were resolved: ``inherited``,
    #: ``default_hours``, ``price_source``, ``mix``, ``overclock_inherited``, ``shards``.
    payback: dict = field(default_factory=dict)


def _shared_settings() -> dict:
    try:
        return settings.read()["values"]
    except Exception:
        return {k: spec.default for k, spec in settings.SPECS.items()}


def shard_stock(state) -> dict:
    """Power Shards overclock-last may spend: ``free`` in hand plus ``craftable`` from slugs
    in hand whose shard recipe this save has unlocked (contract §5)."""
    budget = state.shard_budget()
    shards = set(state.game.clock_shards())
    unlocked = {r.cls for r in state.unlocked_recipes("part")}
    best: dict[str, float] = {}
    for rid in unlocked:
        recipe = state.game.recipes.get(rid)
        if recipe is None or len(recipe.ingredients) != 1 or not recipe.ingredients[0].amount:
            continue
        made = sum(f.amount for f in recipe.products if f.item in shards)
        slug = recipe.ingredients[0].item
        if made and slug not in shards:
            best[slug] = max(best.get(slug, 0.0), made / recipe.ingredients[0].amount)
    craftable = sum(row["held"] * best.get(row["item"], 0.0) for row in budget["slugs"])
    return {"free": budget["free"], "craftable": craftable}


def _payback_fields(state, hours, overclock, price, rows: dict) -> tuple[dict, dict]:
    """Scenario fields for the horizon, and how each was resolved (contract §6)."""
    shared = _shared_settings()
    resolved_hours = float(shared["payback_hours"] if inherits_default(hours) else hours)
    overclock_last_on = bool(shared["overclock_last"] if inherits_default(overclock) else overclock)
    prices = prices_mod.prices_for(state, bool(shared["biomass"]))
    stock = shard_stock(state) if overclock_last_on or "last" in rows.values() else None
    fields = {
        "payback_hours": resolved_hours,
        "power_price": prices.power_price if inherits_default(price) else float(price),
        "build_points": prices.build_points,
        "overclock_last": overclock_last_on,
        "overclock_shards": stock["free"] + stock["craftable"] if stock else None,
        "row_overclock": rows,
    }
    info = {
        "inherited": inherits_default(hours),
        "default_hours": float(shared["payback_hours"]),
        "price_source": "grid mix" if inherits_default(price) else "plan",
        "mix": prices.grid_mix,
        "overclock_inherited": inherits_default(overclock),
    }
    return fields, info


def _resolve_exports(
    game: GameData, exports: list[str] | None, export_minimums: dict[str, float] | None
) -> tuple[list[str], dict[str, float], list[str]]:
    """Export ids and minimums by id, plus every token that resolved to nothing."""
    export_ids: list[str] = []
    errors: list[str] = []
    for name in exports or [MW]:
        resolved, err = _export_token(game, name)
        if resolved is None:
            errors.append(f"exports: {err}")
            continue
        export_ids.append(resolved)
    minimums = {}
    for name, value in (export_minimums or {}).items():
        resolved, err = _export_token(game, name)
        if resolved is None:
            errors.append(f"export_minimums: {err}")
            continue
        minimums[resolved] = float(value)
    return export_ids, minimums, errors


def _supplied_caps(
    game: GameData, supplied: dict[str, float] | None, errors: list[str]
) -> dict[str, float]:
    """Items another plan hands this one, as free raw caps (docs/planning.md §8.2i)."""
    raw_caps: dict[str, float] = {}
    for name, rate in (supplied or {}).items():
        resolved, err = _export_token(game, name)
        if resolved is None or resolved == MW:
            errors.append(f"supplied: {err or 'MW cannot be supplied as an item'}")
            continue
        # Slack for a rate another solve rounded to 4 dp (docs/planning.md, solver tolerance).
        rate = float(rate)
        raw_caps[resolved] = rate + max(1e-6, abs(rate) * 1e-6)
    return raw_caps


def _extractor_census(
    game: GameData, state: WorldState, node_rows: list[dict], water_extractors: int | None
) -> dict[tuple[str, str, str], int]:
    """Nodes per ``(extractor, resource, purity)``, each tapped by the best unlocked extractor."""
    extractor_counts: dict[tuple[str, str, str], int] = {}
    for node_row in node_rows:
        if node_row["kind"] != "node" or node_row["rate"] <= 0:
            continue
        for building_cls in _EXTRACTOR_PREFERENCE:
            building = game.buildings.get(building_cls)
            if building is None or building_cls not in state.unlocked_building_ids:
                continue
            if (
                building.allowed_resources
                and node_row["resource"] not in building.allowed_resources
            ):
                continue
            if not building.allowed_resources and game.items[node_row["resource"]].is_fluid:
                continue
            key = (building_cls, node_row["resource"], node_row["purity"])
            extractor_counts[key] = extractor_counts.get(key, 0) + 1
            break
    if WATER_PUMP in state.unlocked_building_ids:
        # An assumed cap stands in for nodes water lacks; zero means no water (§8.2c, §8.2g).
        cap = WATER_EXTRACTOR_CAP_ASSUMED if water_extractors is None else int(water_extractors)
        if cap > 0:
            extractor_counts[WATER_EXTRACTOR_KEY] = cap
    return extractor_counts


class _RecipePool(NamedTuple):
    #: The recipe ids the scenario may use.
    recipes: list[str]
    #: Every unlocked part recipe, before only_recipes and exclude_recipes.
    unlocked: list[str]
    #: Names of the recipes a ban removed.
    excluded: list[str]
    #: Recipe ids ``required`` put in force.
    required: list[str]


def _recipe_pool(
    game: GameData,
    state: WorldState,
    only_recipes: list[str] | None,
    exclude_recipes: list[str] | None,
    required: list[str] | None,
    errors: list[str],
) -> _RecipePool:
    """The unlocked recipes narrowed by ``only_recipes``, banned by ``exclude_recipes`` and
    with ``required`` replacing their rivals (docs/planning.md §8.8, contract §6)."""
    unlocked = [r.cls for r in state.unlocked_recipes("part")]
    recipes = list(unlocked)
    excluded: list[str] = []
    if only_recipes:
        keep: set[str] = set()
        for pattern in only_recipes:
            hits = match_recipes(game, pattern, recipes)
            if not hits:
                errors.append(f"only_recipes: nothing matches {pattern!r}")
            keep.update(hits)
        if keep:
            recipes = [rid for rid in recipes if rid in keep]

    # Each ban is also offered to the synthesised processes, never only the first matcher
    # (docs/planning.md §8.2c); ``build_scenario`` does that half.
    for pattern in exclude_recipes or []:
        hits = match_recipes(game, pattern, recipes)
        if not hits:
            continue
        excluded.extend(game.recipes[rid].name for rid in hits)
        banned = set(hits)
        recipes = [rid for rid in recipes if rid not in banned]

    in_force = _required(game, required, unlocked, exclude_recipes, errors)
    if in_force:
        makes = {game.recipes[rid].main_product for rid in in_force}
        recipes = [
            rid for rid in recipes if rid in in_force or game.recipes[rid].main_product not in makes
        ]
        recipes += [rid for rid in in_force if rid not in recipes]
    return _RecipePool(recipes, unlocked, excluded, in_force)


def _recycle_once_pids(scenario: Scenario, patterns: list[str], errors: list[str]) -> frozenset:
    """Pids whose label matches a ``recycle_once`` pattern, as exclude_recipes widens."""
    processes = build_processes(scenario)
    wanted: set[str] = set()
    for pattern in patterns:
        needle = pattern.strip().casefold()
        hits = {process.pid for process in processes if needle in process.label.casefold()}
        if not hits:
            errors.append(f"recycle_once: nothing matches {pattern!r}")
        wanted |= hits
    return frozenset(wanted)


def _resolve_site(
    state: WorldState, site_at: str, site_footprint: str
) -> tuple[siting_mod.Siting | None, list[str]]:
    if not str(site_at or "").strip():
        return None, []
    try:
        return siting_mod.resolve_plan_site(state, site_at, site_footprint), []
    except ValueError as exc:
        return None, [f"site_at: {exc}"]


def build_scenario(
    game: GameData,
    state: WorldState,
    objective: str = "max_mw",
    target_item: str | None = None,
    sources: list[str] | None = None,
    exports: list[str] | None = None,
    export_minimums: dict[str, float] | None = None,
    only_free_nodes: bool = False,
    allow_sinks: bool = True,
    clocks: list[float] | None = None,
    extractor_clocks: list[float] | None = None,
    machine_cost_mw: float = 5.0,
    #: None means "the fastest tier this save can build" (docs/planning.md §8.5i).
    belt_ipm: float | None = None,
    pipe_m3min: float | None = None,
    exclude_recipes: list[str] | None = None,
    only_recipes: list[str] | None = None,
    water_extractors: int | None = None,
    sloops: int = 0,
    recycle_once: list[str] | None = None,
    supplied: dict[str, float] | None = None,
    required: list[str] | None = None,
    #: Hours, a switch and points per MWh; None or "default" follows the shared settings
    #: and the save's grid mix.
    payback_hours: float | str | None = None,
    overclock_last: bool | str | None = None,
    power_price: float | str | None = None,
    #: Per recipe id, ``"last"`` or ``"spread"``: a row's own overclock-last choice.
    row_overclock: dict[str, str] | None = None,
    #: Where the factory will stand: a measured water note, never an LP input (§8.2c).
    site_at: str = "",
    site_footprint: str = "",
) -> PlanRequest:
    """Translate tool arguments into a Scenario, its node scope and a plan id.

    ``exports`` REPLACES the default ``[MW]`` rather than extending it, because the grid
    import allowance is derived from it (docs/planning.md §8.9).
    """
    export_ids, minimums, export_errors = _resolve_exports(game, exports, export_minimums)
    raw_caps = _supplied_caps(game, supplied, export_errors)

    # The Mk5/Mk2 fallbacks are for a caller with no save to read at all.
    if belt_ipm is None:
        best = state.best_belt()
        belt_ipm = best[1] if best else 780.0
    if pipe_m3min is None:
        best = state.best_pipe()
        pipe_m3min = best[1] if best else 600.0

    selection = select_for(game, state, sources)
    scoped = nodes_mod.annotate(
        selection.nodes, game, state.projection, state.unlocked_building_ids
    )
    node_rows = [node_row for node_row in scoped if node_row["reachable"]]
    if only_free_nodes:
        node_rows = [node_row for node_row in node_rows if not node_row["tapped"]]

    recipe_errors: list[str] = []
    pool = _recipe_pool(game, state, only_recipes, exclude_recipes, required, recipe_errors)
    excluded = list(pool.excluded)
    payback_fields, payback_info = _payback_fields(
        state, payback_hours, overclock_last, power_price, dict(row_overclock or {})
    )
    scenario = Scenario(
        game=game,
        recipes=pool.recipes,
        objective=objective,
        target_item=resolve_item(game, target_item) if target_item else None,
        exports=tuple(export_ids),
        export_minimums=minimums,
        extractor_nodes=_extractor_census(game, state, node_rows, water_extractors),
        raw_caps=raw_caps,
        allow_sinks=allow_sinks,
        clocks=tuple(clocks) if clocks else (1.0,),
        extractor_clocks=tuple(extractor_clocks) if extractor_clocks else None,
        machine_cost_mw=machine_cost_mw,
        belt_ipm=belt_ipm,
        pipe_m3min=pipe_m3min,
        buildings_available=state.unlocked_building_ids,
        # Zero spends none: a fixed number exist on the whole map (docs/planning.md §8.2f).
        sloop_budget=max(0, int(sloops or 0)),
        grid_import_mw=None if MW in export_ids else 1e6,
        **payback_fields,
    )

    if recycle_once:
        scenario = replace(
            scenario, recycle_once=_recycle_once_pids(scenario, recycle_once, recipe_errors)
        )

    if exclude_recipes:
        scenario, process_hits, misses = _ban_processes(scenario, exclude_recipes)
        excluded.extend(process_hits)
        matched_a_recipe = {
            pattern for pattern in exclude_recipes if match_recipes(game, pattern, pool.unlocked)
        }
        for pattern in [m for m in misses if m not in matched_a_recipe]:
            recipe_errors.append(f"exclude_recipes: nothing matches {pattern!r}")

    site, site_errors = _resolve_site(state, site_at, site_footprint)
    return PlanRequest(
        scenario=scenario,
        selection=selection,
        node_rows=node_rows,
        plan_id=_plan_id(scenario, only_free_nodes, pool.required),
        excluded=sorted(set(excluded)),
        recipe_errors=recipe_errors,
        export_errors=export_errors,
        scoped_nodes=scoped,
        only_free_nodes=only_free_nodes,
        site=site,
        site_errors=site_errors,
        required=pool.required,
        payback=payback_info,
    )


def _required(
    game: GameData,
    required: list[str] | None,
    unlocked: list[str],
    bans: list[str] | None,
    errors: list[str],
) -> list[str]:
    """``required`` resolved to recipe ids, each refusal named in ``errors`` (contract §6)."""
    out: list[str] = []
    for entry in required or []:
        rid = entry if entry in game.recipes else None
        if rid is None:
            wanted = entry.strip().casefold()
            named = [r for r, rec in game.recipes.items() if rec.name.casefold() == wanted]
            rid = named[0] if len(named) == 1 else None
        if rid is None:
            errors.append(f"required: {entry!r} is not a recipe")
            continue
        name = game.recipes[rid].name
        if rid not in unlocked:
            errors.append(f"required: {name!r} is not unlocked in this save")
            continue
        ban = next((b for b in bans or [] if rid in match_recipes(game, b, unlocked)), None)
        if ban is not None:
            errors.append(f"required {name!r} is banned by {ban!r}")
            continue
        if rid not in out:
            out.append(rid)
    return out


def _plan_id(sc: Scenario, only_free_nodes: bool, required: list[str] | None = None) -> str:
    """Short hash over everything that can change the solve.

    The save's mtime is excluded: a rotating autosave that changed nothing relevant must
    yield the SAME id, or the id stops meaning "same plan" and starts meaning "same second".
    """
    fields: dict = {"required": sorted(required)} if required else {}
    if sc.payback_hours > 0:
        fields["payback_hours"] = sc.payback_hours
        fields["power_price"] = round(sc.power_price, 1)
        fields["build_points"] = sorted((k, round(v)) for k, v in sc.build_points.items())
    if sc.overclock_last:
        fields["overclock_last"] = sc.overclock_shards
    if sc.row_overclock:
        fields["row_overclock"] = [sorted(sc.row_overclock.items()), sc.overclock_shards]
    if sc.excluded_pids:
        # Only when present, so a plan without bans keeps the id it was stored under.
        fields["excluded_pids"] = sorted(sc.excluded_pids)
    payload = json.dumps(
        {
            **fields,
            "objective": sc.objective,
            "target_item": sc.target_item,
            "exports": sorted(sc.exports),
            "export_minimums": dict(sorted(sc.export_minimums.items())),
            "only_free_nodes": only_free_nodes,
            "allow_sinks": sc.allow_sinks,
            "clocks": list(sc.clocks),
            "extractor_clocks": list(sc.extractor_clocks or ()),
            "machine_cost_mw": sc.machine_cost_mw,
            "recycle_once": sorted(sc.recycle_once),
            "supplied": dict(sorted(sc.raw_caps.items())),
            "sloop_budget": sc.sloop_budget,
            "belt_ipm": sc.belt_ipm,
            "pipe_m3min": sc.pipe_m3min,
            "grid_import_mw": sc.grid_import_mw,
            "extractors": sorted(
                f"{k[0]}|{k[1]}|{k[2]}={v}" for k, v in sc.extractor_nodes.items()
            ),
            "recipes": sorted(sc.recipes),
            "buildings": sorted(sc.buildings_available or ()),
        },
        sort_keys=True,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:8]


def _ban_processes(
    scenario: Scenario, patterns: list[str]
) -> tuple[Scenario, list[str], list[str]]:
    """Remove synthesised processes by name, and report which patterns hit nothing.

    A pattern matches the process label as printed, its building name, or an item it
    consumes, case-insensitively (docs/planning.md §8.2c).
    """
    game = scenario.game
    candidates = [p for p in build_processes(scenario) if p.kind in ("generator", "extractor")]
    banned: set[str] = set()
    labels: list[str] = []
    misses: list[str] = []
    for pattern in patterns:
        needle = pattern.strip().casefold()
        hits = [
            p
            for p in candidates
            if needle in p.label.casefold()
            or needle
            == (game.buildings[p.building].name.casefold() if p.building in game.buildings else "")
            or any(
                needle == game.item_name(item).casefold()
                for item, rate in p.rates.items()
                if rate < 0
            )
        ]
        if not hits:
            misses.append(pattern)
            continue
        banned.update(p.pid for p in hits)
        labels.extend(sorted({p.label for p in hits}))
    return replace(scenario, excluded_pids=frozenset(banned)), labels, misses


@dataclass
class ChainScenario:
    """A chain to solve on its own: every resource a free raw input, no extractors."""

    request: PlanRequest
    scenario: Scenario
    #: Every resource but the target, at ``UNLIMITED_RATE``.
    raw_caps: dict[str, float]
    #: The outlets as given, each resolved to an item id where one matches.
    outlets: tuple[str, ...]


def chain_scenario(
    game: GameData,
    state: WorldState,
    target: str,
    *,
    outlets: list[str] | tuple[str, ...] = (),
    allow_sinks: bool = True,
    exclude_recipes: list[str] | None = None,
    only_recipes: list[str] | None = None,
) -> ChainScenario:
    """The ``min_raw`` scenario ``bom`` and route comparison derive from: ``target`` and the
    outlets exported, and the bill the chain's rather than the mine's (docs/planning.md §8.10)."""
    request = build_scenario(
        game,
        state,
        objective="min_raw",
        exports=["MW"],
        exclude_recipes=exclude_recipes,
        only_recipes=only_recipes,
    )
    # Every resource needs a cap, since min_raw prices only resources with a raw column.
    raw_caps = {cls: UNLIMITED_RATE for cls, item in game.items.items() if item.is_resource}
    # A target that is itself a resource must be MADE, or it scores one per one from nothing.
    raw_caps.pop(target, None)
    outlet_ids = tuple(resolve_item(game, outlet) or outlet for outlet in outlets)
    scenario = replace(
        request.scenario,
        objective="min_raw",
        target_item=target,
        exports=(target, *(o for o in outlet_ids if o != target)),
        raw_caps=raw_caps,
        extractor_nodes={},
        allow_sinks=allow_sinks,
        grid_import_mw=UNLIMITED_RATE,
    )
    return ChainScenario(request=request, scenario=scenario, raw_caps=raw_caps, outlets=outlet_ids)


def with_recipes(scenario: Scenario, recipe_ids: list[str]) -> Scenario:
    """``scenario`` with ``recipe_ids`` allowed and each one's machine made buildable.

    An id already present is not added twice, which would collide two process columns.
    """
    if not recipe_ids:
        return scenario
    have = set(scenario.recipes)
    added = [rid for rid in recipe_ids if rid not in have]
    machines = {
        recipe.machine
        for recipe in (scenario.game.recipes.get(rid) for rid in recipe_ids)
        if recipe is not None and recipe.machine
    }
    return replace(
        scenario,
        recipes=[*scenario.recipes, *added],
        buildings_available=(scenario.buildings_available or set()) | machines,
    )

"""One construction path from tool arguments to a solvable Scenario.

plan_factory, plan_layout and diff_vs_save must all describe the SAME factory for a given
set of arguments, so the translation lives here once. It also mints the ``plan_id``, which
hashes the arguments TOGETHER WITH the save-derived solve inputs -- the unlocked recipe
set, the extractor node census, the buildable set -- so two responses carrying the same id
are provably about the same plan; that is what makes a stateless re-solve safe.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from dataclasses import replace as replace_scenario
from typing import TYPE_CHECKING

from ...core.gamedata.constants import WATER_EXTRACTOR_CAP_ASSUMED
from ...core.gamedata.model import GameData, Recipe
from ..spatial import nodes as nodes_mod
from ..spatial.select import Selection, select_nodes
from . import prices as prices_mod
from . import siting as siting_mod
from .optimize import MW, Scenario
from .planlog import is_power

if TYPE_CHECKING:  # pragma: no cover - import cycle only matters for type checkers
    from ..world.state import WorldState

__all__ = [
    "EXPORT_HELP",
    "PlanRequest",
    "build_scenario",
    "find_recipe",
    "match_recipes",
    "resolve_item",
    "select_for",
    "shard_stock",
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


def resolve_item(game: GameData, query: str) -> str | None:
    """Resolve a display name or class id to an item id."""
    if query in game.items:
        return query
    q = query.casefold()
    exact = [c for c, i in game.items.items() if i.name.casefold() == q]
    if exact:
        return exact[0]
    partial = [c for c, i in game.items.items() if q in i.name.casefold()]
    return partial[0] if partial else None


def _export_token(game: GameData, name: str) -> tuple[str | None, str | None]:
    """Resolve one export token to an item id, or say why it cannot be.

    Returns ``(id, None)`` or ``(None, error)``. ``planlog.is_power`` spellings mean the grid
    pseudo-item. An unresolvable token is NAMED rather than passed through: as an item id
    no process produces it would enter the LP as an unsatisfiable balance row and come back
    as a bare INFEASIBLE with nothing pointing at the typo.
    """
    if is_power(name):
        return MW, None
    resolved = resolve_item(game, name)
    if resolved is None:
        return None, f"no item matches {name!r}"
    return resolved, None


def match_recipes(game: GameData, pattern: str, pool: list[str]) -> list[str]:
    """Resolve one recipe pattern against a pool of recipe ids.

    Widening, in this order: exact class id, exact display name, then case-insensitive
    substring returning EVERY match. That is what makes "Recycled" drop both Recycled
    Plastic and Recycled Rubber in one go -- banning half a loop leaves the loop intact.
    """
    if pattern in pool:
        return [pattern]
    q = pattern.strip().casefold()
    exact = [rid for rid in pool if game.recipes[rid].name.casefold() == q]
    if exact:
        return exact
    return [rid for rid in pool if q in game.recipes[rid].name.casefold()]


def find_recipe(game: GameData, text: str) -> tuple[Recipe | None, list[str]]:
    """One recipe by class id or display name, and every id the text matched.

    The recipe is ``None`` when the text matched nothing or more than one recipe."""
    if text in game.recipes:
        return game.recipes[text], [text]
    hits = match_recipes(game, text, list(game.recipes))
    return (game.recipes[hits[0]] if len(hits) == 1 else None), hits


def select_for(game: GameData, state: WorldState, sources: list[str] | None) -> Selection:
    """Resolve a source spec against this world -- the ONE place that wiring lives.

    The world state goes in as ``st``, NOT as ``origin``: origin would also turn every
    direction selector into a cone from the player, so "north" would stop meaning the
    northern half of the map and start meaning "north of where I am standing".

    ``planning.provenance`` re-resolves through here too: a staleness check taking any
    other route would measure a field the plan does not plan over.
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
    #: In-scope nodes annotated with tapped/tapped_by/reachable. The diff joins its
    #: extractor rows against these, which is the one exact machine match available.
    node_rows: list[dict]
    plan_id: str
    #: Recipes removed by exclude_recipes, and patterns that matched nothing. A silently
    #: ignored ban would produce a plan using the very recipe the user forbade.
    excluded: list[str] = field(default_factory=list)
    recipe_errors: list[str] = field(default_factory=list)
    #: Export / export_minimum tokens that resolve to no item. The token is dropped so the
    #: scenario stays solvable, and the caller is told what was ignored.
    export_errors: list[str] = field(default_factory=list)
    #: Every in-scope node BEFORE the reachable/tapped filters, which is what makes "why
    #: can this plan not get Nitrogen Gas" answerable. `node_rows` cannot: an unreachable
    #: node is gone from the post-filter set precisely when it is the interesting one.
    scoped_nodes: list[dict] = field(default_factory=list)
    only_free_nodes: bool = False
    #: Recipe ids ``required`` put in force, after the refusals (contract §6).
    required: list[str] = field(default_factory=list)
    #: Where this plan STANDS, resolved. Deliberately absent from ``plan_id``: nothing here
    #: enters the LP, so hashing it would give one plan two ids depending only on whether
    #: the caller had said where it goes.
    site: siting_mod.Siting | None = None
    #: A ``site_at`` that would not resolve. Reported, never raised: a bad coordinate must
    #: not take down a plan whose numbers do not depend on one.
    site_errors: list[str] = field(default_factory=list)
    #: How the horizon, price and overclock switch were resolved: ``inherited``,
    #: ``default_hours``, ``price_source``, ``mix``, ``overclock_inherited``, ``shards``.
    payback: dict = field(default_factory=dict)


def _inherits(value) -> bool:
    return value is None or value == "default"


def _shared() -> dict:
    from .. import settings

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


def _payback(state, hours, overclock, price, rows: dict) -> tuple[dict, dict]:
    """Scenario fields for the horizon, and how each was resolved (contract §6)."""
    shared = _shared()
    resolved_hours = float(shared["payback_hours"] if _inherits(hours) else hours)
    on = bool(shared["overclock_last"] if _inherits(overclock) else overclock)
    found = prices_mod.prices_for(state, bool(shared["biomass"]))
    stock = shard_stock(state) if on or "last" in rows.values() else None
    fields = {
        "payback_hours": resolved_hours,
        "power_price": found.price if _inherits(price) else float(price),
        "build_points": found.points,
        "overclock_last": on,
        "overclock_shards": stock["free"] + stock["craftable"] if stock else None,
        "row_overclock": rows,
    }
    info = {
        "inherited": _inherits(hours),
        "default_hours": float(shared["payback_hours"]),
        "price_source": "grid mix" if _inherits(price) else "plan",
        "mix": found.mix,
        "overclock_inherited": _inherits(overclock),
    }
    return fields, info


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
    #: None means "the fastest tier this save can build". Getting a tier wrong is silent:
    #: every belt and pipe count is off by a factor and nothing says so.
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
    #: Where the factory will stand, in any spelling ``spatial.origin`` takes. It buys the
    #: plan a MEASURED water assumption instead of an assumed one; it changes no number the
    #: LP sees, because how much water a site yields is placement geometry no data here has.
    site_at: str = "",
    site_footprint: str = "",
) -> PlanRequest:
    """Translate tool arguments into a Scenario, its node scope and a plan id.

    ``exports`` REPLACES the default ``[MW]`` rather than extending it, which is
    load-bearing: `grid_import_mw` below is derived from the export set, so auto-appending
    MW would force every item plan to be self-powered.
    """
    export_ids: list[str] = []
    export_errors: list[str] = []
    for name in exports or [MW]:
        resolved, err = _export_token(game, name)
        if resolved is None:
            export_errors.append(f"exports: {err}")
            continue
        export_ids.append(resolved)
    minimums = {}
    for name, value in (export_minimums or {}).items():
        # Same resolution as exports, aliases included: a minimum keyed "MW" that does not
        # match the power pseudo-item is a floor the LP silently ignores.
        resolved, err = _export_token(game, name)
        if resolved is None:
            export_errors.append(f"export_minimums: {err}")
            continue
        minimums[resolved] = float(value)

    # The Mk5/Mk2 fallbacks are for a caller with no save to read at all.
    if belt_ipm is None:
        best = state.best_belt()
        belt_ipm = best[1] if best else 780.0
    if pipe_m3min is None:
        best = state.best_pipe()
        pipe_m3min = best[1] if best else 600.0

    # Items another plan hands this one, as a free input up to a rate: what makes a plant
    # solvable in PIECES. The cost of producing them is charged in the plan that does and
    # NOT here, which is correct for a module and wrong for a whole-plant comparison --
    # `advisor` warns against feeding a basket this way for a baseline.
    raw_caps: dict[str, float] = {}
    for name, rate in (supplied or {}).items():
        resolved, err = _export_token(game, name)
        if resolved is None or resolved == MW:
            export_errors.append(f"supplied: {err or 'MW cannot be supplied as an item'}")
            continue
        # A hair of slack, because a rate read out of ANOTHER solve is rounded to 4dp on
        # the way out: 2299.9998 Polymer Resin against a demand for exactly 2300 is
        # INFEASIBLE for two ten-thousandths. 1e-6 relative is far below anything physical.
        rate = float(rate)
        raw_caps[resolved] = rate + max(1e-6, abs(rate) * 1e-6)

    sel = select_for(game, state, sources)
    scoped = nodes_mod.annotate(sel.nodes, game, state.projection, state.unlocked_building_ids)
    rows = [r for r in scoped if r["reachable"]]
    if only_free_nodes:
        rows = [r for r in rows if not r["tapped"]]

    ext: dict[tuple[str, str, str], int] = {}
    for r in rows:
        if r["kind"] != "node" or r["rate"] <= 0:
            continue
        for cls in _EXTRACTOR_PREFERENCE:
            b = game.buildings.get(cls)
            if b is None or cls not in state.unlocked_building_ids:
                continue
            if b.allowed_resources and r["resource"] not in b.allowed_resources:
                continue
            if not b.allowed_resources and game.items[r["resource"]].is_fluid:
                continue
            key = (cls, r["resource"], r["purity"])
            ext[key] = ext.get(key, 0) + 1
            break
    if "Build_WaterPump_C" in state.unlocked_building_ids:
        # Water has no nodes to count, so this is an ASSUMPTION standing in for a site the
        # model cannot see; a caller who has measured theirs should override it.
        # `is None`, NOT falsiness: zero means "this site has no water at all", which is
        # exactly the question an inland plan asks, and `or`-defaulting turns that answer
        # into the 200-pump assumption.
        cap = WATER_EXTRACTOR_CAP_ASSUMED if water_extractors is None else int(water_extractors)
        if cap > 0:
            ext[("Build_WaterPump_C", "Desc_Water_C", "normal")] = cap

    recipes = [r.cls for r in state.unlocked_recipes("part")]
    #: Kept for the miss check: a pattern that banned a recipe is not a miss, even though
    #: `recipes` no longer contains it by the time processes are matched.
    all_recipes = list(recipes)
    excluded: list[str] = []
    recipe_errors: list[str] = []

    if only_recipes:
        keep: set[str] = set()
        for pattern in only_recipes:
            hits = match_recipes(game, pattern, recipes)
            if not hits:
                recipe_errors.append(f"only_recipes: nothing matches {pattern!r}")
            keep.update(hits)
        if keep:
            recipes = [rid for rid in recipes if rid in keep]

    # EVERY pattern is offered to recipes AND to the synthesised processes, never to the
    # first that matches: generator burn and extraction come from building data rather
    # than Docs.json and so have no recipe to hit, and "Coal" matches Biocoal/Charcoal, so
    # recipe-first precedence would make "do not burn coal here" ban the opposite. What is
    # banned is listed back, so an over-broad pattern is visible.
    pending_process_bans: list[str] = []
    for pattern in exclude_recipes or []:
        hits = match_recipes(game, pattern, recipes)
        pending_process_bans.append(pattern)
        if not hits:
            continue
        excluded.extend(game.recipes[rid].name for rid in hits)
        banned = set(hits)
        recipes = [rid for rid in recipes if rid not in banned]

    in_force = _required(game, required, all_recipes, exclude_recipes, recipe_errors)
    if in_force:
        makes = {_main_product(game, rid) for rid in in_force}
        recipes = [
            rid for rid in recipes if rid in in_force or _main_product(game, rid) not in makes
        ]
        recipes += [rid for rid in in_force if rid not in recipes]

    buildings = state.unlocked_building_ids
    power, payback = _payback(
        state, payback_hours, overclock_last, power_price, dict(row_overclock or {})
    )
    sc = Scenario(
        game=game,
        recipes=recipes,
        objective=objective,
        target_item=resolve_item(game, target_item) if target_item else None,
        exports=tuple(export_ids),
        export_minimums=minimums,
        extractor_nodes=ext,
        raw_caps=raw_caps,
        allow_sinks=allow_sinks,
        clocks=tuple(clocks) if clocks else (1.0,),
        extractor_clocks=tuple(extractor_clocks) if extractor_clocks else None,
        machine_cost_mw=machine_cost_mw,
        belt_ipm=belt_ipm,
        pipe_m3min=pipe_m3min,
        buildings_available=buildings,
        # Zero is "spend none", not "unlimited". A fixed number of somersloops exist on
        # the whole map, so a plan that assumed them would be unbuildable; opting in also
        # keeps the column count down, since offering every sloop count doubles the matrix.
        sloop_budget=max(0, int(sloops or 0)),
        # Without this the power row forces generation == consumption. Ignored when MW
        # is exported, since a power plant that imports power to export it is unbounded.
        grid_import_mw=None if MW in export_ids else 1e6,
        **power,
    )

    if recycle_once:
        # Widened over the process label as well as the recipe name, as exclude_recipes is.
        from .optimize import build_processes as _procs

        wanted: set[str] = set()
        for pattern in recycle_once:
            needle = pattern.strip().casefold()
            for proc in _procs(sc):
                if needle in proc.label.casefold() or (
                    proc.recipe in game.recipes
                    and needle in game.recipes[proc.recipe].name.casefold()
                ):
                    wanted.add(proc.pid)
            if not any(needle in p.label.casefold() for p in _procs(sc)):
                recipe_errors.append(f"recycle_once: nothing matches {pattern!r}")
        sc = replace_scenario(sc, recycle_once=frozenset(wanted))

    if pending_process_bans:
        sc, process_hits, misses = _ban_processes(sc, pending_process_bans)
        excluded.extend(process_hits)
        matched_a_recipe = {
            pattern for pattern in pending_process_bans if match_recipes(game, pattern, all_recipes)
        }
        for pattern in [m for m in misses if m not in matched_a_recipe]:
            recipe_errors.append(f"exclude_recipes: nothing matches {pattern!r}")

    site = None
    site_errors: list[str] = []
    if str(site_at or "").strip():
        try:
            site = siting_mod.resolve_plan_site(state, site_at, site_footprint)
        except ValueError as exc:
            site_errors.append(f"site_at: {exc}")

    return PlanRequest(
        scenario=sc,
        selection=sel,
        node_rows=rows,
        plan_id=_plan_id(sc, only_free_nodes, in_force),
        excluded=sorted(set(excluded)),
        recipe_errors=recipe_errors,
        export_errors=export_errors,
        scoped_nodes=scoped,
        only_free_nodes=only_free_nodes,
        site=site,
        site_errors=site_errors,
        required=in_force,
        payback=payback,
    )


def _main_product(game: GameData, rid: str) -> str | None:
    products = game.recipes[rid].products
    return products[0].item if products else None


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


def _ban_processes(sc: Scenario, patterns: list[str]) -> tuple[Scenario, list[str], list[str]]:
    """Remove synthesised processes by name, and report which patterns hit nothing.

    Matched case-insensitively against the process LABEL exactly as the build table
    prints it ("Coal-Powered Generator on Coal"), its building name, and the item it
    consumes -- so "Coal-Powered Generator", "coal-powered generator on coal" and
    "Coal" all work, the last banning every generator that burns it.
    """
    from dataclasses import replace

    from .optimize import build_processes

    candidates = [p for p in build_processes(sc) if p.kind in ("generator", "extractor")]
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
            == (
                sc.game.buildings[p.building].name.casefold()
                if p.building in sc.game.buildings
                else ""
            )
            or any(
                needle == sc.game.item_name(item).casefold()
                for item, rate in p.rates.items()
                if rate < 0
            )
        ]
        if not hits:
            misses.append(pattern)
            continue
        banned.update(p.pid for p in hits)
        labels.extend(sorted({p.label for p in hits}))
    return replace(sc, excluded_pids=frozenset(banned)), labels, misses
